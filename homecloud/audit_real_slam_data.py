"""Audit synchronized camera and Ranger telemetry datasets for SLAM readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

import cv2
import numpy as np


REQUIRED_FILES = (
    "camera.mp4",
    "frames.jsonl",
    "telemetry.jsonl",
    "telemetry_errors.jsonl",
    "manifest.json",
)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} 不是有效 JSON：{exc}") from exc
    return rows


def monotonic(rows: list[dict], key: str = "host_time_s") -> bool:
    values = [float(row[key]) for row in rows if key in row]
    return len(values) == len(rows) and all(b >= a for a, b in zip(values, values[1:]))


def video_metadata(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"opened": False, "decoded_frames": 0}
    declared_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    declared_fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    decoded_frames = 0
    while True:
        ok, _ = capture.read()
        if not ok:
            break
        decoded_frames += 1
    capture.release()
    return {
        "opened": True,
        "declared_frames": declared_frames,
        "decoded_frames": decoded_frames,
        "declared_fps": round(declared_fps, 3),
        "playback_duration_s": round(decoded_frames / declared_fps, 3) if declared_fps else None,
        "size": [width, height],
    }


def visual_return_check(path: Path) -> dict:
    """Check whether the final view is geometrically consistent with the first view."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"candidate": False, "matches": 0, "inliers": 0, "inlier_ratio": 0.0}
    ok, first = capture.read()
    last = None
    while True:
        frame_ok, frame = capture.read()
        if not frame_ok:
            break
        last = frame
    capture.release()
    if not ok or last is None:
        return {"candidate": False, "matches": 0, "inliers": 0, "inlier_ratio": 0.0}

    orb = cv2.ORB_create(nfeatures=1800)
    keypoints_a, descriptors_a = orb.detectAndCompute(cv2.cvtColor(first, cv2.COLOR_BGR2GRAY), None)
    keypoints_b, descriptors_b = orb.detectAndCompute(cv2.cvtColor(last, cv2.COLOR_BGR2GRAY), None)
    if descriptors_a is None or descriptors_b is None:
        return {"candidate": False, "matches": 0, "inliers": 0, "inlier_ratio": 0.0}
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(descriptors_a, descriptors_b, k=2)
    good = [a for a, b in pairs if a.distance < 0.75 * b.distance]
    if len(good) < 8:
        return {"candidate": False, "matches": len(good), "inliers": 0, "inlier_ratio": 0.0}
    source = np.float32([keypoints_a[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
    target = np.float32([keypoints_b[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(source, target, cv2.RANSAC, 4.0)
    inliers = int(mask.sum()) if mask is not None else 0
    ratio = inliers / len(good) if good else 0.0
    return {
        "candidate": inliers >= 24 and ratio >= 0.35,
        "matches": len(good),
        "inliers": inliers,
        "inlier_ratio": round(ratio, 4),
    }


def create_contact_sheet(video_path: Path, output_path: Path, columns: int = 4) -> None:
    """Save uniformly sampled frames so the route can be reviewed at a glance."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_count = 12
    indexes = np.linspace(0, max(frame_count - 1, 0), sample_count, dtype=int)
    tiles = []
    for number, index in enumerate(indexes):
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
        ok, frame = capture.read()
        if not ok:
            continue
        frame = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
        cv2.putText(
            frame,
            f"{number + 1}/{sample_count} frame={index}",
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tiles.append(frame)
    capture.release()
    if not tiles:
        return
    blank = np.zeros_like(tiles[0])
    while len(tiles) % columns:
        tiles.append(blank.copy())
    rows = [np.hstack(tiles[index:index + columns]) for index in range(0, len(tiles), columns)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), np.vstack(rows))


def motion_expectation(session: str, left_delta: int, right_delta: int) -> tuple[bool, str]:
    threshold = 300
    if session.startswith("straight"):
        valid = abs(left_delta) >= threshold and abs(right_delta) >= threshold and left_delta * right_delta < 0
        return valid, "直線應有足夠脈衝，且兩側原始編碼器符號相反"
    if session.startswith("turn_left") or session.startswith("turn_right"):
        valid = abs(left_delta) >= threshold and abs(right_delta) >= threshold and left_delta * right_delta > 0
        return valid, "原地轉向應有足夠脈衝，且兩側原始編碼器符號相同"
    if session.startswith("room_loop") or session.startswith("obstacle_route"):
        valid = abs(left_delta) >= threshold or abs(right_delta) >= threshold
        return valid, "路線錄影期間至少一側應有明顯脈衝變化"
    return True, "未套用場次動作規則"


def audit_run(run_dir: Path) -> dict:
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).exists()]
    if missing:
        return {"run": run_dir.name, "ready": False, "missing_files": missing}

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    frames = read_jsonl(run_dir / "frames.jsonl")
    telemetry = read_jsonl(run_dir / "telemetry.jsonl")
    errors = read_jsonl(run_dir / "telemetry_errors.jsonl")
    video = video_metadata(run_dir / "camera.mp4")

    session = str(manifest.get("session", run_dir.name))
    first = telemetry[0] if telemetry else {}
    last = telemetry[-1] if telemetry else {}
    left_delta = int(last.get("left_pulse", 0)) - int(first.get("left_pulse", 0))
    right_delta = int(last.get("right_pulse", 0)) - int(first.get("right_pulse", 0))
    moving_samples = sum(bool(row.get("moving")) for row in telemetry)
    total_abs_left = sum(
        abs(int(current.get("left_pulse", 0)) - int(previous.get("left_pulse", 0)))
        for previous, current in zip(telemetry, telemetry[1:])
    )
    total_abs_right = sum(
        abs(int(current.get("right_pulse", 0)) - int(previous.get("right_pulse", 0)))
        for previous, current in zip(telemetry, telemetry[1:])
    )
    movement_ok, movement_rule = motion_expectation(session, left_delta, right_delta)

    frame_duration = float(frames[-1]["host_time_s"]) - float(frames[0]["host_time_s"]) if len(frames) > 1 else 0.0
    telemetry_duration = (
        float(telemetry[-1]["host_time_s"]) - float(telemetry[0]["host_time_s"])
        if len(telemetry) > 1 else 0.0
    )
    measured_fps = (len(frames) - 1) / frame_duration if frame_duration > 0 else 0.0
    declared_fps = float(video.get("declared_fps") or 0.0)
    fps_ratio = measured_fps / declared_fps if declared_fps else None
    timing_ok = monotonic(frames) and monotonic(telemetry)
    decode_ok = video.get("opened", False) and video.get("decoded_frames") == len(frames)
    telemetry_ok = bool(telemetry) and not errors and all(row.get("ok") for row in telemetry)

    warnings: list[str] = []
    if fps_ratio is not None and not 0.95 <= fps_ratio <= 1.05:
        warnings.append(
            f"MP4 標示 {declared_fps:.1f} FPS，但實際擷取約 {measured_fps:.1f} FPS；播放速度會失真，分析時須使用 frames.jsonl 時戳"
        )
    if not movement_ok:
        warnings.append(f"動作資料不符預期：{movement_rule}")
    if manifest.get("camera_pitch_deg", 0) > 0:
        warnings.append("鏡頭角度記為正值；目前規格以向下為負，須確認實際是仰拍或俯拍")

    return {
        "run": run_dir.name,
        "session": session,
        "ready": decode_ok and telemetry_ok and timing_ok and movement_ok,
        "video": video,
        "frames_jsonl": len(frames),
        "telemetry_samples": len(telemetry),
        "telemetry_errors": len(errors),
        "frame_timeline_s": round(frame_duration, 3),
        "telemetry_timeline_s": round(telemetry_duration, 3),
        "measured_frame_rate": round(measured_fps, 3),
        "timestamps_monotonic": timing_ok,
        "left_delta_pulse": left_delta,
        "right_delta_pulse": right_delta,
        "moving_samples": moving_samples,
        "total_abs_left_pulse": total_abs_left,
        "total_abs_right_pulse": total_abs_right,
        "movement_ok": movement_ok,
        "known_route_length_m": manifest.get("known_route_length_m"),
        "interrupted": bool(manifest.get("interrupted")),
        "visual_return": visual_return_check(run_dir / "camera.mp4") if session.startswith("room_loop") else None,
        "warnings": warnings,
    }


def build_summary(results: list[dict], root: Path) -> dict:
    straight_runs = [
        result for result in results
        if str(result.get("session", "")).startswith("straight_1m")
        and result.get("movement_ok") and result.get("known_route_length_m")
    ]
    left_ppm_values = [
        abs(result["left_delta_pulse"]) / float(result["known_route_length_m"])
        for result in straight_runs
    ]
    right_ppm_values = [
        abs(result["right_delta_pulse"]) / float(result["known_route_length_m"])
        for result in straight_runs
    ]
    left_ppm = median(left_ppm_values) if left_ppm_values else None
    right_ppm = median(right_ppm_values) if right_ppm_values else None
    for result in results:
        if not str(result.get("session", "")).startswith("room_loop"):
            continue
        route_length = result.get("known_route_length_m")
        wheel_path = None
        route_ratio = None
        if left_ppm and right_ppm:
            wheel_path = (
                result.get("total_abs_left_pulse", 0) / left_ppm
                + result.get("total_abs_right_pulse", 0) / right_ppm
            ) / 2.0
            if route_length:
                route_ratio = wheel_path / float(route_length)
        visual_return = bool((result.get("visual_return") or {}).get("candidate"))
        route_motion_plausible = route_ratio is None or route_ratio >= 0.5
        closed_loop_ready = bool(result.get("ready")) and visual_return and route_motion_plausible
        result["wheel_path_proxy_m"] = round(wheel_path, 3) if wheel_path is not None else None
        result["declared_route_ratio"] = round(route_ratio, 3) if route_ratio is not None else None
        result["closed_loop_ready"] = closed_loop_ready
        result["ready"] = closed_loop_ready
        if not visual_return:
            result["warnings"].append("起點與終點畫面不一致，未形成可驗證的視覺閉環")
        if not route_motion_plausible:
            result["warnings"].append(
                f"輪脈衝顯示的行走量約 {wheel_path:.2f} m，明顯少於宣告路線 {float(route_length):.2f} m"
            )
        if result.get("interrupted"):
            result["warnings"].append("錄製由操作中斷，需確認是否已走完整條路線")

    sessions = [str(result.get("session", "")) for result in results]
    required = {
        "straight_1m": any(s.startswith("straight_1m") and r.get("ready") for s, r in zip(sessions, results)),
        "turn_left_360": any(s.startswith("turn_left_360") and r.get("ready") for s, r in zip(sessions, results)),
        "turn_right_360": any(s.startswith("turn_right_360") and r.get("ready") for s, r in zip(sessions, results)),
        "room_loop_01": any(s.startswith("room_loop") and r.get("ready") for s, r in zip(sessions, results)),
    }
    valid_fps = [r["measured_frame_rate"] for r in results if r.get("measured_frame_rate")]
    return {
        "root": str(root.resolve()),
        "run_count": len(results),
        "ready_run_count": sum(bool(result.get("ready")) for result in results),
        "required_sessions": required,
        "all_required_sessions_ready": all(required.values()),
        "median_capture_fps": round(median(valid_fps), 3) if valid_fps else None,
        "encoder_scale_from_recordings": {
            "left_pulses_per_m": round(left_ppm, 3) if left_ppm else None,
            "right_pulses_per_m": round(right_ppm, 3) if right_ppm else None,
        },
        "runs": results,
    }


def write_markdown(summary: dict, path: Path) -> None:
    lines = [
        "# 實車 SLAM 資料檢查報告",
        "",
        f"- 錄製資料夾：`{summary['run_count']}` 組",
        f"- 可直接分析：`{summary['ready_run_count']}` 組",
        f"- 四種必要錄製皆完成：`{'是' if summary['all_required_sessions_ready'] else '否'}`",
        "",
        "## 必要場次",
        "",
    ]
    labels = {
        "straight_1m": "一公尺直線",
        "turn_left_360": "原地左轉 360°",
        "turn_right_360": "原地右轉 360°",
        "room_loop_01": "房間閉環路線",
    }
    for key, ready in summary["required_sessions"].items():
        lines.append(f"- {'✅' if ready else '❌'} {labels[key]}")
    lines.extend(["", "## 每組結果", ""])
    for result in summary["runs"]:
        lines.extend(
            [
                f"### {result['run']}",
                "",
                f"- 狀態：{'可分析' if result.get('ready') else '需補錄或修正'}",
                f"- 影格／遙測：{result.get('frames_jsonl', 0)}／{result.get('telemetry_samples', 0)}",
                f"- 輪脈衝差：左 {result.get('left_delta_pulse', 0)}、右 {result.get('right_delta_pulse', 0)}",
            ]
        )
        for warning in result.get("warnings", []):
            lines.append(f"- ⚠️ {warning}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="檢查實車 SLAM 同步錄影與遙測完整性")
    parser.add_argument("--root", type=Path, default=Path("navigation/real_runs"))
    parser.add_argument("--output", type=Path, default=Path("navigation/output/real_data_audit.json"))
    parser.add_argument("--contact-sheets", action="store_true")
    args = parser.parse_args()

    run_dirs = sorted(path for path in args.root.iterdir() if path.is_dir())
    results = [audit_run(run_dir) for run_dir in run_dirs]
    summary = build_summary(results, args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    write_markdown(summary, markdown_path)
    if args.contact_sheets:
        sheet_root = args.output.parent / "real_data_contact_sheets"
        for run_dir in run_dirs:
            create_contact_sheet(run_dir / "camera.mp4", sheet_root / f"{run_dir.name}.jpg")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n報告：{markdown_path.resolve()}")


if __name__ == "__main__":
    main()
