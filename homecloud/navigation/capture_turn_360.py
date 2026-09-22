"""Capture three left and three right 360-degree Ranger turn trials."""

import argparse
import json
import math
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


def read_angle_error(direction_zh):
    while True:
        raw = input(
            f"輸入{direction_zh} 360° 的角度誤差（超過輸入正數，例如 5；不足輸入負數，例如 -8；準確輸入 0）："
        ).strip()
        try:
            value = float(raw)
        except ValueError:
            print("格式不正確。請只輸入角度數字，例如 5、-8 或 0。")
            continue
        if not -180 < value < 180:
            print("誤差必須介於 -180° 與 180° 之間。")
            continue
        return value


def sign(value):
    return 1 if value >= 0 else -1


def load_straight_calibration(path):
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    trials = data.get("trials", [])
    summary = data.get("summary", {})
    if not trials:
        return None
    left_mean_delta = statistics.mean(item["left_delta_pulse"] for item in trials)
    right_mean_delta = statistics.mean(item["right_delta_pulse"] for item in trials)
    return {
        "source": str(path.resolve()),
        "left_m_per_pulse": float(summary["left_m_per_pulse"]),
        "right_m_per_pulse": float(summary["right_m_per_pulse"]),
        "left_forward_sign": sign(left_mean_delta),
        "right_forward_sign": sign(right_mean_delta),
    }


def effective_track_width(trial, straight):
    actual_angle_rad = math.radians(trial["actual_angle_deg"])
    dl = (
        trial["left_delta_pulse"]
        * straight["left_m_per_pulse"]
        * straight["left_forward_sign"]
    )
    dr = (
        trial["right_delta_pulse"]
        * straight["right_m_per_pulse"]
        * straight["right_forward_sign"]
    )
    return abs(dr - dl) / actual_angle_rad


def direction_summary(trials):
    trials = [item for item in trials if item.get("valid", True)]
    if not trials:
        return {"trial_count": 0}
    result = {
        "trial_count": len(trials),
        "mean_abs_left_delta_pulse": statistics.mean(
            abs(item["left_delta_pulse"]) for item in trials
        ),
        "mean_abs_right_delta_pulse": statistics.mean(
            abs(item["right_delta_pulse"]) for item in trials
        ),
        "mean_angle_error_deg": statistics.mean(item["angle_error_deg"] for item in trials),
        "max_abs_angle_error_deg": max(abs(item["angle_error_deg"]) for item in trials),
    }
    widths = [
        item["effective_track_width_m"] for item in trials
        if item.get("effective_track_width_m") is not None
    ]
    if widths:
        result["mean_effective_track_width_m"] = statistics.mean(widths)
    return result


def build_summary(trials, required_per_direction=3):
    valid = [
        item for item in trials
        if item.get("valid", True)
        and item.get("direction") in {"left", "right"}
        and not (item.get("left_delta_pulse") == 0 and item.get("right_delta_pulse") == 0)
    ]
    left_trials = [item for item in valid if item["direction"] == "left"]
    right_trials = [item for item in valid if item["direction"] == "right"]
    if len(left_trials) < required_per_direction or len(right_trials) < required_per_direction:
        return {
            "valid": False,
            "status": "pending_replacement_trials",
            "valid_trial_count": len(valid),
            "invalid_trial_count": len(trials) - len(valid),
            "actual_left_valid_trials": len(left_trials),
            "actual_right_valid_trials": len(right_trials),
            "required_per_direction": required_per_direction,
        }
    summary = {
        "valid": True,
        "status": "complete",
        "left": direction_summary(left_trials),
        "right": direction_summary(right_trials),
    }
    widths = [
        item["effective_track_width_m"] for item in valid
        if item.get("effective_track_width_m") is not None
    ]
    if widths:
        summary["overall_mean_effective_track_width_m"] = statistics.mean(widths)
        summary["overall_median_effective_track_width_m"] = statistics.median(widths)
    return summary


def main():
    parser = argparse.ArgumentParser(description="擷取 Ranger 左右各三次原地 360 度轉向資料")
    parser.add_argument("--url", default="http://10.129.211.235:5000")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--direction", choices=("both", "left", "right"), default="both",
                        help="只補測單一方向時選 left 或 right")
    parser.add_argument("--append", action="store_true",
                        help="保留現有 JSON 並追加新量測；用於補測無效資料")
    parser.add_argument(
        "--straight-data",
        type=Path,
        default=Path("navigation/output/straight_1m_trials.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("navigation/output/turn_360_trials.json"),
    )
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials 必須至少為 1")
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

    straight = load_straight_calibration(args.straight_data)
    if straight:
        print(f"已載入一公尺直線校正：{straight['source']}")
    else:
        print("尚未找到完整的一公尺直線結果；本次仍會保存 pulse 與角度誤差。")
    print("此程式只讀取遙測。請用另一個 PowerShell 視窗控制左轉或右轉。")
    print("每次均以車體同一條方向標記對準地面基準線。")

    existing = None
    all_trials = []
    if args.append:
        if not args.output.exists():
            parser.error("--append 需要已存在的輸出 JSON")
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        all_trials = list(existing.get("trials", []))
    choices = (("left", "左轉"), ("right", "右轉"))
    if args.direction != "both":
        choices = tuple(item for item in choices if item[0] == args.direction)
    for direction, direction_zh in choices:
        previous_count = sum(
            item.get("valid", True) and item.get("direction") == direction
            for item in all_trials
        )
        for offset in range(1, args.trials + 1):
            number = previous_count + offset
            print(f"\n=== {direction_zh}第 {number} 次，共 {args.trials} 次 ===")
            input("將車體方向標記對準基準線、完全停止後，按 Enter 擷取起點 pulse：")
            start = read_telemetry(base_url)
            print(f"起點：left={start['left_pulse']}，right={start['right_pulse']}")
            input(
                f"使用另一個 PowerShell 讓車原地{direction_zh}一圈並停止；回到此視窗後按 Enter 擷取終點："
            )
            end = read_telemetry(base_url)
            angle_error_deg = read_angle_error(direction_zh)
            trial = {
                "direction": direction,
                "trial": number,
                "actual_direction": direction,
                "actual_direction_trial": number,
                "command_direction": direction,
                "command_trial": number,
                "target_angle_deg": 360.0,
                "angle_error_deg": angle_error_deg,
                "angle_error_convention": "positive=overshoot, negative=undershoot",
                "actual_angle_deg": 360.0 + angle_error_deg,
                "start": start,
                "end": end,
                "left_delta_pulse": end["left_pulse"] - start["left_pulse"],
                "right_delta_pulse": end["right_pulse"] - start["right_pulse"],
            }
            trial["valid"] = not (
                trial["left_delta_pulse"] == 0 and trial["right_delta_pulse"] == 0
            )
            if not trial["valid"]:
                trial["invalid_reason"] = "zero_encoder_delta"
            if straight and trial["valid"]:
                trial["effective_track_width_m"] = effective_track_width(trial, straight)
            all_trials.append(trial)
            print(
                f"{direction_zh}第 {number} 次完成："
                f"left_delta={trial['left_delta_pulse']}，"
                f"right_delta={trial['right_delta_pulse']}，"
                f"角度誤差={angle_error_deg:+.1f}°"
            )

    required_per_direction = int((existing or {}).get("trials_per_direction", 3))
    summary = build_summary(all_trials, required_per_direction)

    result = {
        "schema_version": 2,
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "gateway_url": base_url,
        "trials_per_direction": required_per_direction,
        "straight_calibration": straight,
        "trials": all_trials,
        "summary": summary,
    }
    if existing and existing.get("direction_label_correction"):
        result["direction_label_correction"] = existing["direction_label_correction"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n轉向量測已保存：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
