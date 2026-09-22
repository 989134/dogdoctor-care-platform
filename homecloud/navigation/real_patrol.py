"""Real Ranger patrol runtime using the live localization service.

Motion is disabled unless the process is launched with motion_enabled=True.
Every movement is one encoder-bounded V2 firmware step.  Localization loss,
stale telemetry, a near obstacle, or a gateway exception sends an immediate
stop and latches the mission in a recoverable state.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from navigation.core import Grid, Pose
from navigation.mission import PatrolMission, SensorSnapshot
from navigation.local_network import direct_urlopen
from navigation.ranger_patrol_adapter import GatewayError, RangerPatrolAdapter


class LocalizationServiceError(RuntimeError):
    pass


def get_json(url: str, timeout=2.0) -> dict:
    try:
        request = urllib.request.Request(url, method="GET")
        with direct_urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LocalizationServiceError(f"即時定位服務連線失敗：{exc}") from exc


def grid_from_localization(status: dict) -> Grid:
    source = status.get("map") or {}
    size = int(source.get("size", 0))
    resolution = float(source.get("resolution", 0))
    if size < 10 or not 0.01 <= resolution <= 0.5:
        raise LocalizationServiceError("定位地圖格式不完整")
    grid = Grid(size=size, resolution=resolution)
    expected_origin = tuple(float(v) for v in source.get("origin", grid.origin))
    if len(expected_origin) != 2:
        raise LocalizationServiceError("定位地圖原點格式錯誤")
    grid.origin = expected_origin
    for x, y in source.get("free", []):
        cell = grid.cell(float(x), float(y))
        if grid.inside(cell):
            grid.cells[cell[1]][cell[0]] = -5
    for x, y in source.get("occupied", []):
        cell = grid.cell(float(x), float(y))
        if grid.inside(cell):
            grid.cells[cell[1]][cell[0]] = 10
    return grid


class RealPatrolRuntime:
    def __init__(self, localization_url: str, gateway_url: str, calibration: Path,
                 motion_enabled=False, adapter=None, status_provider=None,
                 tick_seconds=.25, settle_seconds=.5,
                 localization_loss_grace_s=2.5, start_thread=True,
                 evidence_dir=Path("navigation/output")):
        self.localization_url = localization_url.rstrip("/")
        self.gateway_url = gateway_url.rstrip("/")
        self.motion_enabled = bool(motion_enabled)
        self.adapter = adapter or RangerPatrolAdapter.from_calibration_file(
            Path(calibration), base_url=self.gateway_url,
            motion_enabled=self.motion_enabled,
        )
        self.status_provider = status_provider
        self.tick_seconds = float(tick_seconds)
        self.settle_seconds = max(.25, float(settle_seconds))
        self.localization_loss_grace_s = max(0.0, float(localization_loss_grace_s))
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.mission = None
        self.latest_localization = None
        self.latest_map = {}
        self.last_error = ""
        self.last_response = None
        self.hardware_commands_sent = 0
        self.hardware_history = []
        self.mission_started_at = None
        self.evidence_dir = Path(evidence_dir)
        self.command = "stop"
        self.last_sent_command = None
        self.next_motion_at = 0.0
        self.localization_failure_since = None
        self.mission_held = False
        self.thread = None
        if start_thread:
            self.thread = threading.Thread(target=self._loop, daemon=True, name="RealPatrol")
            self.thread.start()

    def _status(self, include_map=False):
        if self.status_provider:
            status = self.status_provider()
        else:
            suffix = "?include_map=1" if include_map else "?include_map=0"
            status = get_json(self.localization_url + "/api/localization/status" + suffix)
        if not status.get("localization_available"):
            raise LocalizationServiceError(status.get("error") or "即時定位目前不可用")
        if not status.get("camera_connected") or not status.get("telemetry_connected"):
            raise LocalizationServiceError("攝影機或遙測已中斷")
        pose = status.get("pose") or {}
        if not all(isinstance(pose.get(k), (int, float)) and math.isfinite(pose[k]) for k in ("x", "y", "yaw")):
            raise LocalizationServiceError("定位座標格式錯誤")
        return status

    def preflight(self):
        errors = []
        localization = None
        gateway = None
        try:
            localization = self._status(include_map=True)
            grid = grid_from_localization(localization)
            if sum(value < 0 for row in grid.cells for value in row) < 10:
                errors.append("已知可通行地圖範圍不足")
        except (LocalizationServiceError, ValueError) as exc:
            errors.append(str(exc))
        try:
            gateway = self.adapter.preflight()
            errors.extend(gateway.get("errors", []))
        except GatewayError as exc:
            errors.append(str(exc))
        return {
            "ok": not errors, "errors": errors,
            "motion_enabled": self.motion_enabled,
            "localization": localization, "gateway": gateway,
        }

    @staticmethod
    def _snapshot(status: dict, now: float) -> SensorSnapshot:
        pose_data = status["pose"]
        age = status.get("telemetry_age_s")
        age = float(age) if isinstance(age, (int, float)) else 99.0
        front = status.get("front_m")
        front = float(front) if isinstance(front, (int, float)) else float("nan")
        speed = .05 if status.get("robot_moving") else 0.0
        return SensorSnapshot(
            now=now,
            pose=Pose(float(pose_data["x"]), float(pose_data["y"]), float(pose_data["yaw"])),
            sample_time=now - age,
            localization_confidence=float(status.get("localization_confidence", 0.0)),
            front_m=front,
            speed_mps=speed,
        )

    def start_mission(self, waypoints, route_mode="manual", hold_before_motion=False):
        status = self._status(include_map=True)
        grid = grid_from_localization(status)
        now = time.monotonic()
        pose_data = status["pose"]
        pose = Pose(float(pose_data["x"]), float(pose_data["y"]), float(pose_data["yaw"]))
        mission = PatrolMission(grid)
        mission.start(waypoints, pose, route_mode=route_mode, now=now)
        with self.lock:
            self.mission = mission
            self.latest_localization = status
            self.latest_map = status.get("map", self.latest_map)
            self.last_error = ""
            self.command = "stop"
            self.last_response = None
            self.last_sent_command = None
            self.hardware_commands_sent = 0
            self.hardware_history = []
            self.mission_started_at = datetime.now(timezone.utc).isoformat()
            self.mission_held = bool(hold_before_motion)
            if self.mission_held:
                self.mission._event(now, "mission_held", "任務已建立，等待釋放後才會移動")

    def release_hold(self):
        now = time.monotonic()
        with self.lock:
            if not self.mission:
                raise ValueError("目前沒有巡檢任務")
            if not self.mission_held:
                raise ValueError("巡檢任務目前沒有被保持")
            self.mission_held = False
            self.mission._event(now, "mission_released", "巡檢任務已釋放")

    def _record_hardware(self, command, outcome, response=None, error=""):
        localization = self.latest_localization or {}
        self.hardware_history.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "outcome": outcome,
            "response": response,
            "error": error,
            "pose": localization.get("pose"),
            "front_m": localization.get("front_m"),
            "telemetry_age_s": localization.get("telemetry_age_s"),
        })
        self.hardware_history = self.hardware_history[-500:]

    def pause(self):
        now = time.monotonic()
        with self.lock:
            if self.mission:
                self.mission.pause(now)
            self.command = "stop"
        self._safe_stop(force=True)

    def resume(self):
        status = self._status()
        pose_data = status["pose"]
        pose = Pose(float(pose_data["x"]), float(pose_data["y"]), float(pose_data["yaw"]))
        with self.lock:
            if not self.mission:
                raise ValueError("目前沒有巡檢任務")
            self.mission.resume(pose, localization_ok=True, now=time.monotonic())

    def cancel(self):
        with self.lock:
            if self.mission:
                self.mission.cancel(time.monotonic())
            self.command = "stop"
            self.mission_held = False
        self._safe_stop(force=True)

    def emergency_stop(self):
        with self.lock:
            if self.mission:
                self.mission.pause(time.monotonic())
                self.mission.reason = "緊急停止"
            self.command = "stop"
            self.mission_held = False
        self._safe_stop(force=True)

    def _safe_stop(self, force=False):
        if not self.motion_enabled:
            return
        if self.last_sent_command == "stop" and not force:
            return
        try:
            response = self.adapter.apply("stop")
            self.hardware_commands_sent += 1
            self.last_sent_command = "stop"
            self.last_response = response
            self._record_hardware("stop", "completed", response=response)
        except (GatewayError, ValueError) as exc:
            self.last_error = str(exc)
            self._record_hardware("stop", "failed", error=str(exc))

    def advance_once(self):
        try:
            now = time.monotonic()
            with self.lock:
                refresh_map = bool(
                    self.mission
                    and self.last_sent_command in {"left", "right"}
                    and now >= self.next_motion_at
                )
            status = self._status(include_map=refresh_map)
            with self.lock:
                self.last_error = ""
                self.localization_failure_since = None
                if self.motion_enabled and now < self.next_motion_at:
                    self.latest_localization = status
                    self.command = "stop"
                    return "stop"
            sample = self._snapshot(status, now)
            # A single delayed Wi-Fi/serial sample must stop motion immediately,
            # but it should pass through the runtime grace period instead of
            # letting PatrolMission permanently latch localization_lost.
            if now - sample.sample_time > .6:
                raise LocalizationServiceError("定位遙測暫時延遲")
            with self.lock:
                self.latest_localization = status
                map_source = status.get("map") or {}
                if "free" in map_source:
                    self.latest_map = map_source
                    if refresh_map and self.mission:
                        self.mission.grid = grid_from_localization(status)
                if not self.mission:
                    self.command = "stop"
                    return "stop"
                if self.mission_held:
                    self.command = "stop"
                    return "stop"
                command = self.mission.tick(sample)
                self.command = command
            if not self.motion_enabled:
                return command
            if command == "stop":
                self._safe_stop()
                return command
            response = self.adapter.apply(command, now=now)
            with self.lock:
                self.last_response = response
                self.hardware_commands_sent += 1
                self.last_sent_command = command
                self._record_hardware(command, "completed", response=response)
                self.next_motion_at = time.monotonic() + self.settle_seconds
            return command
        except (LocalizationServiceError, GatewayError, ValueError) as exc:
            failed_at = time.monotonic()
            with self.lock:
                self.last_error = str(exc)
                self.command = "stop"
                if self.localization_failure_since is None:
                    self.localization_failure_since = failed_at
                failed_for = failed_at - self.localization_failure_since
                if (
                    self.mission
                    and self.mission.state != "localization_lost"
                    and failed_for >= self.localization_loss_grace_s
                ):
                    self.mission._lose_localization(failed_at, str(exc))
            self._safe_stop()
            return "stop"

    def _loop(self):
        while not self.closed.wait(self.tick_seconds):
            self.advance_once()

    def status(self):
        with self.lock:
            mission = self.mission.status() if self.mission else {
                "state": "idle", "reason": "", "command": "stop", "target": None,
                "route": [], "current_index": 0, "path": [],
                "completed_observations": [], "replans": 0, "events": [],
            }
            localization = self.latest_localization or {}
            mission.update({
                "mode": "REAL_ARMED" if self.motion_enabled else "REAL_READ_ONLY",
                "motion_enabled": self.motion_enabled,
                "hardware_commands_sent": self.hardware_commands_sent,
                "mission_held": self.mission_held,
                "motion_settle_remaining_s": round(max(0.0, self.next_motion_at - time.monotonic()), 3),
                "localization_failure_seconds": round(
                    max(0.0, time.monotonic() - self.localization_failure_since)
                    if self.localization_failure_since is not None else 0.0, 3,
                ),
                "last_error": self.last_error,
                "last_response": self.last_response,
                "pose": localization.get("pose", {"x": 0, "y": 0, "yaw": 0}),
                "front_m": localization.get("front_m"),
                "localization_available": localization.get("localization_available", False),
                "camera_connected": localization.get("camera_connected", False),
                "telemetry_connected": localization.get("telemetry_connected", False),
                "map": self.latest_map,
                "trace": localization.get("trace", []),
            })
            return mission

    def evidence(self):
        """Return a compact, reviewable record of the current/last mission."""
        status = self.status()
        map_data = status.pop("map", {}) or {}
        status["map_summary"] = {
            "size": map_data.get("size"),
            "resolution": map_data.get("resolution"),
            "free_cells": len(map_data.get("free", [])),
            "occupied_cells": len(map_data.get("occupied", [])),
        }
        return {
            "schema": "homecloud.real_patrol.evidence.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mission_started_at": self.mission_started_at,
            "localization_url": self.localization_url,
            "gateway_url": self.gateway_url,
            "status": status,
            "hardware_history": list(self.hardware_history),
        }

    def save_evidence(self):
        report = self.evidence()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.evidence_dir / f"real_patrol_evidence_{stamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path.resolve()

    def close(self):
        self.closed.set()
        self._safe_stop(force=True)
        if self.thread:
            self.thread.join(timeout=3)
