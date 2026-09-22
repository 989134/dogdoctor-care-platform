"""Patrol mission state machine shared by simulation and future robot adapters."""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass

from navigation.core import Grid, Pose, wrap


@dataclass(frozen=True)
class Waypoint:
    id: str
    name: str
    x: float
    y: float
    observe_seconds: float = 3.0
    priority: int = 0
    yaw_deg: float | None = None

    @classmethod
    def from_value(cls, value, index=0):
        if isinstance(value, (list, tuple)) and len(value) == 3:
            value = {"x": value[0], "y": value[1], "observe_seconds": value[2]}
        if not isinstance(value, dict):
            raise ValueError("巡檢點必須是物件或 [x,y,觀察秒數]")
        waypoint = cls(
            id=str(value.get("id", f"point-{index + 1}")).strip(),
            name=str(value.get("name", f"巡檢點 {index + 1}")).strip(),
            x=float(value["x"]), y=float(value["y"]),
            observe_seconds=float(value.get("observe_seconds", 3.0)),
            priority=int(value.get("priority", 0)),
            yaw_deg=(None if value.get("yaw_deg") is None else float(value["yaw_deg"])),
        )
        if not waypoint.id or not waypoint.name:
            raise ValueError("巡檢點 id 與名稱不可空白")
        if not all(math.isfinite(v) for v in (waypoint.x, waypoint.y, waypoint.observe_seconds)):
            raise ValueError("巡檢點座標與觀察時間必須是有限數字")
        if waypoint.yaw_deg is not None and not math.isfinite(waypoint.yaw_deg):
            raise ValueError("巡檢點朝向必須是有限角度")
        if waypoint.observe_seconds < 1 or waypoint.observe_seconds > 300:
            raise ValueError("觀察時間必須介於 1 到 300 秒")
        return waypoint


@dataclass
class SensorSnapshot:
    now: float
    pose: Pose
    sample_time: float
    localization_confidence: float
    front_m: float
    speed_mps: float


def _route_distance(start: Pose, route: list[Waypoint]):
    x, y = start.x, start.y
    total = 0.0
    for point in route:
        total += math.hypot(point.x - x, point.y - y)
        x, y = point.x, point.y
    return total


def arrange_waypoints(points: list[Waypoint], start: Pose, mode: str) -> list[Waypoint]:
    if mode == "manual":
        return list(points)
    if mode != "shortest":
        raise ValueError("route_mode 必須是 manual 或 shortest")
    # Priority is a hard grouping rule; distance is optimized inside each group.
    result = []
    current = Pose(start.x, start.y, start.yaw)
    for priority in sorted({point.priority for point in points}, reverse=True):
        group = [point for point in points if point.priority == priority]
        if len(group) <= 8:
            ordered = min(itertools.permutations(group), key=lambda route: _route_distance(current, list(route)))
            group_result = list(ordered)
        else:
            group_result = []
            remaining = list(group)
            while remaining:
                nearest = min(remaining, key=lambda p: math.hypot(p.x - current.x, p.y - current.y))
                group_result.append(nearest)
                current = Pose(nearest.x, nearest.y, current.yaw)
                remaining.remove(nearest)
        result.extend(group_result)
        if group_result:
            current = Pose(group_result[-1].x, group_result[-1].y, current.yaw)
    return result


class PatrolMission:
    terminal_states = {"completed", "cancelled"}

    def __init__(self, grid: Grid, arrival_m=.08, stop_m=.36, min_confidence=.55):
        self.grid = grid
        self.arrival_m = arrival_m
        self.stop_m = stop_m
        self.min_confidence = min_confidence
        self.state = "idle"
        self.reason = ""
        self.route: list[Waypoint] = []
        self.index = 0
        self.path = []
        self.observe_until = None
        self.completed_observations = []
        self.events = []
        self.replans = 0
        self.last_command = "stop"

    def _event(self, now, kind, message):
        self.events.append({"time": round(float(now), 2), "kind": kind, "message": message})
        self.events = self.events[-100:]

    def start(self, values, start_pose: Pose, route_mode="manual", now=0.0):
        points = [Waypoint.from_value(value, i) for i, value in enumerate(values)]
        if not points:
            raise ValueError("至少需要一個巡檢點")
        if len({point.id for point in points}) != len(points):
            raise ValueError("巡檢點 id 不可重複")
        for index, point in enumerate(points):
            cell = self.grid.cell(point.x, point.y)
            if not self.grid.inside(cell):
                raise ValueError(f"巡檢點「{point.name}」超出地圖範圍")
            # Only the first destination must already be known. A later point
            # may become known after an earlier waypoint rotates the ultrasonic
            # sensor toward a new corridor. Planning still refuses to drive
            # into unknown cells when that time comes.
            if index == 0 and cell in self.grid.blocked():
                raise ValueError(f"第一個巡檢點「{point.name}」不在已知安全區域")
        self.route = arrange_waypoints(points, start_pose, route_mode)
        self.index = 0
        self.state = "navigating"
        self.reason = ""
        self.observe_until = None
        self.completed_observations = []
        self.events = []
        self.replans = 0
        self.last_command = "stop"
        self._event(now, "mission_started", f"開始巡檢，共 {len(self.route)} 點")
        if not self._plan(start_pose, now, "initial_plan"):
            raise ValueError("起點到第一個巡檢點沒有已知安全路線")

    def _plan(self, pose: Pose, now: float, event_kind="replanned") -> bool:
        if self.index >= len(self.route):
            return False
        target = self.route[self.index]
        self.path = self.grid.plan((pose.x, pose.y), (target.x, target.y))
        self.replans += 1
        if len(self.path) < 2 and math.hypot(target.x - pose.x, target.y - pose.y) >= self.arrival_m:
            self.state = "blocked"
            self.reason = "目前沒有通往巡檢點的已知安全路線"
            self.last_command = "stop"
            self._event(now, "blocked", self.reason)
            return False
        self._event(now, event_kind, f"規劃前往「{target.name}」，路徑 {len(self.path)} 格")
        return True

    def _mark_front_obstacle(self, pose: Pose, distance: float):
        distance = max(.05, min(distance, self.stop_m))
        x = pose.x + distance * math.cos(pose.yaw)
        y = pose.y + distance * math.sin(pose.yaw)
        cx, cy = self.grid.cell(x, y)
        # The range hit marks the obstacle surface. Grid.plan performs robot
        # footprint inflation, so this layer must not inflate it a second time.
        radius_cells = 1
        for dy in range(-radius_cells, radius_cells + 1):
            for dx in range(-radius_cells, radius_cells + 1):
                if math.hypot(dx, dy) <= radius_cells and self.grid.inside((cx + dx, cy + dy)):
                    self.grid.cells[cy + dy][cx + dx] = 10

    def _line_is_known_free(self, pose: Pose, target: Waypoint) -> bool:
        """Check a metric straight segment against the inflated known-free grid."""
        distance = math.hypot(target.x - pose.x, target.y - pose.y)
        if distance <= 1e-9:
            return True
        start_cell = self.grid.cell(pose.x, pose.y)
        steps = max(1, math.ceil(distance / (self.grid.resolution / 2)))
        ux = (target.x - pose.x) / distance
        uy = (target.y - pose.y) / distance
        # Sample the centre and both sides of the robot corridor. This avoids
        # rebuilding the entire inflated grid on every steering tick while
        # still rejecting unknown or occupied clearance around the body.
        offsets = (-.25, -.125, 0.0, .125, .25)
        for index in range(1, steps + 1):
            ratio = index / steps
            center_x = pose.x + (target.x - pose.x) * ratio
            center_y = pose.y + (target.y - pose.y) * ratio
            for offset in offsets:
                cell = self.grid.cell(center_x - uy * offset, center_y + ux * offset)
                if not self.grid.inside(cell):
                    return False
                if cell != start_cell and self.grid.cells[cell[1]][cell[0]] >= 0:
                    return False
        return True

    def _steer(self, pose: Pose):
        if len(self.path) < 2:
            return "stop"
        target = self.route[self.index]
        if self._line_is_known_free(pose, target):
            lookahead = (target.x, target.y)
        else:
            closest_index = min(
                range(len(self.path)),
                key=lambda index: math.hypot(self.path[index][0] - pose.x, self.path[index][1] - pose.y),
            )
            lookahead = self.path[min(closest_index + 4, len(self.path) - 1)]
        angle = wrap(math.atan2(lookahead[1] - pose.y, lookahead[0] - pose.x) - pose.yaw)
        # The measured Ranger V2 turn step is about 25 to 30 degrees.  Turning
        # for a smaller heading error overshoots a short indoor route, so keep
        # moving straight until the error reaches one physical turn step.
        if abs(angle) > .52:
            return "left" if angle > 0 else "right"
        return "forward"

    def tick(self, sample: SensorSnapshot):
        if self.state in {"idle", "paused", "completed", "cancelled", "localization_lost", "blocked"}:
            self.last_command = "stop"
            return self.last_command
        if not all(math.isfinite(v) for v in (
            sample.now, sample.pose.x, sample.pose.y, sample.pose.yaw, sample.sample_time,
            sample.localization_confidence, sample.front_m, sample.speed_mps,
        )) or sample.now < sample.sample_time or sample.now - sample.sample_time > .6:
            return self._lose_localization(sample.now, "定位資料逾時或格式錯誤")
        if sample.localization_confidence < self.min_confidence:
            return self._lose_localization(sample.now, "定位可信度過低")
        if self.index >= len(self.route):
            self.state = "completed"
            self.last_command = "stop"
            return self.last_command

        target = self.route[self.index]
        distance = math.hypot(target.x - sample.pose.x, target.y - sample.pose.y)
        if distance < self.arrival_m:
            if target.yaw_deg is not None:
                heading_error = wrap(math.radians(target.yaw_deg) - sample.pose.yaw)
                if abs(heading_error) > math.radians(15):
                    self.state = "aligning"
                    self.reason = f"在「{target.name}」調整觀察方向"
                    self.path = []
                    self.observe_until = None
                    self.last_command = "left" if heading_error > 0 else "right"
                    return self.last_command
            self.state = "observing"
            self.reason = ""
            self.path = []
            self.last_command = "stop"
            if abs(sample.speed_mps) > .025:
                self.observe_until = None
                return self.last_command
            if self.observe_until is None:
                self.observe_until = sample.now + target.observe_seconds
                self._event(sample.now, "observation_started", f"在「{target.name}」停車觀察")
            if sample.now >= self.observe_until:
                self.completed_observations.append(target.id)
                self._event(sample.now, "observation_completed", f"完成「{target.name}」觀察")
                self.index += 1
                self.observe_until = None
                if self.index >= len(self.route):
                    self.state = "completed"
                    self._event(sample.now, "mission_completed", "全部巡檢點完成")
                else:
                    self.state = "navigating"
                    self._plan(sample.pose, sample.now)
            return self.last_command

        if sample.front_m < self.stop_m:
            if self.state != "replanning":
                self._mark_front_obstacle(sample.pose, sample.front_m)
                if not self._plan(sample.pose, sample.now, "obstacle_replan"):
                    return "stop"
            command = self._steer(sample.pose)
            if command == "forward":
                self.state = "blocked"
                self.reason = "前方障礙仍阻擋目前路線"
                self._event(sample.now, "blocked", self.reason)
                self.last_command = "stop"
                return self.last_command
            self.state = "replanning"
            self.reason = "偵測到前方障礙，改走替代路線"
            self._event(sample.now, "obstacle_avoided", self.reason)
            self.last_command = command
            return command

        if not self.path or self.grid.cell(*self.path[-1]) != self.grid.cell(target.x, target.y):
            if not self._plan(sample.pose, sample.now):
                return "stop"
        self.state = "navigating"
        self.reason = ""
        self.last_command = self._steer(sample.pose)
        return self.last_command

    def _lose_localization(self, now, reason):
        self.state = "localization_lost"
        self.reason = reason
        self.observe_until = None
        self.last_command = "stop"
        self._event(now, "localization_lost", reason)
        return self.last_command

    def pause(self, now=0.0):
        if self.state in self.terminal_states or self.state == "idle":
            return
        self.state = "paused"
        self.reason = "使用者暫停"
        self.observe_until = None
        self.last_command = "stop"
        self._event(now, "paused", self.reason)

    def resume(self, pose: Pose, localization_ok=True, now=0.0):
        if self.state not in {"paused", "blocked", "localization_lost"}:
            raise ValueError("目前狀態不能恢復")
        if not localization_ok:
            raise ValueError("定位尚未恢復，不能繼續巡檢")
        self.state = "navigating"
        self.reason = ""
        self.observe_until = None
        self._event(now, "resumed", "巡檢恢復")
        self._plan(pose, now)

    def cancel(self, now=0.0):
        if self.state in self.terminal_states:
            return
        self.state = "cancelled"
        self.reason = "使用者取消"
        self.path = []
        self.observe_until = None
        self.last_command = "stop"
        self._event(now, "cancelled", self.reason)

    def status(self):
        target = asdict(self.route[self.index]) if self.index < len(self.route) else None
        return {
            "state": self.state, "reason": self.reason, "command": self.last_command,
            "target": target, "route": [asdict(point) for point in self.route],
            "current_index": self.index, "path": [[x, y] for x, y in self.path],
            "completed_observations": list(self.completed_observations),
            "replans": self.replans, "events": list(self.events),
        }
