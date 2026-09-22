"""Ranger gateway adapter for the future measured patrol runtime.

The adapter contains no autonomous loop by itself. PatrolMission produces a
command; this adapter supplies measured SensorSnapshot values and transmits the
command only when motion was explicitly enabled and the preflight is fresh.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

from navigation.core import Odometry, Pose
from navigation.mission import SensorSnapshot
from navigation.local_network import direct_urlopen
from navigation.telemetry import PulseCalibration


class GatewayError(RuntimeError):
    pass


class HttpJsonTransport:
    def __init__(self, base_url, timeout=4.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with direct_urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GatewayError(f"Ranger 閘道連線失敗：{exc}") from exc


class RangerPatrolAdapter:
    allowed_commands = {"forward", "backward", "left", "right", "stop"}

    def __init__(self, calibration, track_width_m, base_url="http://172.20.10.2:5000",
                 motion_enabled=False, transport=None, yaw_sign=1,
                 left_turn_scale=1.0, right_turn_scale=1.0):
        self.calibration = calibration
        self.odometry = Odometry(
            track_width_m, yaw_sign=yaw_sign,
            left_turn_scale=left_turn_scale, right_turn_scale=right_turn_scale,
        )
        self.motion_enabled = motion_enabled
        self.transport = transport or HttpJsonTransport(base_url)
        self.preflight_valid_until = 0.0
        self.last_pose = None
        self.last_sample_time = None

    @classmethod
    def from_calibration_file(cls, path: Path, **kwargs):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        required = {"left_m_per_pulse", "right_m_per_pulse", "left_sign", "right_sign", "track_width_m"}
        if not required.issubset(data):
            raise ValueError("實車巡檢校正檔缺少輪距或編碼器公尺換算")
        calibration = PulseCalibration(
            data["left_m_per_pulse"], data["right_m_per_pulse"],
            int(data["left_sign"]), int(data["right_sign"]),
        )
        return cls(
            calibration, float(data["track_width_m"]),
            yaw_sign=int(data.get("yaw_sign", 1)),
            left_turn_scale=float(data.get("left_turn_yaw_scale", 1.0)),
            right_turn_scale=float(data.get("right_turn_yaw_scale", 1.0)),
            **kwargs,
        )

    def preflight(self, now=None):
        now = time.monotonic() if now is None else now
        health = self.transport.request("GET", "/health")
        telemetry = self.transport.request("GET", "/telemetry")
        errors = []
        if not health.get("ok") or not health.get("serial_open"):
            errors.append("序列埠尚未就緒")
        if not str(health.get("board", "")).startswith("MBOT_RANGER_AURIGA"):
            errors.append("控制板識別不符")
        if not health.get("telemetry_fresh") or not telemetry.get("ok") or telemetry.get("age_sec", 99) > .6:
            errors.append("遙測資料不新鮮")
        front_cm = telemetry.get("front_cm")
        if not isinstance(front_cm, (int, float)) or not math.isfinite(front_cm) or not 1 <= front_cm < 399.5:
            errors.append("沒有有效前向距離，禁止自主移動")
        ok = not errors
        self.preflight_valid_until = now + .8 if ok else 0.0
        return {"ok": ok, "errors": errors, "health": health, "telemetry": telemetry}

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        packet = self.transport.request("GET", "/telemetry")
        if not packet.get("ok"):
            raise GatewayError("Ranger 尚無有效遙測")
        age = float(packet.get("age_sec", 99))
        front_cm = float(packet.get("front_cm", -1))
        if age > .6 or not 1 <= front_cm < 399.5:
            raise GatewayError("Ranger 遙測逾時或前向距離無效")
        left_m, right_m = self.calibration.meters(packet)
        pose = self.odometry.update(float(packet["t_ms"]) / 1000.0, left_m, right_m)
        speed = 0.0
        if self.last_pose is not None and self.last_sample_time is not None and now > self.last_sample_time:
            speed = math.hypot(pose.x - self.last_pose.x, pose.y - self.last_pose.y) / (now - self.last_sample_time)
        self.last_pose = Pose(pose.x, pose.y, pose.yaw)
        self.last_sample_time = now
        return SensorSnapshot(
            now=now, pose=Pose(pose.x, pose.y, pose.yaw), sample_time=now - age,
            localization_confidence=max(0.0, min(1.0, 1.0 - age)),
            front_m=front_cm / 100.0, speed_mps=speed,
        )

    def apply(self, command, now=None):
        now = time.monotonic() if now is None else now
        if command not in self.allowed_commands:
            raise ValueError("未知 Ranger 指令")
        if command != "stop":
            if not self.motion_enabled:
                raise GatewayError("實車自主輸出尚未啟用")
            if now > self.preflight_valid_until:
                result = self.preflight(now)
                if not result["ok"]:
                    raise GatewayError("實車前置檢查失敗：" + "；".join(result["errors"]))
        telemetry_before = self.transport.request("GET", "/telemetry")
        response = self.transport.request("POST", "/control", {"command": command})
        if not response.get("ok"):
            raise GatewayError(f"Ranger 拒絕指令：{response}")
        done = str(response.get("done", ""))
        if command != "stop" and "reason=encoder_target" not in done:
            raise GatewayError(f"Ranger 未完成編碼器步進：{done or 'missing DONE'}")
        # DONE means PWM has been cut, but the tracked chassis can keep rolling
        # from inertia. Do not let the next navigation decision use a packet
        # captured during that residual motion. Require two new, stationary
        # controller packets after the command.
        deadline = time.monotonic() + 2.5
        settled_samples = 0
        last_t_ms = telemetry_before.get("t_ms")
        telemetry_after = telemetry_before
        while time.monotonic() < deadline:
            telemetry_after = self.transport.request("GET", "/telemetry")
            t_ms = telemetry_after.get("t_ms")
            is_new = isinstance(t_ms, (int, float)) and t_ms != last_t_ms
            stopped = (
                not bool(telemetry_after.get("moving", 0))
                and abs(float(telemetry_after.get("left_rpm", 0.0))) <= 5.0
                and abs(float(telemetry_after.get("right_rpm", 0.0))) <= 5.0
            )
            if is_new:
                settled_samples = settled_samples + 1 if stopped else 0
                last_t_ms = t_ms
            if settled_samples >= 2:
                break
            time.sleep(.12)
        settled = settled_samples >= 2
        response["telemetry_before"] = telemetry_before
        response["telemetry_after"] = telemetry_after
        response["settled"] = settled
        if all(
            isinstance(packet.get(side), (int, float))
            for packet in (telemetry_before, telemetry_after)
            for side in ("left_pulse", "right_pulse")
        ):
            response["pulse_delta"] = {
                "left": int(telemetry_after["left_pulse"] - telemetry_before["left_pulse"]),
                "right": int(telemetry_after["right_pulse"] - telemetry_before["right_pulse"]),
            }
        if command != "stop" and not settled:
            try:
                self.transport.request("POST", "/control", {"command": "stop"})
            finally:
                raise GatewayError("Ranger 完成步進後未在 2.5 秒內停止滑動")
        return response
