# local_test.py
import cv2
import time
import numpy as np
import mediapipe as mp
from enum import Enum

# === 匯入你現有的模組（若路徑不對請調整）===
try:
    from eldertest.depth_model import load_depth_anything, estimate_depth
    from yolo_model import load_yolo, detect_objects
    from draw_yolo_detections import draw_yolo_detections
    from smooth_ball_data import smooth_ball_data
    from move_decision_raw import move_decision_raw
    import shared_config
except ImportError as e:
    print(f"[警告] 模組載入失敗: {e} —— 我會用最小模擬版代替")

# ===== 最小模擬版（若真實模組不存在）=====
class RobotState(Enum):
    IDLE = 0
    SEARCH_AND_MOVE = 1
    PICK_UP = 2
    RETURN_BALL = 3
    WAITING = 4

def load_yolo():
    return "dummy_yolo"

def detect_objects(model, frame):
    # 模擬：若畫面中有黃色區域（HSV），就當作「乒乓球」
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([20, 100, 100])
    upper = np.array([35, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        return [(x, y, x+w, y+h, 0.95, 0)]  # (x1,y1,x2,y2,conf,class_id)
    return []

def estimate_depth(frame):
    # 模擬深度：用 YOLO 檢測到的寬度反推距離（越寬越近）
    detections = detect_objects(None, frame)

    if detections:
        x1, y1, x2, y2, _, _ = detections[0]
        width_px = x2 - x1
        # 假設真實球直徑 4cm → 距離 ≈ 200 / width_px （經驗公式）
        raw_depth = int(200 / max(width_px, 1))
        return np.full((frame.shape[0], frame.shape[1]), raw_depth, dtype=np.uint8)
    return np.zeros(frame.shape[:2], dtype=np.uint8)



def smooth_ball_data(detections, depth_map, x_win, y_win, z_win):
    if not detections:
        return None, None, None, None
    x1, y1, x2, y2, _, _ = detections[0]
    smooth_x, smooth_y = (x1+x2)//2, (y1+y2)//2
    x_win.append(smooth_x); y_win.append(smooth_y)
    dis = int(np.mean(depth_map[y1:y2, x1:x2])) if depth_map.size else None
    z_win.append(dis)
    return smooth_x, smooth_y, int(np.mean(x_win)), int(np.mean(z_win))

# ===== 主類別：精簡版 StateMachineController =====
class LocalStateMachine:
    def __init__(self):
        self.state = RobotState.IDLE
        self.x_window = []  # 模擬 deque
        self.y_window = []
        self.z_window = []
        self.mp_hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=2, min_detection_confidence=0.5)
        self.last_touch_time = 0
        self.log_file = "rehab_local_log.csv"
        with open(self.log_file, "w") as f:
            f.write("timestamp,mode,state,event,distance,x_center\n")

    def _log(self, event, distance=None, x=None):
        import datetime
        mode = shared_config.mode
        with open(self.log_file, "a") as f:
            f.write(f"{datetime.datetime.now()},{mode},{self.state.name},{event},{distance},{x}\n")

    def step(self, frame):
        H, W = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        hand_results = self.mp_hands.process(rgb)

        # YOLO + Depth
        detections = detect_objects(None, frame)
        depth_map = estimate_depth(frame)
        x1, y1, smooth_x, smooth_z = smooth_ball_data(
            detections, depth_map, self.x_window, self.y_window, self.z_window
        )
        print(f"[DEBUG] Detections: {detections}")
        dis = smooth_z

        # 畫圖
        frame = draw_yolo_detections(frame, detections, {0: "Ball"})
        if hand_results.multi_hand_landmarks:
            for hand in hand_results.multi_hand_landmarks:
                mp.solutions.drawing_utils.draw_landmarks(frame, hand, mp.solutions.hands.HAND_CONNECTIONS)

        # === 模式切換（手動改 shared_config.py 即可）===
        mode = shared_config.mode

        if mode == "Manual":
            cv2.putText(frame, "Mode: Manual", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)
            return frame

        elif mode == "TouchRehab":
            cv2.putText(frame, "Mode: TouchRehab", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,165,0), 2)
            CENTER_TOL = 50
            TOUCH_THRESHOLD = 200  # raw_depth < 200 ≈ 距離 < 30cm

            if dis is None:
                cv2.putText(frame, "Searching...", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 2)
                self.state = RobotState.SEARCH_AND_MOVE
                return frame

            # 對準中心
            center_x = W // 2
            if smooth_x < center_x - CENTER_TOL:
                cv2.putText(frame, "→ Move Left", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
            elif smooth_x > center_x + CENTER_TOL:
                cv2.putText(frame, "→ Move Right", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
            else:
                # 已對準 → 檢查距離
                if dis < TOUCH_THRESHOLD:
                    cv2.putText(frame, "✅ TOUCH READY!", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
                    if time.time() - self.last_touch_time > 2.0:  # 防抖
                        self._log("touch_start", dis, smooth_x)
                        self.last_touch_time = time.time()
                else:
                    cv2.putText(frame, f"→ Move Forward (dist={dis})", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
            # 在 step() 的最後（return frame 前）
            if dis is not None:
                cv2.putText(frame, f"Dist: {dis:.2f}m", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            return frame

        elif mode == "GazeNavRehab":
            cv2.putText(frame, "Mode: GazeNavRehab", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,128,255), 2)
            if hand_results.multi_hand_landmarks:
                for hand in hand_results.multi_hand_landmarks:
                    x_px = int(hand.landmark[8].x * W)  # 食指尖
                    y_px = int(hand.landmark[8].y * H)
                    cv2.circle(frame, (x_px, y_px), 10, (255,0,0), -1)
                    if x_px > W * 0.6:
                        cv2.putText(frame, "→ Turn Right", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,128,255), 2)
                        self._log("gaze_right", value=x_px)
                    elif x_px < W * 0.4:
                        cv2.putText(frame, "→ Turn Left", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,128,255), 2)
                        self._log("gaze_left", value=x_px)
            else:
                cv2.putText(frame, "Point with finger!", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,128,255), 2)
            return frame

        else:
            cv2.putText(frame, f"Unknown Mode: {mode}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)
            return frame

# ===== 執行主迴圈 =====
if __name__ == "__main__":
    cap = cv2.VideoCapture(0)  # 使用筆電鏡頭
    if not cap.isOpened():
        print("❌ 無法開啟攝影機！請檢查權限或裝置。")
        exit()

    controller = LocalStateMachine()
    print("✅ 啟動本地測試模式")
    print("📌 請將「黃色物體」（如檸檬、黃色紙片）放在鏡頭前")
    print("📌 修改 shared_config.py 的 mode 為 'TouchRehab' 或 'GazeNavRehab' 來切換模式")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = controller.step(frame)
        cv2.imshow("Rehab Test", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('m'):
            # 快速切換模式（僅供測試）
            modes = ["Manual", "TouchRehab", "GazeNavRehab"]
            current = shared_config.mode
            idx = (modes.index(current) + 1) % len(modes)
            with open("shared_config.py", "w") as f:
                f.write(f'mode = "{modes[idx]}"\n')
            print(f"→ 切換至 {modes[idx]}")

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n📊 測試完成！數據已儲存於：{controller.log_file}")