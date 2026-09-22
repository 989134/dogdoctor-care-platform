"""Deterministic indoor patrol simulator for the no-hardware development stage."""

import math
from dataclasses import dataclass

from navigation.core import Grid, Pose, wrap
from navigation.mission import SensorSnapshot


def create_demo_grid():
    grid = Grid(size=80, resolution=.05)
    # Known free 4 x 4 metre room.
    for y in range(grid.size):
        for x in range(grid.size):
            wx, wy = grid.world((x, y))
            if -1.9 < wx < 1.9 and -1.9 < wy < 1.9:
                grid.cells[y][x] = -5
    # Room perimeter and a central furniture obstacle.
    for y in range(grid.size):
        for x in range(grid.size):
            wx, wy = grid.world((x, y))
            wall = (abs(wx) >= 1.88 and abs(wy) <= 1.92) or (abs(wy) >= 1.88 and abs(wx) <= 1.92)
            furniture = -.25 <= wx <= .25 and -.65 <= wy <= .65
            if wall or furniture:
                grid.cells[y][x] = 10
    return grid


DEFAULT_WAYPOINTS = [
    {"id": "living", "name": "客廳", "x": 1.25, "y": -1.20, "observe_seconds": 2, "priority": 1},
    {"id": "bathroom", "name": "浴室門口", "x": 1.25, "y": 1.20, "observe_seconds": 2, "priority": 2},
    {"id": "bedroom", "name": "臥室", "x": -1.25, "y": 1.20, "observe_seconds": 2, "priority": 1},
    {"id": "home", "name": "充電位置", "x": -1.35, "y": -1.25, "observe_seconds": 2, "priority": 0},
]


@dataclass
class PatrolSimulator:
    grid: Grid
    pose: Pose
    dt: float = .1

    def __post_init__(self):
        self.time = 0.0
        self.speed = 0.0
        self.localization_available = True
        self.trace = [[self.pose.x, self.pose.y]]
        self._occupied_cells = {
            (x, y) for y, row in enumerate(self.grid.cells) for x, value in enumerate(row) if value > 0
        }
        self._blocked_cells = self.grid.blocked(radius=.16)

    @classmethod
    def create(cls):
        return cls(create_demo_grid(), Pose(-1.35, -1.25, 0.0))

    def front_distance(self, max_range=2.0):
        for step in range(1, math.ceil(max_range / .025) + 1):
            distance = step * .025
            cell = self.grid.cell(
                self.pose.x + distance * math.cos(self.pose.yaw),
                self.pose.y + distance * math.sin(self.pose.yaw),
            )
            if not self.grid.inside(cell) or cell in self._occupied_cells:
                return distance
        return max_range

    def step(self, command):
        linear = .24 if command == "forward" else (-.18 if command == "backward" else 0.0)
        angular = .85 if command == "left" else (-.85 if command == "right" else 0.0)
        next_yaw = wrap(self.pose.yaw + angular * self.dt)
        next_x = self.pose.x + linear * self.dt * math.cos(next_yaw)
        next_y = self.pose.y + linear * self.dt * math.sin(next_yaw)
        cell = self.grid.cell(next_x, next_y)
        if linear and (not self.grid.inside(cell) or cell in self._blocked_cells):
            linear = 0.0
        else:
            self.pose.x, self.pose.y = next_x, next_y
        self.pose.yaw = next_yaw
        self.speed = linear
        self.time += self.dt
        if not self.trace or math.hypot(self.pose.x - self.trace[-1][0], self.pose.y - self.trace[-1][1]) > .015:
            self.trace.append([self.pose.x, self.pose.y])

    def snapshot(self):
        confidence = .95 if self.localization_available else .1
        sample_time = self.time if self.localization_available else self.time - 2.0
        return SensorSnapshot(
            now=self.time, pose=Pose(self.pose.x, self.pose.y, self.pose.yaw),
            sample_time=sample_time, localization_confidence=confidence,
            front_m=self.front_distance(), speed_mps=self.speed,
        )

    def inject_obstacle(self, distance=.55, radius=.22):
        x = self.pose.x + distance * math.cos(self.pose.yaw)
        y = self.pose.y + distance * math.sin(self.pose.yaw)
        cx, cy = self.grid.cell(x, y)
        cells = math.ceil(radius / self.grid.resolution)
        for dy in range(-cells, cells + 1):
            for dx in range(-cells, cells + 1):
                if math.hypot(dx, dy) <= cells and self.grid.inside((cx + dx, cy + dy)):
                    self.grid.cells[cy + dy][cx + dx] = 10
        self._blocked_cells = self.grid.blocked(radius=.16)
        self._occupied_cells = {
            (x, y) for y, row in enumerate(self.grid.cells) for x, value in enumerate(row) if value > 0
        }
