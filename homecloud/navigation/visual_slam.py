"""Offline monocular visual-odometry feasibility tools.

This module intentionally does not claim metric SLAM.  A monocular camera can
recover rotation and translation direction, but it needs another scale source
(wheel odometry, IMU constraints, a measured route, or a metric depth sensor)
before the trajectory can be expressed reliably in metres.
"""

from __future__ import annotations

import csv
import json
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class Calibration:
    K: np.ndarray
    D: np.ndarray
    source: str
    reference_size: tuple[int, int] = (640, 480)

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        with path.open("rb") as handle:
            data = pickle.load(handle)
        if not isinstance(data, dict) or "K" not in data or "D" not in data:
            raise ValueError("校正檔必須包含 K 與 D")
        K = np.asarray(data["K"], dtype=np.float64)
        D = np.asarray(data["D"], dtype=np.float64).reshape(-1, 1)
        if K.shape != (3, 3) or D.size != 4 or not np.isfinite(K).all() or not np.isfinite(D).all():
            raise ValueError("目前只接受有效的 OpenCV fisheye K(3x3) 與 D(4)")
        return cls(K=K, D=D, source=str(path.resolve()))

    def rectifier(self, size: tuple[int, int]):
        width, height = size
        ref_width, ref_height = self.reference_size
        scaled = self.K.copy()
        scaled[0, 0] *= width / ref_width
        scaled[0, 2] *= width / ref_width
        scaled[1, 1] *= height / ref_height
        scaled[1, 2] *= height / ref_height
        new_K = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
            scaled, self.D, size, np.eye(3), balance=0.0
        )
        map1, map2 = cv2.fisheye.initUndistortRectifyMap(
            scaled, self.D, np.eye(3), new_K, size, cv2.CV_16SC2
        )
        return new_K, map1, map2


class RelativeDepthMasker:
    """Depth Anything V2 percentile mask; never returns metric distance."""

    def __init__(self, model_path: Path, input_size: int = 322, exclude_near_percent: float = 18.0):
        if not 0 <= exclude_near_percent < 50:
            raise ValueError("exclude_near_percent 必須介於 0 到 50")
        import torch
        from eldertest.depth_anything_v2.dpt import DepthAnythingV2

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = DepthAnythingV2(
            encoder="vits", features=64, out_channels=[48, 96, 192, 384]
        )
        state = torch.load(str(model_path), map_location=self.device, weights_only=False)
        self.model.load_state_dict(state)
        self.model.to(self.device).eval()
        self.input_size = input_size
        self.exclude_near_percent = exclude_near_percent

    def mask(self, frame: np.ndarray) -> np.ndarray:
        import torch

        with torch.no_grad():
            raw = self.model.infer_image(frame, input_size=self.input_size)
        raw = cv2.resize(np.asarray(raw, dtype=np.float32), (frame.shape[1], frame.shape[0]))
        # Depth Anything V2 relative output is used only by within-frame rank.
        # Large values are treated as near.  No value is converted to metres.
        cutoff = np.percentile(raw, 100.0 - self.exclude_near_percent)
        keep = (raw <= cutoff).astype(np.uint8) * 255
        keep = cv2.morphologyEx(keep, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        return keep


def _spread(points: np.ndarray, width: int, height: int) -> float:
    if len(points) < 2:
        return 0.0
    x_span = float(np.ptp(points[:, 0])) / max(width, 1)
    y_span = float(np.ptp(points[:, 1])) / max(height, 1)
    return min(x_span, y_span)


def place_match(kp_a, desc_a, kp_b, desc_b) -> dict:
    result = {"matches": 0, "inliers": 0, "inlier_ratio": 0.0}
    if desc_a is None or desc_b is None or len(desc_a) < 8 or len(desc_b) < 8:
        return result
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(desc_a, desc_b, k=2)
    good = [m for pair in pairs if len(pair) == 2 for m, n in [pair] if m.distance < 0.72 * n.distance]
    result["matches"] = len(good)
    if len(good) < 8:
        return result
    src = np.float32([kp_a[m.queryIdx].pt for m in good])
    dst = np.float32([kp_b[m.trainIdx].pt for m in good])
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if mask is not None:
        result["inliers"] = int(mask.sum())
        result["inlier_ratio"] = result["inliers"] / len(good)
    return result


class MonocularTracker:
    def __init__(self, K: np.ndarray, size: tuple[int, int], feature_count: int = 1800):
        self.K = np.asarray(K, dtype=np.float64)
        self.width, self.height = size
        self.orb = cv2.ORB_create(
            nfeatures=feature_count, scaleFactor=1.2, nlevels=8,
            edgeThreshold=19, fastThreshold=12,
        )
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self.previous = None
        self.initial = None
        self.keyframes = []
        self.R_world_camera = np.eye(3, dtype=np.float64)
        self.position = np.zeros(3, dtype=np.float64)
        self.relative_distance = 0.0
        self.accepted_steps = 0
        self.consecutive_failures = 0
        self.loss_events = 0
        self._in_loss = False

    def _features(self, gray: np.ndarray, mask: Optional[np.ndarray]):
        keypoints, descriptors = self.orb.detectAndCompute(gray, mask)
        return keypoints or [], descriptors

    def process(self, gray: np.ndarray, frame_index: int, timestamp_s: float,
                mask: Optional[np.ndarray] = None) -> dict:
        keypoints, descriptors = self._features(gray, mask)
        base = {
            "frame": frame_index, "time_s": round(timestamp_s, 4),
            "features": len(keypoints), "matches": 0, "inliers": 0,
            "inlier_ratio": 0.0, "median_parallax_px": 0.0,
            "spread": 0.0, "state": "initializing", "reason": "",
            "x": float(self.position[0]), "y": float(self.position[2]),
            "yaw_deg": math.degrees(math.atan2(self.R_world_camera[0, 2], self.R_world_camera[2, 2])),
            "place_keyframe": None, "place_inliers": 0,
        }
        current = (keypoints, descriptors)
        if self.previous is None:
            self.previous = current
            self.initial = current
            self.keyframes.append((frame_index, keypoints, descriptors, self.position.copy()))
            base["state"] = "initialized" if descriptors is not None else "rejected"
            base["reason"] = "first_frame" if descriptors is not None else "insufficient_features"
            return base

        prev_kp, prev_desc = self.previous
        self.previous = current
        if prev_desc is None or descriptors is None or len(prev_desc) < 8 or len(descriptors) < 8:
            return self._reject(base, "insufficient_features", current)

        pairs = self.matcher.knnMatch(prev_desc, descriptors, k=2)
        good = [m for pair in pairs if len(pair) == 2 for m, n in [pair] if m.distance < 0.75 * n.distance]
        base["matches"] = len(good)
        if len(good) < 20:
            return self._reject(base, "insufficient_matches", current)

        previous_points = np.float32([prev_kp[m.queryIdx].pt for m in good])
        current_points = np.float32([keypoints[m.trainIdx].pt for m in good])
        flow = np.linalg.norm(current_points - previous_points, axis=1)
        base["median_parallax_px"] = float(np.median(flow))
        if base["median_parallax_px"] < 0.65:
            base["state"] = "stationary"
            base["reason"] = "parallax_below_threshold"
            self.consecutive_failures = 0
            self._in_loss = False
            return base

        essential, mask_e = cv2.findEssentialMat(
            previous_points, current_points, self.K,
            method=cv2.RANSAC, prob=0.999, threshold=1.5,
        )
        if essential is None or mask_e is None:
            return self._reject(base, "essential_matrix_failed", current)
        inlier_selector = mask_e.ravel().astype(bool)
        inlier_prev = previous_points[inlier_selector]
        inlier_curr = current_points[inlier_selector]
        base["inliers"] = len(inlier_prev)
        base["inlier_ratio"] = base["inliers"] / len(good)
        base["spread"] = _spread(inlier_curr, self.width, self.height)
        if base["inliers"] < 18 or base["inlier_ratio"] < 0.32:
            return self._reject(base, "weak_geometry", current)
        if base["spread"] < 0.12:
            return self._reject(base, "features_too_concentrated", current)

        _, rotation, translation, pose_mask = cv2.recoverPose(
            essential, inlier_prev, inlier_curr, self.K
        )
        pose_inliers = int(pose_mask.astype(bool).sum())
        if pose_inliers < 12:
            return self._reject(base, "pose_recovery_failed", current)

        delta_camera = -rotation.T @ translation.reshape(3)
        norm = float(np.linalg.norm(delta_camera))
        if not math.isfinite(norm) or norm < 1e-9:
            return self._reject(base, "invalid_translation", current)
        delta_camera /= norm
        self.position += self.R_world_camera @ delta_camera
        self.R_world_camera = self.R_world_camera @ rotation.T
        self.relative_distance += 1.0
        self.accepted_steps += 1
        self.consecutive_failures = 0
        self._in_loss = False
        base.update({
            "state": "tracked", "reason": "motion_accepted",
            "x": float(self.position[0]), "y": float(self.position[2]),
            "yaw_deg": math.degrees(math.atan2(self.R_world_camera[0, 2], self.R_world_camera[2, 2])),
        })
        if self.accepted_steps % 30 == 0:
            self.keyframes.append((frame_index, keypoints, descriptors, self.position.copy()))
        return base

    def _reject(self, row: dict, reason: str, current) -> dict:
        self.consecutive_failures += 1
        row["state"] = "rejected"
        row["reason"] = reason
        if self.consecutive_failures >= 5 and not self._in_loss:
            self.loss_events += 1
            self._in_loss = True
        if self._in_loss and current[1] is not None:
            best = (None, 0)
            for frame_index, kp, desc, _ in self.keyframes[-8:]:
                match = place_match(kp, desc, current[0], current[1])
                if match["inliers"] > best[1]:
                    best = (frame_index, match["inliers"])
            if best[1] >= 24:
                row["place_keyframe"] = best[0]
                row["place_inliers"] = best[1]
                row["reason"] += ";place_recognized_only"
        return row

    def final_loop_check(self, minimum_motion_steps: int = 20) -> dict:
        if not self.initial or not self.previous:
            return {"candidate": False, "reason": "no_frames", "matches": 0, "inliers": 0}
        matched = place_match(self.initial[0], self.initial[1], self.previous[0], self.previous[1])
        enough_motion = self.accepted_steps >= minimum_motion_steps
        candidate = enough_motion and matched["inliers"] >= 24 and matched["inlier_ratio"] >= 0.35
        reason = "visual_return_candidate" if candidate else (
            "not_enough_camera_motion" if not enough_motion else "start_end_not_visually_consistent"
        )
        return {"candidate": candidate, "reason": reason, **matched}


def _draw_trajectory(rows: list[dict], output: Path, scale: float, units: str):
    positions = np.array([[r["x"] * scale, r["y"] * scale] for r in rows], dtype=np.float64)
    canvas = np.full((720, 900, 3), 248, np.uint8)
    cv2.putText(canvas, f"Monocular trajectory ({units})", (30, 45),
                cv2.FONT_HERSHEY_SIMPLEX, 0.85, (25, 55, 65), 2, cv2.LINE_AA)
    if len(positions):
        low = positions.min(axis=0)
        high = positions.max(axis=0)
        span = np.maximum(high - low, 1e-6)
        plot_scale = min(780 / span[0], 590 / span[1])
        pixels = np.empty_like(positions)
        pixels[:, 0] = 60 + (positions[:, 0] - low[0]) * plot_scale
        pixels[:, 1] = 660 - (positions[:, 1] - low[1]) * plot_scale
        for a, b in zip(pixels[:-1], pixels[1:]):
            cv2.line(canvas, tuple(a.astype(int)), tuple(b.astype(int)), (210, 105, 35), 2)
        cv2.circle(canvas, tuple(pixels[0].astype(int)), 7, (45, 165, 65), -1)
        cv2.circle(canvas, tuple(pixels[-1].astype(int)), 7, (40, 55, 210), -1)
        cv2.putText(canvas, "START", tuple((pixels[0] + [9, -7]).astype(int)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (45, 125, 45), 1, cv2.LINE_AA)
        cv2.putText(canvas, "END", tuple((pixels[-1] + [9, -7]).astype(int)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 55, 180), 1, cv2.LINE_AA)
    cv2.imwrite(str(output), canvas)


def _write_html(output: Path, report: dict):
    q = report["quality"]
    loop = report["loop_check"]
    scale_note = "近似公尺（依已知總路線長度整體縮放）" if report["scale"]["metric"] else "相對單位（不可當作公尺）"
    html = f"""<!doctype html><meta charset="utf-8"><title>單目視覺定位驗證</title>
<style>body{{font-family:system-ui;max-width:1100px;margin:28px auto;color:#17323b;background:#eef4f4}}.card{{background:white;padding:20px;margin:14px 0;border-radius:12px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:9px;border-bottom:1px solid #d9e3e5;text-align:left}}.warn{{color:#9a4d00;font-weight:700}}img,video{{max-width:100%;border-radius:8px}}</style>
<h1>單目視覺定位 B 版驗證</h1>
<div class="card"><p><b>來源：</b>{report['source']}</p><p><b>尺度：</b>{scale_note}</p>
<p class="warn">本結果是離線單目視覺里程計與地點辨識驗證，不是已完成的實車公尺尺度 SLAM。</p></div>
<div class="card"><h2>追蹤結果</h2><table>
<tr><th>分析影格</th><td>{q['frames_analyzed']}</td></tr><tr><th>接受移動</th><td>{q['tracked_frames']}</td></tr>
<tr><th>靜止影格</th><td>{q['stationary_frames']}</td></tr><tr><th>拒絕影格</th><td>{q['rejected_frames']}</td></tr>
<tr><th>跳過重複影格</th><td>{q['duplicate_frames_skipped']}</td></tr><tr><th>追蹤中斷事件</th><td>{q['tracking_loss_events']}</td></tr>
<tr><th>移動畫面追蹤成功率</th><td>{q['motion_tracking_success_rate']:.1%}</td></tr>
<tr><th>起終點地點辨識</th><td>{'候選迴環' if loop['candidate'] else '未確認'}（{loop['inliers']} inliers）</td></tr></table></div>
<div class="card"><h2>相對軌跡</h2><img src="trajectory.png"></div>
<div class="card"><h2>疊圖影片</h2><video src="annotated.mp4" controls></video></div>
<div class="card"><h2>判讀</h2><p>{report['assessment']}</p></div>"""
    output.write_text(html, encoding="utf-8")


def analyze_video(source: Path, output_dir: Path, calibration_path: Optional[Path] = None,
                  frame_step: int = 2, max_frames: int = 0,
                  route_length_m: Optional[float] = None,
                  depth_masker: Optional[RelativeDepthMasker] = None,
                  exclude_regions: tuple[tuple[float, float, float, float], ...] = (),
                  duplicate_threshold: float = 0.0,
                  minimum_motion_steps: int = 20) -> dict:
    if frame_step < 1:
        raise ValueError("frame_step 至少為 1")
    if route_length_m is not None and route_length_m <= 0:
        raise ValueError("route_length_m 必須大於 0")
    if duplicate_threshold < 0:
        raise ValueError("duplicate_threshold 不可為負")
    if minimum_motion_steps < 1:
        raise ValueError("minimum_motion_steps 至少為 1")
    for region in exclude_regions:
        if len(region) != 4 or not all(0 <= value <= 1 for value in region):
            raise ValueError("exclude_regions 必須使用 0 到 1 的 x1,y1,x2,y2")
        if region[0] >= region[2] or region[1] >= region[3]:
            raise ValueError("exclude_regions 的右下角必須大於左上角")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"無法開啟影片：{source}")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    output_dir.mkdir(parents=True, exist_ok=True)

    calibration = Calibration.load(calibration_path) if calibration_path else None
    if calibration:
        K, map1, map2 = calibration.rectifier((width, height))
        calibration_source = calibration.source
    else:
        focal = 0.8 * max(width, height)
        K = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=np.float64)
        map1 = map2 = None
        calibration_source = "estimated_intrinsics_only"

    tracker = MonocularTracker(K, (width, height))
    writer = cv2.VideoWriter(
        str(output_dir / "annotated.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
        max(1.0, fps / frame_step), (width, height),
    )
    rows = []
    source_frame = 0
    analyzed = 0
    duplicates_skipped = 0
    previous_signature = None
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if source_frame % frame_step:
            source_frame += 1
            continue
        if map1 is not None:
            frame = cv2.remap(frame, map1, map2, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        signature = cv2.resize(gray, (96, 72), interpolation=cv2.INTER_AREA)
        if previous_signature is not None and duplicate_threshold > 0:
            difference = float(np.mean(cv2.absdiff(signature, previous_signature)))
            if difference <= duplicate_threshold:
                duplicates_skipped += 1
                source_frame += 1
                continue
        previous_signature = signature
        feature_mask = depth_masker.mask(frame) if depth_masker else np.full((height, width), 255, np.uint8)
        for x1, y1, x2, y2 in exclude_regions:
            cv2.rectangle(
                feature_mask, (round(x1 * width), round(y1 * height)),
                (round(x2 * width), round(y2 * height)), 0, -1,
            )
        row = tracker.process(gray, source_frame, source_frame / fps, feature_mask)
        rows.append(row)
        analyzed += 1
        color = (55, 190, 70) if row["state"] == "tracked" else ((35, 180, 220) if row["state"] == "stationary" else (40, 60, 220))
        cv2.rectangle(frame, (0, 0), (width, 82), (16, 34, 39), -1)
        cv2.putText(frame, f"{row['state']}  features={row['features']} matches={row['matches']} inliers={row['inliers']}",
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
        cv2.putText(frame, f"relative pose x={row['x']:.2f} z={row['y']:.2f} yaw={row['yaw_deg']:.1f} deg",
                    (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (235, 240, 240), 1, cv2.LINE_AA)
        cv2.putText(frame, "NON-METRIC OFFLINE VO", (width - 230, height - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (30, 190, 245), 1, cv2.LINE_AA)
        for x1, y1, x2, y2 in exclude_regions:
            cv2.rectangle(
                frame, (round(x1 * width), round(y1 * height)),
                (round(x2 * width), round(y2 * height)), (85, 85, 85), 1,
            )
        writer.write(frame)
        source_frame += 1
        if max_frames and analyzed >= max_frames:
            break
    capture.release()
    writer.release()
    if not rows:
        raise RuntimeError("影片沒有可分析影格")

    loop = tracker.final_loop_check(minimum_motion_steps)
    tracked = sum(r["state"] == "tracked" for r in rows)
    stationary = sum(r["state"] == "stationary" for r in rows)
    rejected = sum(r["state"] == "rejected" for r in rows)
    scale = 1.0
    if route_length_m is not None and tracker.relative_distance > 0:
        scale = route_length_m / tracker.relative_distance
    quality = {
        "source_frames_read": source_frame, "duplicate_frames_skipped": duplicates_skipped,
        "frames_analyzed": len(rows), "tracked_frames": tracked,
        "stationary_frames": stationary, "rejected_frames": rejected,
        "tracking_loss_events": tracker.loss_events,
        "tracking_accept_rate": tracked / max(1, len(rows) - 1),
        "motion_tracking_success_rate": tracked / max(1, tracked + rejected),
        "median_inliers_on_tracked": float(np.median([r["inliers"] for r in rows if r["state"] == "tracked"])) if tracked else 0.0,
    }
    if tracked < minimum_motion_steps:
        assessment = "有效相機移動不足，這段影片不適合判定視覺 SLAM；需要鏡頭在房間內移動並回到起點。"
    elif quality["motion_tracking_success_rate"] < 0.65:
        assessment = "有相機移動，但追蹤率偏低；請改善光線、移動速度、動態人物遮擋或重新確認鏡頭校正。"
    elif loop["candidate"]:
        assessment = "已取得可用相對軌跡及起終點候選迴環；仍需已知尺度或實車里程計才能建立公尺地圖。"
    else:
        assessment = "已取得部分相對軌跡，但尚未證明回環；需要錄製回到相同起點與朝向的完整繞行影片。"

    report = {
        "version": "visual_slam_b_v1", "source": str(source.resolve()),
        "claim": "offline_monocular_visual_odometry_feasibility",
        "full_slam_completed": False, "metric_map_completed": False,
        "calibration": {"source": calibration_source, "video_size": [width, height], "fisheye_rectified": calibration is not None},
        "depth_assistance": {
            "enabled": depth_masker is not None, "purpose": "relative_near_feature_filter_only",
            "metric": False,
        },
        "feature_exclusions": [list(region) for region in exclude_regions],
        "sampling": {
            "frame_step": frame_step, "duplicate_threshold": duplicate_threshold,
            "minimum_motion_steps_for_loop": minimum_motion_steps,
        },
        "scale": {
            "metric": route_length_m is not None and tracker.relative_distance > 0,
            "units": "approx_meters" if route_length_m is not None and tracker.relative_distance > 0 else "relative_step_units",
            "factor": scale, "known_route_length_m": route_length_m,
        },
        "quality": quality, "loop_check": loop, "assessment": assessment,
        "limitations": [
            "No wheel/IMU scale fusion", "No globally optimized pose graph",
            "Place recognition does not reposition the camera", "Offline video does not validate live robot safety",
        ],
    }
    with (output_dir / "trace.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer_csv = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer_csv.writeheader()
        writer_csv.writerows(rows)
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _draw_trajectory(rows, output_dir / "trajectory.png", scale, report["scale"]["units"])
    _write_html(output_dir / "report.html", report)
    return report
