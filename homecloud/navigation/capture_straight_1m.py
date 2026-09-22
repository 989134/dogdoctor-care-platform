"""Interactively capture three one-metre Ranger encoder calibration trials.

This tool only reads gateway health and telemetry. The operator drives the robot
with the existing manual-control UI and confirms when the reference point on the
robot reaches the one-metre mark.
"""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def get_json(url, timeout=3.0):
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except Exception:
            detail = {"error": str(exc)}
        return exc.code, detail


def read_telemetry(base_url, wait_seconds=5.0):
    """Wait for t_ms to change, proving that telemetry is currently live."""
    deadline = time.monotonic() + wait_seconds
    first_t_ms = None
    last_status = None
    last_data = None
    while time.monotonic() < deadline:
        status, data = get_json(base_url + "/telemetry")
        last_status, last_data = status, data
        if status == 200 and data.get("ok") and "t_ms" in data:
            current_t_ms = int(data["t_ms"])
            if first_t_ms is None:
                first_t_ms = current_t_ms
            elif current_t_ms != first_t_ms:
                return {
                    "left_pulse": int(data["left_pulse"]),
                    "right_pulse": int(data["right_pulse"]),
                    "t_ms": current_t_ms,
                    "front_cm": data.get("front_cm"),
                }
        time.sleep(0.2)
    raise RuntimeError(
        f"5 秒內沒有收到新的遙測樣本：HTTP {last_status} {last_data}"
    )


def read_deviation():
    while True:
        raw = input(
            "輸入終點橫向偏移（例如 L2.5、R3、0；L=偏左，R=偏右）："
        ).strip().upper().replace(" ", "")
        if raw in {"0", "0.0"}:
            return "none", 0.0, 0.0
        if len(raw) >= 2 and raw[0] in {"L", "R"}:
            try:
                magnitude = abs(float(raw[1:]))
            except ValueError:
                pass
            else:
                direction = "left" if raw[0] == "L" else "right"
                signed = -magnitude if direction == "left" else magnitude
                return direction, magnitude, signed
        print("格式不正確。請輸入 L2.5、R3 或 0。")


def main():
    parser = argparse.ArgumentParser(description="擷取三次 Ranger 一公尺直線校正資料")
    parser.add_argument("--url", default="http://10.129.211.235:5000")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--distance-m", type=float, default=1.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("navigation/output/straight_1m_trials.json"),
    )
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials 必須至少為 1")
    if args.distance_m <= 0:
        parser.error("--distance-m 必須大於 0")
    base_url = args.url.rstrip("/")

    health_status, health = get_json(base_url + "/health")
    if health_status != 200 or not health.get("ok") or not health.get("serial_open"):
        raise SystemExit(
            "閘道或遙測尚未就緒："
            + json.dumps({"http_status": health_status, "health": health}, ensure_ascii=False)
        )

    try:
        read_telemetry(base_url)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print("連線與遙測正常。此程式不會控制馬達，請使用既有前端手動駕駛。")
    print("每次都用車體上的同一個參考點對準起點與一公尺終點。")
    trials = []
    for number in range(1, args.trials + 1):
        print(f"\n=== 第 {number} 次，共 {args.trials} 次 ===")
        input("將車放回起點、擺正且完全停止後，按 Enter 擷取起點 pulse：")
        start = read_telemetry(base_url)
        print(
            f"起點：left={start['left_pulse']}，right={start['right_pulse']}"
        )
        input(
            "現在切到前端，讓車直走至一公尺線並停止；回到此視窗後按 Enter 擷取終點："
        )
        end = read_telemetry(base_url)
        left_delta = end["left_pulse"] - start["left_pulse"]
        right_delta = end["right_pulse"] - start["right_pulse"]
        direction, deviation_cm, signed_deviation_cm = read_deviation()
        trial = {
            "trial": number,
            "distance_m": args.distance_m,
            "start": start,
            "end": end,
            "left_delta_pulse": left_delta,
            "right_delta_pulse": right_delta,
            "deviation_direction": direction,
            "deviation_cm": deviation_cm,
            "signed_deviation_cm": signed_deviation_cm,
        }
        trials.append(trial)
        print(
            f"第 {number} 次完成：left_delta={left_delta}，"
            f"right_delta={right_delta}，偏移={direction} {deviation_cm:.1f} cm"
        )

    left_per_m = statistics.mean(abs(item["left_delta_pulse"]) for item in trials) / args.distance_m
    right_per_m = statistics.mean(abs(item["right_delta_pulse"]) for item in trials) / args.distance_m
    signed_deviations = [item["signed_deviation_cm"] for item in trials]
    result = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "gateway_url": base_url,
        "trial_count": len(trials),
        "distance_m": args.distance_m,
        "trials": trials,
        "summary": {
            "left_pulses_per_m": left_per_m,
            "right_pulses_per_m": right_per_m,
            "left_m_per_pulse": 1.0 / left_per_m,
            "right_m_per_pulse": 1.0 / right_per_m,
            "mean_signed_deviation_cm": statistics.mean(signed_deviations),
            "max_absolute_deviation_cm": max(abs(value) for value in signed_deviations),
            "deviation_sign_convention": "negative=left, positive=right",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n三次量測完成：")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
