"""Passive real-time Ranger localization and ultrasonic occupancy mapping.

Wheel encoders provide metric continuity. Monocular vision measures tracking
health and recognizes a return to the initial place. A verified return resets
the current pose to the map origin; it is not a full pose-graph optimizer.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from navigation.core import Grid, Odometry, Pose, wrap
from navigation.telemetry import PulseCalibration
from navigation.visual_slam import MonocularTracker


@dataclass
class LocalizationConfig:
    grid_size: int = 180
    grid_resolution_m: float = 0.05
    ultrasonic_max_m: float = 4.0
    ultrasonic_hit_below_m: float = 3.95
    minimum_loop_travel_m: float = 1.5
    minimum_loop_motion_steps: int = 20
    loop_cooldown_s: float = 20.0
    maximum_loop_heading_error_deg: float = 35.0
    strong_loop_min_inliers: int = 40
    strong_loop_min_inlier_ratio: float = 0.55
    # A return is accepted only after the robot has settled.  The recorded
    # Ranger route contains short pauses between correction turns; five
    # seconds prevents those pauses from being mistaken for mission end.
    loop_stationary_s: float = 5.0
    # USB serial packets are emitted every 250 ms, but the Raspberry Pi/Wi-Fi
    # path occasionally produces a single 0.7-1.0 s scheduling gap.  The
    # motion controller applies its own stricter 0.6 s stop rule.
    telemetry_stale_s: float = 1.2
    robot_free_radius_m: float = 0.13
    ultrasonic_half_angle_deg: float = 7.5
    ultrasonic_ray_count: int = 5
    # A hard flat obstacle can occasionally alternate with the sensor's 400 cm
    # maximum reading. Keep the nearest recent valid return for safety.
    front_obstacle_hold_s: float = 1.25


def load_calibration(path: Path) -> tuple[PulseCalibration, float, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"left_m_per_pulse", "right_m_per_pulse", "left_sign", "right_sign", "track_width_m"}
    if not required.issubset(data):
        raise ValueError("定位校正檔缺少編碼器換算或輪距")
    calibration = PulseCalibration(
        float(data["left_m_per_pulse"]), float(data["right_m_per_pulse"]),
        int(data["left_sign"]), int(data["right_sign"]),
    )
    track_width = float(data["track_width_m"])
    if not 0.08 <= track_width <= 0.6:
        raise ValueError("輪距不合理")
    return calibration, track_width, data


class LocalizationEngine:
    def __init__(self, calibration_path: Path, video_size=(640, 480), config=None):
        self.config = config or LocalizationConfig()
        self.pulse_calibration, track_width, self.calibration_metadata = load_calibration(calibration_path)
        self.odometry = Odometry(
            track_width,
            yaw_sign=int(self.calibration_metadata.get("yaw_sign", 1)),
            left_turn_scale=float(self.calibration_metadata.get("left_turn_yaw_scale", 1.0)),
            right_turn_scale=float(self.calibration_metadata.get("right_turn_yaw_scale", 1.0)),
        )
        self.grid = Grid(self.config.grid_size, self.config.grid_resolution_m)
        width, height = video_size
        focal = 0.8 * max(width, height)
        intrinsic = np.array(
            [[focal, 0.0, width / 2.0], [0.0, focal, height / 2.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        self.tracker = MonocularTracker(intrinsic, (width, height))
        self.raw_pose = Pose()
        self.pose = Pose()
        self.correction_yaw = 0.0
        self.correction_x = 0.0
        self.correction_y = 0.0
        self.last_telemetry_host_time = None
        self.last_controller_time_s = None
        self.last_frame_time = None
        self.front_m = None
        self.front_raw_m = None
        self.front_history = []
        self.left_pulse = None
        self.right_pulse = None
        self.robot_moving = False
        self.last_robot_motion_time = -float("inf")
        self.ultrasonic_valid = False
        self.telemetry_samples = 0
        self.vision_frames = 0
        self.vision_state = "waiting"
        self.vision_inliers = 0
        self.vision_inlier_ratio = 0.0
        self.vision_yaw = 0.0
        self.confidence = 0.0
        self.loop_closures = []
        self.last_loop_time = -float("inf")
        self.distance_m = 0.0
        self.trace = []
        self.events = []
        self._last_trace_pose = None

    def _correct_pose(self, raw: Pose) -> Pose:
        c, s = math.cos(self.correction_yaw), math.sin(self.correction_yaw)
        return Pose(
            c * raw.x - s * raw.y + self.correction_x,
            s * raw.x + c * raw.y + self.correction_y,
            wrap(raw.yaw + self.correction_yaw),
        )

    def update_telemetry(self, packet: dict, host_time_s: float) -> Pose:
        required = {"t_ms", "left_pulse", "right_pulse", "front_cm"}
        if not required.issubset(packet) or not packet.get("ok", True):
            raise ValueError("遙測資料不完整")
        left_m, right_m = self.pulse_calibration.meters(packet)
        controller_time = float(packet["t_ms"]) / 1000.0
        if self.last_controller_time_s is not None and controller_time < self.last_controller_time_s:
            self.confidence = 0.0
            raise ValueError("控制板時間倒退，可能重新啟動；定位必須重新建立基準")
        if self.last_controller_time_s is None or controller_time > self.last_controller_time_s:
            previous_raw = Pose(self.raw_pose.x, self.raw_pose.y, self.raw_pose.yaw)
            raw = self.odometry.update(controller_time, left_m, right_m)
            self.raw_pose = Pose(raw.x, raw.y, raw.yaw)
            self.distance_m += math.hypot(raw.x - previous_raw.x, raw.y - previous_raw.y)
            self.pose = self._correct_pose(self.raw_pose)
            self.last_controller_time_s = controller_time
        self.last_telemetry_host_time = float(host_time_s)
        self.telemetry_samples += 1
        new_left_pulse = int(packet["left_pulse"])
        new_right_pulse = int(packet["right_pulse"])
        encoder_changed = (
            self.left_pulse is not None
            and (
                abs(new_left_pulse - self.left_pulse) >= 2
                or abs(new_right_pulse - self.right_pulse) >= 2
            )
        )
        self.left_pulse = new_left_pulse
        self.right_pulse = new_right_pulse
        rpm_moving = (
            abs(float(packet.get("left_rpm", 0.0))) > 5.0
            or abs(float(packet.get("right_rpm", 0.0))) > 5.0
        )
        self.robot_moving = bool(packet.get("moving", False)) or rpm_moving or encoder_changed
        # Some one-second commands can finish between 250 ms telemetry packets,
        # so the board may report moving=0 even though the pulse count changed.
        if self.robot_moving or encoder_changed:
            self.last_robot_motion_time = float(host_time_s)

        front_cm = float(packet["front_cm"])
        self.ultrasonic_valid = (
            bool(packet.get("ultrasonic_valid", True))
            and 1 <= front_cm < 399.5
        )
        self.front_raw_m = front_cm / 100.0 if self.ultrasonic_valid else None
        if self.front_raw_m is not None:
            self.front_history.append((float(host_time_s), self.front_raw_m))
        cutoff = float(host_time_s) - self.config.front_obstacle_hold_s
        self.front_history = [row for row in self.front_history if row[0] >= cutoff]
        self.front_m = min((row[1] for row in self.front_history), default=None)
        if self.front_m is not None and 0 < self.front_m <= self.config.ultrasonic_max_m:
            hit = self.front_m < self.config.ultrasonic_hit_below_m
            # The robot's occupied footprint is direct evidence of free floor.
            center = self.grid.cell(self.pose.x, self.pose.y)
            radius_cells = math.ceil(self.config.robot_free_radius_m / self.grid.resolution)
            for dy in range(-radius_cells, radius_cells + 1):
                for dx in range(-radius_cells, radius_cells + 1):
                    if (
                        math.hypot(dx, dy) * self.grid.resolution <= self.config.robot_free_radius_m
                        and self.grid.inside((center[0] + dx, center[1] + dy))
                    ):
                        cy, cx = center[1] + dy, center[0] + dx
                        self.grid.cells[cy][cx] = min(-1, self.grid.cells[cy][cx] - 2)
            bearings = np.linspace(
                -math.radians(self.config.ultrasonic_half_angle_deg),
                math.radians(self.config.ultrasonic_half_angle_deg),
                self.config.ultrasonic_ray_count,
            )
            self.grid.integrate(
                self.pose,
                [
                    (float(bearing), min(self.front_m, self.config.ultrasonic_max_m), hit)
                    for bearing in bearings
                ],
                self.config.ultrasonic_max_m,
            )
        self._append_trace(host_time_s)
        return self.pose

    def _append_trace(self, host_time_s: float) -> None:
        if self._last_trace_pose is not None:
            moved = math.hypot(self.pose.x - self._last_trace_pose.x, self.pose.y - self._last_trace_pose.y)
            turned = abs(wrap(self.pose.yaw - self._last_trace_pose.yaw))
            if moved < 0.01 and turned < math.radians(2):
                return
        self.trace.append({
            "time_s": float(host_time_s), "x": self.pose.x, "y": self.pose.y, "yaw": self.pose.yaw,
        })
        self.trace = self.trace[-5000:]
        self._last_trace_pose = Pose(self.pose.x, self.pose.y, self.pose.yaw)

    def process_frame(self, frame: np.ndarray, host_time_s: float, frame_index: int) -> dict:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        row = self.tracker.process(gray, int(frame_index), float(host_time_s))
        self.last_frame_time = float(host_time_s)
        self.vision_frames += 1
        self.vision_state = row["state"]
        self.vision_inliers = int(row["inliers"])
        self.vision_inlier_ratio = float(row["inlier_ratio"])
        self.vision_yaw = math.radians(float(row["yaw_deg"]))
        telemetry_age = (
            float("inf") if self.last_telemetry_host_time is None
            else max(0.0, host_time_s - self.last_telemetry_host_time)
        )
        if telemetry_age > self.config.telemetry_stale_s:
            self.confidence = 0.1
        elif row["state"] == "tracked":
            self.confidence = min(0.99, 0.62 + 0.36 * row["inlier_ratio"])
        elif row["state"] in {"initialized", "stationary"}:
            self.confidence = 0.82
        else:
            self.confidence = 0.25 if self.tracker.consecutive_failures >= 5 else 0.55

        if (
            self.distance_m >= self.config.minimum_loop_travel_m
            and host_time_s - self.last_loop_time >= self.config.loop_cooldown_s
            and host_time_s - self.last_robot_motion_time >= self.config.loop_stationary_s
            and self.tracker.accepted_steps >= self.config.minimum_loop_motion_steps
        ):
            loop = self.tracker.final_loop_check(self.config.minimum_loop_motion_steps)
            heading_consistent = (
                abs(math.degrees(wrap(self.raw_pose.yaw)))
                <= self.config.maximum_loop_heading_error_deg
            )
            strong_visual_return = (
                loop["inliers"] >= self.config.strong_loop_min_inliers
                and loop["inlier_ratio"] >= self.config.strong_loop_min_inlier_ratio
            )
            if loop["candidate"] and (heading_consistent or strong_visual_return):
                self._apply_initial_place_closure(host_time_s, loop)
        row["localization_confidence"] = self.confidence
        row["pose"] = asdict(self.pose)
        return row

    def _apply_initial_place_closure(self, host_time_s: float, match: dict) -> None:
        raw = self.raw_pose
        correction_yaw = -raw.yaw
        c, s = math.cos(correction_yaw), math.sin(correction_yaw)
        correction_x = -(c * raw.x - s * raw.y)
        correction_y = -(s * raw.x + c * raw.y)
        before = asdict(self.pose)
        self.correction_yaw = correction_yaw
        self.correction_x = correction_x
        self.correction_y = correction_y
        self.pose = self._correct_pose(raw)
        self.last_loop_time = host_time_s
        event = {
            "time_s": host_time_s,
            "type": "initial_place_closure",
            "matches": match["matches"],
            "inliers": match["inliers"],
            "inlier_ratio": match["inlier_ratio"],
            "pose_before": before,
            "pose_after": asdict(self.pose),
        }
        self.loop_closures.append(event)
        self.events.append(event)
        self._append_trace(host_time_s)

    def status(self, now_s: float | None = None, include_map=False) -> dict:
        now_s = self.last_frame_time if now_s is None else now_s
        telemetry_age = None
        if now_s is not None and self.last_telemetry_host_time is not None:
            telemetry_age = max(0.0, now_s - self.last_telemetry_host_time)
        result = {
            "ok": self.telemetry_samples > 0,
            "mode": "PASSIVE_LOCALIZATION",
            "motion_commands_sent": 0,
            "pose": asdict(self.pose),
            "raw_pose": asdict(self.raw_pose),
            "distance_m": self.distance_m,
            "localization_confidence": self.confidence,
            "localization_available": self.confidence >= 0.55 and (
                telemetry_age is None or telemetry_age <= self.config.telemetry_stale_s
            ),
            "telemetry_age_s": telemetry_age,
            "telemetry_samples": self.telemetry_samples,
            "front_m": self.front_m,
            "front_raw_m": self.front_raw_m,
            "encoder": {"left_pulse": self.left_pulse, "right_pulse": self.right_pulse},
            "robot_moving": self.robot_moving,
            "turn_calibration": {
                "yaw_sign": self.odometry.yaw_sign,
                "left_scale": self.odometry.left_turn_scale,
                "right_scale": self.odometry.right_turn_scale,
            },
            "ultrasonic_valid": self.ultrasonic_valid,
            "vision": {
                "frames": self.vision_frames,
                "state": self.vision_state,
                "inliers": self.vision_inliers,
                "inlier_ratio": self.vision_inlier_ratio,
                "yaw": self.vision_yaw,
                "tracking_loss_events": self.tracker.loss_events,
                "accepted_motion_steps": self.tracker.accepted_steps,
            },
            "loop_closures": list(self.loop_closures),
            "trace": [[row["x"], row["y"]] for row in self.trace],
            "map": {
                "size": self.grid.size,
                "resolution": self.grid.resolution,
                "origin": list(self.grid.origin),
            },
        }
        if include_map:
            occupied = []
            free = []
            for y, values in enumerate(self.grid.cells):
                for x, value in enumerate(values):
                    if value >= 3:
                        occupied.append(list(self.grid.world((x, y))))
                    elif value < 0:
                        free.append(list(self.grid.world((x, y))))
            result["map"].update({"occupied": occupied, "free": free})
        return result
