# world.py
import pygame

def build_obstacles():
    # 用 pygame.Rect 表示障礙物
    obs = []

    # 牆（外框）
    obs.append(pygame.Rect(60, 60, 880, 18))   # top
    obs.append(pygame.Rect(60, 540, 880, 18))  # bottom
    obs.append(pygame.Rect(60, 60, 18, 498))   # left
    obs.append(pygame.Rect(922, 60, 18, 498))  # right

    # 客廳家具（簡單）
    obs.append(pygame.Rect(120, 120, 220, 90))   # sofa
    obs.append(pygame.Rect(640, 130, 230, 70))   # TV cabinet
    obs.append(pygame.Rect(420, 300, 180, 90))   # coffee table
    obs.append(pygame.Rect(140, 420, 180, 70))   # side cabinet

    # === inflation: give clearance to avoid scraping ===
    INFLATE = 10
    inflated = []
    for r in obs:
        rr = r.copy()
        rr.inflate_ip(INFLATE * 2, INFLATE * 2)
        inflated.append(rr)

    return inflated


    return obs

def draw_world(screen, obstacles):
    screen.fill((245, 246, 248))

    # 左側地圖背景
    pygame.draw.rect(screen, (250, 250, 250), (0, 0, 740, 650))
    pygame.draw.rect(screen, (220, 220, 220), (0, 0, 740, 650), 2)

    # 右側儀表板底
    pygame.draw.rect(screen, (255, 255, 255), (740, 0, 360, 650))
    pygame.draw.rect(screen, (220, 220, 220), (740, 0, 360, 650), 2)

    # 畫障礙物
    for r in obstacles:
        pygame.draw.rect(screen, (55, 55, 55), r, border_radius=8)
        # 淡陰影
        s = pygame.Surface((r.w, r.h), pygame.SRCALPHA)
        s.fill((0, 0, 0, 35))
        screen.blit(s, (r.x + 4, r.y + 4))
        pygame.draw.rect(screen, (70, 70, 70), r, border_radius=8)