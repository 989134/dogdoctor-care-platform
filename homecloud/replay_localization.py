"""Replay synchronized Ranger data through the passive localization runtime."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from navigation.localization_runtime import LocalizationEngine


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def draw_overview(status: dict, output: Path) -> None:
    canvas = np.full((780, 980, 3), 248, np.uint8)
    cv2.putText(canvas, "Ranger passive localization replay", (28, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (25, 55, 65), 2, cv2.LINE_AA)
    trace = np.asarray(status["trace"], dtype=np.float64)
    occupied = np.asarray(status["map"].get("occupied", []), dtype=np.float64)
    free = np.asarray(status["map"].get("free", []), dtype=np.float64)
    points = [value for value in (trace, occupied, free) if value.size]
    if points:
        all_points = np.vstack(points)
        low = all_points.min(axis=0) - 0.2
        high = all_points.max(axis=0) + 0.2
        span = np.maximum(high - low, 0.5)
        scale = min(870 / span[0], 650 / span[1])

        def pixel(point):
            return int(55 + (point[0] - low[0]) * scale), int(720 - (point[1] - low[1]) * scale)

        for point in free:
            cv2.circle(canvas, pixel(point), 1, (220, 228, 230), -1)
        for point in occupied:
            cv2.circle(canvas, pixel(point), 2, (45, 55, 60), -1)
        if len(trace) > 1:
            for a, b in zip(trace[:-1], trace[1:]):
                if np.linalg.norm(b - a) < 0.5:
                    cv2.line(canvas, pixel(a), pixel(b), (210, 105, 35), 2)
            cv2.circle(canvas, pixel(trace[0]), 7, (45, 165, 65), -1)
            cv2.circle(canvas, pixel(trace[-1]), 7, (40, 55, 210), -1)
    cv2.putText(canvas, f"confidence {status['localization_confidence']:.2f}", (28, 754),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (25, 55, 65), 1, cv2.LINE_AA)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), canvas)


def replay(run_dir: Path, calibration: Path, output: Path, frame_step: int) -> dict:
    frames = read_jsonl(run_dir / "frames.jsonl")
    telemetry = read_jsonl(run_dir / "telemetry.jsonl")
    if not frames or not telemetry:
        raise RuntimeError("錄製資料缺少影格或遙測")
    frame_time = {int(row["frame"]): float(row["host_time_s"]) for row in frames}
    final_frame = max(frame_time)
    capture = cv2.VideoCapture(str(run_dir / "camera.mp4"))
    if not capture.isOpened():
        raise RuntimeError("無法開啟 camera.mp4")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    engine = LocalizationEngine(calibration, (width, height))
    telemetry_index = 0
    trace_rows = []
    frame_index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index not in frame_time:
            frame_index += 1
            continue
        host_time = frame_time[frame_index]
        while telemetry_index < len(telemetry) and float(telemetry[telemetry_index]["host_time_s"]) <= host_time:
            packet = telemetry[telemetry_index]
            engine.update_telemetry(packet, float(packet["host_time_s"]))
            telemetry_index += 1
        if frame_index % frame_step == 0 or frame_index == final_frame:
            vision = engine.process_frame(frame, host_time, frame_index)
            trace_rows.append({
                "frame": frame_index, "time_s": host_time,
                "x": engine.pose.x, "y": engine.pose.y, "yaw": engine.pose.yaw,
                "confidence": engine.confidence, "vision_state": vision["state"],
                "inliers": vision["inliers"], "front_m": engine.front_m,
            })
        frame_index += 1
    capture.release()
    while telemetry_index < len(telemetry):
        packet = telemetry[telemetry_index]
        engine.update_telemetry(packet, float(packet["host_time_s"]))
        telemetry_index += 1
    status = engine.status(float(frames[-1]["host_time_s"]), include_map=True)
    output.mkdir(parents=True, exist_ok=True)
    (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "localization_trace.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(trace_rows[0]))
        writer.writeheader()
        writer.writerows(trace_rows)
    map_payload = {
        "size": engine.grid.size, "resolution": engine.grid.resolution,
        "origin": engine.grid.origin, "cells": engine.grid.cells,
    }
    (output / "occupancy_map.json").write_text(json.dumps(map_payload), encoding="utf-8")
    draw_overview(status, output / "overview.png")
    report = {
        "version": "passive_localization_replay_v1",
        "run": str(run_dir.resolve()),
        "calibration": str(calibration.resolve()),
        "frames_processed": frame_index,
        "vision_frames": status["vision"]["frames"],
        "telemetry_samples": status["telemetry_samples"],
        "tracking_loss_events": status["vision"]["tracking_loss_events"],
        "loop_closure_count": len(status["loop_closures"]),
        "final_pose": status["pose"],
        "localization_confidence": status["localization_confidence"],
        "occupied_cells": len(status["map"]["occupied"]),
        "free_cells": len(status["map"]["free"]),
        "motion_commands_sent": 0,
        "claim": "offline replay of the same passive runtime intended for live camera and telemetry",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html = f"""<!doctype html><meta charset="utf-8"><title>Ranger 即時定位核心回放驗證</title>
<style>body{{font-family:system-ui,'Microsoft JhengHei';max-width:1050px;margin:28px auto;background:#edf4f5;color:#17343d}}.card{{background:white;padding:18px;margin:14px 0;border-radius:12px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border-bottom:1px solid #d9e3e5;text-align:left}}img{{max-width:100%;border-radius:9px}}.ok{{color:#16885a;font-weight:700}}.warn{{color:#995c00}}</style>
<h1>Ranger 即時定位核心回放驗證</h1>
<div class="card"><p class="ok">同步影像、輪編碼器、視覺閉環與超音波建圖回放完成。</p><p>本報告沒有傳送任何馬達指令。</p></div>
<div class="card"><table>
<tr><th>來源影格</th><td>{report['frames_processed']}</td></tr>
<tr><th>分析影格</th><td>{report['vision_frames']}</td></tr>
<tr><th>遙測樣本</th><td>{report['telemetry_samples']}</td></tr>
<tr><th>追蹤中斷</th><td>{report['tracking_loss_events']}</td></tr>
<tr><th>閉環修正</th><td>{report['loop_closure_count']}</td></tr>
<tr><th>最終位置</th><td>x={report['final_pose']['x']:.3f} m，y={report['final_pose']['y']:.3f} m，yaw={report['final_pose']['yaw']:.3f} rad</td></tr>
<tr><th>定位可信度</th><td>{report['localization_confidence']:.2f}</td></tr>
<tr><th>障礙／自由格</th><td>{report['occupied_cells']}／{report['free_cells']}</td></tr>
</table></div><div class="card"><h2>軌跡與超音波地圖</h2><img src="overview.png"></div>
<div class="card warn">這是以實車同步資料回放同一套被動定位核心；實時連線與長時間穩定性仍需樹莓派服務在線時驗證。</div>"""
    (output / "report.html").write_text(html, encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="回放實車資料並驗證即時定位與超音波建圖核心")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--calibration", type=Path, default=Path("navigation/calibration.json"))
    parser.add_argument("--output", type=Path, default=Path("navigation/output/localization_replay"))
    parser.add_argument("--frame-step", type=int, default=5)
    args = parser.parse_args()
    if args.frame_step < 1:
        parser.error("--frame-step 至少為 1")
    report = replay(args.run_dir, args.calibration, args.output, args.frame_step)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
