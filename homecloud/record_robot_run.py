"""Record raw Pi camera frames and Ranger telemetry on one PC clock."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2


def get_json(url, timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"無法讀取 {url}：{exc}") from exc


def write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def record(args):
    health = get_json(args.gateway_url.rstrip("/") + "/health")
    if not health.get("ok") or not health.get("serial_open") or not health.get("telemetry_fresh"):
        raise RuntimeError(f"Ranger 閘道或遙測尚未就緒：{health}")
    first_telemetry = get_json(args.gateway_url.rstrip("/") + "/telemetry")
    if not first_telemetry.get("ok"):
        raise RuntimeError("Ranger 沒有有效遙測")

    capture = cv2.VideoCapture(args.camera_url)
    if not capture.isOpened():
        raise RuntimeError(f"無法開啟原始攝影機串流：{args.camera_url}")
    ok, first_frame = capture.read()
    if not ok:
        capture.release()
        raise RuntimeError("攝影機已連線但讀不到第一張影格")
    height, width = first_frame.shape[:2]
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    session = args.output_root / f"{timestamp}_{args.name}"
    session.mkdir(parents=True, exist_ok=False)
    writer = cv2.VideoWriter(
        str(session / "camera.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
        args.output_fps, (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError("無法建立 MP4 輸出")

    start_mono = time.monotonic()
    start_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    stop = threading.Event()
    telemetry_rows = []
    telemetry_errors = []

    def telemetry_reader():
        interval = 1.0 / args.telemetry_hz
        next_at = time.monotonic()
        while not stop.is_set():
            try:
                packet = get_json(args.gateway_url.rstrip("/") + "/telemetry")
                telemetry_rows.append({"host_time_s": time.monotonic() - start_mono, **packet})
            except RuntimeError as exc:
                telemetry_errors.append({"host_time_s": time.monotonic() - start_mono, "error": str(exc)})
            next_at += interval
            stop.wait(max(0.0, next_at - time.monotonic()))

    thread = threading.Thread(target=telemetry_reader, daemon=True, name="TelemetryRecorder")
    thread.start()
    frame_rows = []
    frame_index = 0
    interrupted = False
    try:
        frame = first_frame
        while time.monotonic() - start_mono < args.duration:
            if frame is None:
                ok, frame = capture.read()
                if not ok:
                    break
            host_time = time.monotonic() - start_mono
            writer.write(frame)
            frame_rows.append({"frame": frame_index, "host_time_s": host_time})
            frame_index += 1
            if args.preview:
                preview = frame.copy()
                cv2.putText(preview, f"REC {host_time:.1f}s  q=stop", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, .7, (20, 20, 240), 2, cv2.LINE_AA)
                cv2.imshow("Robot raw recording", preview)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            ok, frame = capture.read()
            if not ok:
                break
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop.set()
        thread.join(timeout=2)
        capture.release()
        writer.release()
        if args.preview:
            cv2.destroyAllWindows()

    write_jsonl(session / "frames.jsonl", frame_rows)
    write_jsonl(session / "telemetry.jsonl", telemetry_rows)
    write_jsonl(session / "telemetry_errors.jsonl", telemetry_errors)
    elapsed = time.monotonic() - start_mono
    manifest = {
        "session": args.name, "description": args.description,
        "started_utc": start_utc, "duration_requested_s": args.duration,
        "duration_recorded_s": elapsed, "interrupted": interrupted,
        "camera_url": args.camera_url, "video_size": [width, height],
        "video_output_fps": args.output_fps, "frames_recorded": len(frame_rows),
        "measured_capture_fps": len(frame_rows) / max(elapsed, .001),
        "gateway_url": args.gateway_url, "health_at_start": health,
        "first_telemetry": first_telemetry,
        "telemetry_samples": len(telemetry_rows), "telemetry_errors": len(telemetry_errors),
        "camera_height_cm": args.camera_height_cm,
        "camera_pitch_deg": args.camera_pitch_deg,
        "known_route_length_m": args.route_length_m,
        "claims": {"raw_frontend_free_video": True, "metric_ground_truth": args.route_length_m is not None},
    }
    (session / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if len(frame_rows) < 10:
        raise RuntimeError(f"錄影影格太少；資料保留在 {session}")
    if len(telemetry_rows) < max(2, elapsed * args.telemetry_hz * .7):
        raise RuntimeError(f"遙測成功率不足 70%；資料保留在 {session}")
    return session, manifest


def main():
    parser = argparse.ArgumentParser(description="同步錄製機器狗原始影像與 Ranger 遙測")
    parser.add_argument("--name", required=True, help="英文場次名稱，例如 straight_1m")
    parser.add_argument("--description", default="")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--camera-url", default="http://172.20.10.2:5001/video_feed")
    parser.add_argument("--gateway-url", default="http://172.20.10.2:5000")
    parser.add_argument("--camera-height-cm", type=float, required=True)
    parser.add_argument("--camera-pitch-deg", type=float, required=True,
                        help="向下為負，例如 -10")
    parser.add_argument("--route-length-m", type=float)
    parser.add_argument("--telemetry-hz", type=float, default=5.0)
    parser.add_argument("--output-fps", type=float, default=20.0)
    parser.add_argument("--output-root", type=Path, default=Path("navigation/real_runs"))
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if args.duration < 5 or args.telemetry_hz < 1 or args.output_fps < 1:
        parser.error("duration 至少 5 秒，telemetry-hz 與 output-fps 至少為 1")
    if args.route_length_m is not None and args.route_length_m <= 0:
        parser.error("route-length-m 必須大於 0")
    session, manifest = record(args)
    print(json.dumps({
        "ok": True, "session": str(session.resolve()),
        "frames": manifest["frames_recorded"],
        "telemetry_samples": manifest["telemetry_samples"],
        "measured_capture_fps": round(manifest["measured_capture_fps"], 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

