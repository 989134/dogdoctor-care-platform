# main.py
import os
import math
import time
import pygame
import config
from world import build_obstacles, draw_world
from robot import Robot

def ensure_dir(path: str):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def ts_str():
    return time.strftime("%Y%m%d_%H%M%S")

def wrap_pi(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a

def point_segment_distance(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    wx, wy = px - x1, py - y1
    vv = vx * vx + vy * vy
    if vv < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = (wx * vx + wy * vy) / vv
    t = max(0.0, min(1.0, t))
    projx = x1 + t * vx
    projy = y1 + t * vy
    return math.hypot(px - projx, py - projy)

def min_distance_to_polyline(px, py, pts):
    if len(pts) < 2:
        return 1e9
    dmin = 1e9
    for i in range(len(pts) - 1):
        dmin = min(dmin, point_segment_distance(px, py, pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]))
    return dmin

def follow_waypoints(robot, wps, idx):
    tx, ty = wps[idx]
    dx, dy = tx - robot.x, ty - robot.y
    dist = math.hypot(dx, dy)

    if dist < 30:
        idx = (idx + 1) % len(wps)

    tx, ty = wps[idx]
    dx, dy = tx - robot.x, ty - robot.y
    ang_to = math.atan2(dy, dx)
    ang_err = wrap_pi(ang_to - robot.th)

    if abs(ang_err) > 0.60:
        v = 0.0
        w = 2.0 * ang_err
    else:
        v = 115.0
        w = 1.5 * ang_err

    robot.set_cmd(v, w)
    return idx

def main():
    pygame.init()
    screen = pygame.display.set_mode((config.WIN_W, config.WIN_H))
    pygame.display.set_caption("Known-Map Home Patrol (Fixed Route + Care Monitor)")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 18)

    obstacles = build_obstacles()
    wps = config.PATROL_WAYPOINTS
    robot = Robot(wps[0][0], wps[0][1], theta=0.0)

    # outputs
    out_dir = "patrol_outputs"
    ensure_dir(out_dir)
    session_id = ts_str()
    log_path = os.path.join(out_dir, f"patrol_log_{session_id}.txt")
    summary_path = os.path.join(out_dir, f"patrol_summary_{session_id}.txt")

    def log_line(msg):
        t_sec = pygame.time.get_ticks() / 1000.0
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{t_sec:8.2f}s] {msg}\n")

    # KPI
    mission_start_ms = pygame.time.get_ticks()
    lap_count = 0
    last_lap_ms = mission_start_ms
    lap_time_sum = 0.0

    stuck_count = 0
    wp_skip_count = 0
    dev_count = 0
    nogo_count = 0
    checkpoint_count = 0

    # lap detection
    START_PT = wps[0]
    lap_armed = False

    # modes
    mode = "NORMAL"
    wp_i = 0

    # checkpoint dwell
    dwell_timer = 0.0
    dwell_at = -1

    # recovery
    rec_timer = 0.0
    BACK_TIME = 0.7
    ROT_TIME = 0.9

    # monitoring
    last_pos = (robot.x, robot.y)
    stuck_timer = 0.0

    dev_timer = 0.0
    DEV_TIME_TH = 1.0

    # watchdog
    wp_enter_ms = pygame.time.get_ticks()
    wp_best_dist = 1e9

    def watchdog_reset():
        nonlocal wp_enter_ms, wp_best_dist
        wp_enter_ms = pygame.time.get_ticks()
        wp_best_dist = 1e9

    # alerts
    last_msg = ""
    msg_hold = 0.0

    # speed demo
    time_scale = 1.0
    MAX_SUBSTEP = 1.0 / 90.0

    # no-go zones
    no_go = [pygame.Rect(*z) for z in config.NO_GO_ZONES]

    # fall zone
    fall_zone = pygame.Rect(*config.FALL_ZONE)
    fall_manual = False
    fall_hold = 0.0
    fall_detected = False
    fall_events = 0

    log_line("SYSTEM: start known-map patrol")

    running = True
    while running:
        # events
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            if e.type == pygame.KEYDOWN:
                if e.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                if e.key == pygame.K_r:
                    robot.x, robot.y = START_PT
                    robot.th = 0.0
                    wp_i = 0
                    mode = "NORMAL"
                    dwell_timer = 0.0
                    rec_timer = 0.0
                    watchdog_reset()
                    lap_armed = False
                    last_msg = "Reset patrol"
                    msg_hold = 2.0
                    log_line("SYSTEM: reset (R)")
                if e.key == pygame.K_f:
                    time_scale = 4.0 if time_scale != 4.0 else 1.0
                    last_msg = f"time_scale={time_scale}x"
                    msg_hold = 1.5
                if e.key == pygame.K_g:
                    time_scale = 8.0 if time_scale != 8.0 else 1.0
                    last_msg = f"time_scale={time_scale}x"
                    msg_hold = 1.5
                if config.ENABLE_MANUAL_FALL_TOGGLE and e.key == pygame.K_h:
                    fall_manual = not fall_manual
                    last_msg = f"Manual FALL toggle = {fall_manual}"
                    msg_hold = 2.0
                    log_line(f"FALL: manual_toggle -> {fall_manual}")

        real_dt = clock.tick(config.FPS) / 1000.0
        sim_dt_total = real_dt * time_scale

        while sim_dt_total > 1e-9:
            dt = min(MAX_SUBSTEP, sim_dt_total)
            sim_dt_total -= dt

            msg_hold = max(0.0, msg_hold - dt)

            # sense lidar
            rays = robot.sense_lidar(obstacles)

            # safety layer (front avoid)
            front = [d for (a, d) in rays if abs(a) < 0.35]
            left_side = [d for (a, d) in rays if 0.35 <= a <= 1.2]
            right_side = [d for (a, d) in rays if -1.2 <= a <= -0.35]

            front_min = min(front) if front else 9999.0
            lmin = min(left_side) if left_side else 9999.0
            rmin = min(right_side) if right_side else 9999.0

            # 只要任一方向太近，都先轉開（避免擦邊卡死）
            near = min(front_min, lmin, rmin)

            if near < config.FRONT_AVOID_DIST:
                # 朝空的那側轉
                turn = 2.2 if lmin > rmin else -2.2
                robot.set_cmd(0.0, turn)
            else:
                prev = wp_i
                wp_i = follow_waypoints(robot, wps, wp_i)
                ...
            # stuck detect
            moved = math.hypot(robot.x - last_pos[0], robot.y - last_pos[1])
            if moved < config.STUCK_MOVE_EPS:
                stuck_timer += dt
            else:
                stuck_timer = 0.0
            last_pos = (robot.x, robot.y)

            if mode == "NORMAL" and stuck_timer > config.STUCK_TIME_TH:
                mode = "REC_BACK"
                rec_timer = BACK_TIME
                stuck_count += 1
                stuck_timer = 0.0
                last_msg = "[ALERT] STUCK -> recovery"
                msg_hold = 2.5
                log_line("ALERT: stuck -> recovery")

            # deviation
            d_dev = min_distance_to_polyline(robot.x, robot.y, wps)
            if d_dev > config.ROUTE_DEVIATION_PX and mode == "NORMAL":
                dev_timer += dt
            else:
                dev_timer = 0.0
            if dev_timer > DEV_TIME_TH and mode == "NORMAL":
                dev_count += 1
                dev_timer = 0.0
                last_msg = "[INFO] Route deviation"
                msg_hold = 1.8
                log_line(f"INFO: deviation d={d_dev:.1f}")

            # fall monitor (known zone check)
            # 你可以把它想成巡邏到 fall_zone 就用攝影機做「倒地判斷」
            is_in_fall_area = fall_zone.collidepoint(robot.x, robot.y)
            if fall_manual or is_in_fall_area:
                fall_hold += dt
            else:
                fall_hold = max(0.0, fall_hold - 2.0 * dt)

            if (not fall_detected) and fall_hold >= config.FALL_HOLD_SEC:
                fall_detected = True
                fall_events += 1
                last_msg = "[ALERT] FALL DETECTED!"
                msg_hold = 3.0
                log_line("ALERT: fall_detected")

            # controller
            if mode == "DWELL":
                robot.set_cmd(0.0, 0.0)
                dwell_timer -= dt
                if dwell_timer <= 0.0:
                    mode = "NORMAL"
                    dwell_at = -1

            elif mode == "REC_BACK":
                robot.set_cmd(-70.0, 0.0)
                rec_timer -= dt
                if rec_timer <= 0.0:
                    mode = "REC_ROT"
                    rec_timer = ROT_TIME

            elif mode == "REC_ROT":
                robot.set_cmd(0.0, 2.2)
                rec_timer -= dt
                if rec_timer <= 0.0:
                    mode = "NORMAL"

            else:
                # NORMAL
                if front_min < config.FRONT_AVOID_DIST:
                    # rotate to freer side
                    left = [d for (a, d) in rays if 0.35 <= a <= 1.2]
                    right = [d for (a, d) in rays if -1.2 <= a <= -0.35]
                    lmin = min(left) if left else 9999.0
                    rmin = min(right) if right else 9999.0
                    turn = 2.2 if lmin > rmin else -2.2
                    robot.set_cmd(0.0, turn)
                else:
                    prev = wp_i
                    wp_i = follow_waypoints(robot, wps, wp_i)
                    if wp_i != prev:
                        watchdog_reset()
                        if prev in config.CHECKPOINT_INDICES:
                            mode = "DWELL"
                            dwell_timer = config.CHECKPOINT_DWELL_SEC
                            dwell_at = prev
                            checkpoint_count += 1
                            last_msg = f"[CHECK] checkpoint idx={prev}"
                            msg_hold = 1.2
                            log_line(f"CHECKPOINT: idx={prev}")

            # watchdog
            tx, ty = wps[wp_i]
            dist_to_wp = math.hypot(robot.x - tx, robot.y - ty)
            wp_best_dist = min(wp_best_dist, dist_to_wp)
            elapsed = (pygame.time.get_ticks() - wp_enter_ms) / 1000.0
            no_progress = (wp_best_dist - dist_to_wp) < config.WP_NO_PROGRESS_EPS

            if mode == "NORMAL" and elapsed > config.WP_TIMEOUT_SEC and no_progress:
                wp_i = (wp_i + 1) % len(wps)
                watchdog_reset()
                wp_skip_count += 1
                last_msg = "[INFO] WP timeout -> skip"
                msg_hold = 1.6
                log_line(f"INFO: wp_skip -> wp_i={wp_i}")
                mode = "REC_ROT"
                rec_timer = 0.45

            # physics step
            robot.step(dt, obstacles)

            # lap detection
            dist_to_start = math.hypot(robot.x - START_PT[0], robot.y - START_PT[1])
            if not lap_armed:
                if dist_to_start > config.ARM_DIST:
                    lap_armed = True
            else:
                if dist_to_start < config.LAP_RADIUS:
                    lap_count += 1
                    lap_armed = False
                    now_ms = pygame.time.get_ticks()
                    lap_time = (now_ms - last_lap_ms) / 1000.0
                    lap_time_sum += lap_time
                    last_lap_ms = now_ms
                    log_line(f"LAP_DONE: lap={lap_count} lap_time={lap_time:.2f}s")

        # draw
        draw_world(screen, obstacles)

        # draw fall zone
        pygame.draw.rect(screen, (220, 70, 70), fall_zone, 2)
        s = pygame.Surface((fall_zone.w, fall_zone.h), pygame.SRCALPHA)
        s.fill((220, 70, 70, 35))
        screen.blit(s, (fall_zone.x, fall_zone.y))

        # route polyline
        for i in range(len(wps) - 1):
            pygame.draw.line(screen, (0, 180, 120), wps[i], wps[i+1], 3)
            pygame.draw.circle(screen, (150, 150, 150), (int(wps[i][0]), int(wps[i][1])), 6)
        pygame.draw.circle(screen, (80, 80, 80), (int(wps[0][0]), int(wps[0][1])), 9, 2)
        pygame.draw.circle(screen, (220, 80, 80), (int(wps[wp_i][0]), int(wps[wp_i][1])), 8)

        # no-go zones
        for z in no_go:
            pygame.draw.rect(screen, (200, 60, 60), z, 2)

        robot.draw_lidar(screen, rays)
        robot.draw(screen)

        # KPI dashboard
        mission_time = (pygame.time.get_ticks() - mission_start_ms) / 1000.0
        avg_lap = (lap_time_sum / lap_count) if lap_count > 0 else 0.0

        # product-oriented health
        if stuck_count > 0:
            health = "WARNING"
        elif nogo_count > 0:
            health = "ATTENTION"
        elif (wp_skip_count > 12 or dev_count > 15):
            health = "ATTENTION"
        else:
            health = "OK"

        lines = [
            "=== Known-Map Patrol KPI ===",
            f"time_scale: {time_scale}x   (F=4x, G=8x)",
            f"mission_time: {mission_time:.1f}s",
            f"lap_count: {lap_count}",
            f"avg_lap_time: {avg_lap:.2f}s",
            "",
            f"health: {health}",
            f"mode: {mode}",
            f"wp: {wp_i+1}/{len(wps)}",
            f"front_min: {front_min:.1f}",
            f"wp_skip: {wp_skip_count}",
            f"deviation: {dev_count}",
            f"checkpoints: {checkpoint_count}",
            f"stuck: {stuck_count}",
            f"no_go: {nogo_count}",
            "",
            "=== Care Monitor ===",
            f"FALL zone hold: {fall_hold:.1f}/{config.FALL_HOLD_SEC:.1f}s",
            f"FALL detected: {'YES' if fall_detected else 'NO'}",
            f"FALL events: {fall_events}",
            "",
            "Keys: R reset | H fall toggle | S not used",
            "Q/ESC quit",
        ]

        y = 30
        for t in lines:
            screen.blit(font.render(t, True, (20, 20, 20)), (760, y))
            y += 24

        if msg_hold > 0 and last_msg:
            pygame.draw.rect(screen, (255, 245, 245), (748, 560, 344, 76), border_radius=12)
            pygame.draw.rect(screen, (200, 60, 60), (748, 560, 344, 76), 3, border_radius=12)
            screen.blit(font.render(last_msg, True, (160, 40, 40)), (760, 588))

        pygame.display.flip()

    # summary
    mission_time = (pygame.time.get_ticks() - mission_start_ms) / 1000.0
    avg_lap = (lap_time_sum / lap_count) if lap_count > 0 else 0.0

    if stuck_count > 0:
        health = "WARNING"
    elif nogo_count > 0:
        health = "ATTENTION"
    elif (wp_skip_count > 12 or dev_count > 15):
        health = "ATTENTION"
    else:
        health = "OK"

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("=== Known-Map Patrol Mission Summary ===\n")
        f.write(f"Session ID: {session_id}\n")
        f.write(f"Total mission time: {mission_time:.2f} sec\n")
        f.write(f"Lap count: {lap_count}\n")
        f.write(f"Average lap time: {avg_lap:.2f} sec\n\n")
        f.write("--- Event Statistics ---\n")
        f.write(f"Stuck events: {stuck_count}\n")
        f.write(f"Waypoint skip events: {wp_skip_count}\n")
        f.write(f"No-go events: {nogo_count}\n")
        f.write(f"Route deviation events: {dev_count}\n")
        f.write(f"Checkpoint count: {checkpoint_count}\n\n")
        f.write("--- Care Monitor ---\n")
        f.write(f"Fall detected: {fall_detected}\n")
        f.write(f"Fall events: {fall_events}\n")
        f.write(f"Fall zone: {tuple(config.FALL_ZONE)}\n")
        f.write(f"Fall hold threshold: {config.FALL_HOLD_SEC:.1f} sec\n\n")
        f.write(f"System health evaluation: {health}\n")

    pygame.quit()


if __name__ == "__main__":
    main()