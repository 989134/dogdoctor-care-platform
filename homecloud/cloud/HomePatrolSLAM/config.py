# config.py

WIN_W, WIN_H = 1100, 650     # 左側地圖 + 右側儀表板
FPS = 60

# 巡邏路線（客廳中間矩形，避開家具）
PATROL_WAYPOINTS = [
    (300, 250),
    (780, 250),
    (780, 430),
    (300, 430),
    (300, 250),
]

# 禁區（例如浴室/廚房危險區）可留空或自行加
NO_GO_ZONES = [
    # (x, y, w, h),
]

# 停留點（例如走到角落停一下掃描）
CHECKPOINT_INDICES = [0, 1, 2, 3]
CHECKPOINT_DWELL_SEC = 0.6

# 偏離路線容忍（避障繞行很正常，所以放寬）
ROUTE_DEVIATION_PX = 60.0

# 卡住偵測
STUCK_TIME_TH = 3.0
STUCK_MOVE_EPS = 0.8

# Waypoint watchdog（走不到就跳點，展示保險）
WP_TIMEOUT_SEC = 10.0
WP_NO_PROGRESS_EPS = 1.0

# Safety layer：前方距離小於這個就轉開
FRONT_AVOID_DIST = 24.0

# 回到起點算一圈
LAP_RADIUS = 30.0
ARM_DIST = 95.0

# ---- 照護監控（先用模擬）----
# 跌倒區域：你可以想像是客廳沙發旁，巡邏經過時「檢查」
FALL_ZONE = (540, 330, 120, 80)   # rect
FALL_HOLD_SEC = 6.0               # 假設倒地維持 6 秒才算事件

# 可用按鍵手動切換 "有人倒地" 事件（Demo 好用）
ENABLE_MANUAL_FALL_TOGGLE = True