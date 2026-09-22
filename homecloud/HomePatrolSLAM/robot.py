# robot.py
import math
import random
import pygame

# ✅ 不再強制依賴 config 裡一定要有那些變數
try:
    import config
except Exception:
    config = None

def cfg(name, default):
    if config is None:
        return default
    return getattr(config, name, default)

# ---- Robot params (with defaults) ----
ROBOT_RADIUS = int(cfg("ROBOT_RADIUS", 14))
MAX_V = float(cfg("MAX_V", 120.0))
MAX_W = float(cfg("MAX_W", 3.0))

LIDAR_RANGE = float(cfg("LIDAR_RANGE", 320.0))
LIDAR_FOV = float(cfg("LIDAR_FOV", math.radians(220)))
LIDAR_RAYS = int(cfg("LIDAR_RAYS", 55))
LIDAR_NOISE_STD = float(cfg("LIDAR_NOISE_STD", 0.6))

ODOM_V_NOISE_STD = float(cfg("ODOM_V_NOISE_STD", 0.6))
ODOM_W_NOISE_STD = float(cfg("ODOM_W_NOISE_STD", 0.02))


def wrap_pi(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


# ---------------------------
# LiDAR Ray Casting（保持用點碰撞）
# ---------------------------
def ray_cast(obstacles, x, y, ang, max_range):
    step = 3.0
    dist = 0.0
    while dist < max_range:
        px = x + dist * math.cos(ang)
        py = y + dist * math.sin(ang)

        for r in obstacles:
            if r.collidepoint(px, py):
                return dist

        dist += step
    return max_range


# ---------------------------
# 圓形碰撞（機器人移動用）
# ---------------------------
def circle_rect_collide(cx, cy, radius, rect: pygame.Rect):
    closest_x = max(rect.left, min(cx, rect.right))
    closest_y = max(rect.top,  min(cy, rect.bottom))
    dx = cx - closest_x
    dy = cy - closest_y
    return (dx * dx + dy * dy) <= radius * radius


class Robot:
    def __init__(self, x, y, theta=0.0):
        self.x = float(x)
        self.y = float(y)
        self.th = float(theta)

        # odom pose
        self.ox = float(x)
        self.oy = float(y)
        self.oth = float(theta)

        self.v = 0.0
        self.w = 0.0

    def set_cmd(self, v, w):
        self.v = max(-MAX_V, min(MAX_V, v))
        self.w = max(-MAX_W, min(MAX_W, w))

    def sense_lidar(self, obstacles):
        rays = []
        start = -LIDAR_FOV / 2
        if LIDAR_RAYS <= 1:
            angles = [0.0]
        else:
            angles = [start + i * (LIDAR_FOV / (LIDAR_RAYS - 1)) for i in range(LIDAR_RAYS)]

        for a_off in angles:
            dist = ray_cast(obstacles, self.x, self.y, self.th + a_off, LIDAR_RANGE)
            dist += random.gauss(0.0, LIDAR_NOISE_STD)
            dist = max(0.0, min(LIDAR_RANGE, dist))
            rays.append((a_off, dist))
        return rays

    def step(self, dt, obstacles):
        nx = self.x + self.v * math.cos(self.th) * dt
        ny = self.y + self.v * math.sin(self.th) * dt
        nth = wrap_pi(self.th + self.w * dt)

        # ✅ 圓形碰撞避免牆角黏住
        for r in obstacles:
            if circle_rect_collide(nx, ny, ROBOT_RADIUS, r):
                self.v = 0.0
                self.w = 0.0
                return

        self.x, self.y, self.th = nx, ny, nth

        # odom with noise
        v_noisy = self.v + random.gauss(0.0, ODOM_V_NOISE_STD)
        w_noisy = self.w + random.gauss(0.0, ODOM_W_NOISE_STD)
        self.ox += v_noisy * math.cos(self.oth) * dt
        self.oy += v_noisy * math.sin(self.oth) * dt
        self.oth = wrap_pi(self.oth + w_noisy * dt)

    def draw(self, screen):
        pygame.draw.circle(screen, (30, 144, 255), (int(self.x), int(self.y)), ROBOT_RADIUS)
        hx = self.x + ROBOT_RADIUS * 1.6 * math.cos(self.th)
        hy = self.y + ROBOT_RADIUS * 1.6 * math.sin(self.th)
        pygame.draw.line(screen, (10, 60, 120), (self.x, self.y), (hx, hy), 3)

    def draw_lidar(self, screen, rays):
        for a_off, dist in rays:
            a = self.th + a_off
            ex = self.x + dist * math.cos(a)
            ey = self.y + dist * math.sin(a)
            pygame.draw.line(screen, (255, 180, 0), (self.x, self.y), (ex, ey), 1)