"""Run one bounded, supervised real L-route validation and save evidence."""

from __future__ import annotations

import json
import math
import time
import urllib.request


BASE = "http://127.0.0.1:5007/api/real-patrol"
ROUTE = [
    {"id": "corner", "name": "右轉觀察點", "x": 0.25, "y": 0.0,
     "yaw_deg": -90, "observe_seconds": 2, "priority": 1},
    {"id": "finish", "name": "右側終點", "x": 0.25, "y": -0.25,
     "yaw_deg": -90, "observe_seconds": 3, "priority": 1},
]


def request(path: str, body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None if body is None else json.dumps(body).encode("utf-8")
    method = "GET" if data is None else "POST"
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=12) as response:
        return json.load(response)


def stop():
    try:
        request("/emergency-stop", {})
    except Exception:
        pass


def main():
    preflight = request("/preflight")
    if not preflight.get("ok") or not preflight.get("motion_enabled"):
        raise RuntimeError(f"preflight failed: {preflight.get('errors')}")
    front = (preflight.get("localization") or {}).get("front_m")
    if not isinstance(front, (int, float)) or front < 0.8:
        raise RuntimeError(f"front clearance must be at least 0.8m, got {front}")

    request("/start", {"waypoints": ROUTE, "route_mode": "manual"})
    started = time.monotonic()
    last_signature = None
    final = None
    try:
        while time.monotonic() - started < 75:
            status = request("/status")
            pose = status.get("pose") or {}
            x, y = float(pose.get("x", 0)), float(pose.get("y", 0))
            count = int(status.get("hardware_commands_sent", 0))
            if count > 18 or math.hypot(x, y) > 1.0:
                raise RuntimeError("safety bound exceeded")
            signature = (
                status.get("state"), status.get("command"), count,
                round(x, 3), round(y, 3),
                round(math.degrees(float(pose.get("yaw", 0))), 1),
                status.get("reason", ""), status.get("last_error", ""),
            )
            if signature != last_signature:
                print(json.dumps({
                    "state": signature[0], "command": signature[1],
                    "commands": count, "x": signature[3], "y": signature[4],
                    "yaw_deg": signature[5], "reason": signature[6],
                    "error": signature[7], "front_m": status.get("front_m"),
                }, ensure_ascii=False), flush=True)
                last_signature = signature
            if status.get("state") in {"completed", "blocked", "localization_lost", "cancelled"}:
                final = status
                break
            time.sleep(0.4)
        if final is None:
            raise TimeoutError("L-route validation timed out")
    except Exception:
        stop()
        raise
    finally:
        saved = request("/save-evidence", {})
        print(json.dumps(saved, ensure_ascii=False), flush=True)

    if final.get("state") != "completed":
        raise RuntimeError(
            f"mission ended safely without completion: {final.get('state')} - {final.get('reason')}"
        )


if __name__ == "__main__":
    main()
