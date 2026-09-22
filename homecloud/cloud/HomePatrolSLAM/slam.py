# slam.py
import math
from config import LIDAR_RANGE

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

class OccupancyGridSLAM:
    """
    grid values: -1 unknown, 0 free, 1 occupied
    """
    def __init__(self, grid_w, grid_h, cell):
        self.W = grid_w
        self.H = grid_h
        self.cell = cell
        self.grid = [[-1 for _ in range(self.W)] for _ in range(self.H)]

    def _cell(self, x, y):
        return int(x // self.cell), int(y // self.cell)

    def _mark(self, cx, cy, val):
        if 0 <= cx < self.W and 0 <= cy < self.H:
            self.grid[cy][cx] = val

    def integrate_scan(self, pose, rays):
        """
        pose: (x, y, theta)
        rays: list of (angle_offset, distance)
        """
        x, y, th = pose
        cx0, cy0 = self._cell(x, y)

        for a_off, dist in rays:
            a = th + a_off
            dist = max(0.0, dist)
            # ray end
            ex = x + dist * math.cos(a)
            ey = y + dist * math.sin(a)
            cx1, cy1 = self._cell(ex, ey)

            # bresenham-like stepping in cells
            dx = cx1 - cx0
            dy = cy1 - cy0
            steps = max(abs(dx), abs(dy), 1)
            for i in range(steps):
                t = i / steps
                cx = int(round(cx0 + dx * t))
                cy = int(round(cy0 + dy * t))
                self._mark(cx, cy, 0)  # free along the beam

            # 只有真的撞到障礙（距離 < 最大量測）才標成 occupied
            if dist < LIDAR_RANGE * 0.98:
                self._mark(cx1, cy1, 1)

    def to_occ_binary(self):
        """
        給 planner 用的二值圖：
        - occupied(1) : grid == 1
        - free(0)     : grid == 0 或 unknown(-1)
        先讓機器人敢走，才會探索、才會建圖完整
        """
        out = [[0 for _ in range(self.W)] for _ in range(self.H)]
        for y in range(self.H):
            for x in range(self.W):
                out[y][x] = 1 if self.grid[y][x] == 1 else 0
        return out