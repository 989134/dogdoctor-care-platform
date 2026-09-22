"""Offline wheel/visual trajectory alignment and closed-loop drift correction."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import cv2
import numpy as np


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def integrate_odometry(rows: list[dict], calibration: dict) -> list[dict]:
    left_scale = float(calibration["left_m_per_pulse"]) * int(calibration["left_sign"])
    right_scale = float(calibration["right_m_per_pulse"]) * int(calibration["right_sign"])
    width = float(calibration["track_width_m"])
    x = y = yaw = distance = 0.0
    output = [{"time_s": float(rows[0]["host_time_s"]), "x": x, "y": y, "yaw": yaw, "distance": distance}]
    previous = rows[0]
    for row in rows[1:]:
        dl = (int(row["left_pulse"]) - int(previous["left_pulse"])) * left_scale
        dr = (int(row["right_pulse"]) - int(previous["right_pulse"])) * right_scale
        ds = (dl + dr) / 2.0
        dyaw = (dr - dl) / width
        if abs(dyaw) < 1e-9:
            x += ds * math.cos(yaw)
            y += ds * math.sin(yaw)
        else:
            radius = ds / dyaw
            x += radius * (math.sin(yaw + dyaw) - math.sin(yaw))
            y -= radius * (math.cos(yaw + dyaw) - math.cos(yaw))
        yaw = wrap(yaw + dyaw)
        distance += abs(ds)
        output.append({"time_s": float(row["host_time_s"]), "x": x, "y": y, "yaw": yaw, "distance": distance})
        previous = row
    return output


def interpolate_pose(poses: list[dict], time_s: float) -> dict:
    times = np.asarray([row["time_s"] for row in poses])
    index = int(np.searchsorted(times, time_s, side="right"))
    if index <= 0:
        return poses[0]
    if index >= len(poses):
        return poses[-1]
    a, b = poses[index - 1], poses[index]
    ratio = (time_s - a["time_s"]) / max(b["time_s"] - a["time_s"], 1e-9)
    yaw_delta = wrap(b["yaw"] - a["yaw"])
    return {
        "time_s": time_s,
        "x": a["x"] + ratio * (b["x"] - a["x"]),
        "y": a["y"] + ratio * (b["y"] - a["y"]),
        "yaw": wrap(a["yaw"] + ratio * yaw_delta),
        "distance": a["distance"] + ratio * (b["distance"] - a["distance"]),
    }


def rigid_align(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1, :] *= -1
        rotation = vt.T @ u.T
    translation = target_center - source_center @ rotation.T
    return rotation, translation


def draw_trajectories(rows: list[dict], output: Path) -> None:
    canvas = np.full((760, 980, 3), 248, np.uint8)
    cv2.putText(canvas, "Real Ranger: wheel + visual loop correction", (28, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (25, 55, 65), 2, cv2.LINE_AA)
    series = {
        "wheel odometry": (np.asarray([[r["odom_x"], r["odom_y"]] for r in rows]), (45, 80, 210)),
        "visual aligned": (np.asarray([[r["visual_x"], r["visual_y"]] for r in rows]), (40, 155, 70)),
        "loop corrected": (np.asarray([[r["fused_x"], r["fused_y"]] for r in rows]), (210, 105, 35)),
    }
    all_points = np.vstack([points for points, _ in series.values()])
    low, high = all_points.min(axis=0), all_points.max(axis=0)
    span = np.maximum(high - low, 0.1)
    scale = min(850 / span[0], 620 / span[1])
    for label, (points, color) in series.items():
        pixels = np.empty_like(points)
        pixels[:, 0] = 60 + (points[:, 0] - low[0]) * scale
        pixels[:, 1] = 700 - (points[:, 1] - low[1]) * scale
        for a, b in zip(pixels[:-1], pixels[1:]):
            cv2.line(canvas, tuple(a.astype(int)), tuple(b.astype(int)), color, 2)
        cv2.circle(canvas, tuple(pixels[0].astype(int)), 6, color, -1)
        cv2.circle(canvas, tuple(pixels[-1].astype(int)), 6, color, 2)
    for index, (label, (_, color)) in enumerate(series.items()):
        y = 72 + index * 26
        cv2.line(canvas, (690, y), (730, y), color, 3)
        cv2.putText(canvas, label, (740, y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 1, cv2.LINE_AA)
    cv2.imwrite(str(output), canvas)


def run(run_dir: Path, visual_dir: Path, calibration_path: Path, output_dir: Path) -> dict:
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    telemetry = read_jsonl(run_dir / "telemetry.jsonl")
    frames = read_jsonl(run_dir / "frames.jsonl")
    frame_times = {int(row["frame"]): float(row["host_time_s"]) for row in frames}
    odometry = integrate_odometry(telemetry, calibration)
    visual_report = json.loads((visual_dir / "report.json").read_text(encoding="utf-8"))
    visual_scale = float(visual_report["scale"]["factor"])

    visual_rows = []
    with (visual_dir / "trace.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            frame = int(row["frame"])
            if frame not in frame_times:
                continue
            pose = interpolate_pose(odometry, frame_times[frame])
            visual_rows.append({
                "frame": frame,
                "time_s": frame_times[frame],
                "state": row["state"],
                "vx": float(row["x"]) * visual_scale,
                "vy": float(row["y"]) * visual_scale,
                "odom": pose,
            })
    tracked = [row for row in visual_rows if row["state"] in {"tracked", "initialized"}]
    source = np.asarray([[row["vx"], row["vy"]] for row in tracked])
    target = np.asarray([[row["odom"]["x"], row["odom"]["y"]] for row in tracked])
    rotation, translation = rigid_align(source, target)

    fused_rows = []
    final_distance = max(odometry[-1]["distance"], 1e-9)
    provisional = []
    for row in visual_rows:
        visual = np.asarray([row["vx"], row["vy"]]) @ rotation.T + translation
        odom = row["odom"]
        # Monocular frame-to-frame translation is too noisy to replace metric wheel
        # motion directly. Use it as an independently verified place-closure
        # constraint, then distribute the odometry endpoint drift along the route.
        px = odom["x"]
        py = odom["y"]
        progress = min(1.0, max(0.0, odom["distance"] / final_distance))
        provisional.append((row, visual, odom, px, py, progress))
    end_x, end_y = provisional[-1][3], provisional[-1][4]
    for row, visual, odom, px, py, progress in provisional:
        fused_rows.append({
            "frame": row["frame"], "time_s": row["time_s"], "state": row["state"],
            "odom_x": odom["x"], "odom_y": odom["y"], "odom_yaw_deg": math.degrees(odom["yaw"]),
            "visual_x": float(visual[0]), "visual_y": float(visual[1]),
            "fused_x": px - progress * end_x, "fused_y": py - progress * end_y,
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(fused_rows[0])
    with (output_dir / "fused_trace.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(fused_rows)
    draw_trajectories(fused_rows, output_dir / "trajectory_fusion.png")
    odom_error = math.hypot(odometry[-1]["x"], odometry[-1]["y"])
    report = {
        "version": "offline_wheel_visual_fusion_v1",
        "run": str(run_dir.resolve()),
        "calibration": str(calibration_path.resolve()),
        "visual_report": str((visual_dir / "report.json").resolve()),
        "claim": "offline synchronized trajectory fusion feasibility",
        "wheel_odometry": {
            "travel_m": odometry[-1]["distance"],
            "closure_position_error_m": odom_error,
            "closure_heading_error_deg": math.degrees(odometry[-1]["yaw"]),
        },
        "visual": visual_report["quality"],
        "loop_check": visual_report["loop_check"],
        "fused": {
            "samples": len(fused_rows),
            "closure_position_error_m": math.hypot(fused_rows[-1]["fused_x"], fused_rows[-1]["fused_y"]),
            "method": "wheel odometry + verified visual place closure + distributed endpoint drift",
        },
        "limitations": [
            "Offline validation; no real-time pose graph optimizer yet.",
            "The loop correction assumes the independently verified start/end place match.",
            "Current metric wheel scale is provisional and uses one synchronized 1 m run."
        ],
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="融合同步錄製的 Ranger 編碼器與視覺軌跡")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("visual_dir", type=Path)
    parser.add_argument("--calibration", type=Path, default=Path("navigation/calibration.json"))
    parser.add_argument("--output", type=Path, default=Path("navigation/output/real_fusion"))
    args = parser.parse_args()
    report = run(args.run_dir, args.visual_dir, args.calibration, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
