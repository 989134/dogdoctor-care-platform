# local_test_rehab.py
import cv2
import time
import numpy as np
import mediapipe as mp
import rehab_config
import rehab_logger

# ==================== 影片/攝影機設定 ====================
USE_VIDEO_FILE = False  # True = 影片檔，False = 攝影機
VIDEO_PATH = "test_fall.mp4"  # 影片路徑
CAMERA_ID = 0  # 攝影機 ID


# ==================== 模擬 YOLO & Depth ====================
def detect_objects(_, frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([20, 100, 100])
    upper = np.array([35, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        return [(x, y, x + w, y + h, 0.95, 0)]
    return []


def estimate_depth(frame):
    detections = detect_objects(None, frame)
    if detections:
        x1, y1, x2, y2, _, _ = detections[0]
        width_px = x2 - x1
        depth_m = max(0.1, 0.4 / (width_px / 100))
        return np.full((frame.shape[0], frame.shape[1]), depth_m, dtype=np.float32)
    return np.zeros(frame.shape[:2], dtype=np.float32)


def smooth_ball_data(detections, depth_map, x_win, y_win, z_win):
    if not detections:
        return None, None, None, None
    x1, y1, x2, y2, _, _ = detections[0]
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    x_win.append(cx)
    y_win.append(cy)
    if len(x_win) > 5: x_win.pop(0); y_win.pop(0)
    avg_x, avg_y = int(sum(x_win) / len(x_win)), int(sum(y_win) / len(y_win))
    roi = depth_map[y1:y2, x1:x2]
    avg_z = float(np.mean(roi)) if roi.size else None
    z_win.append(avg_z)
    if len(z_win) > 5: z_win.pop(0)
    valid_z = [z for z in z_win if z is not None]
    avg_z_smooth = float(np.mean(valid_z)) if valid_z else None
    return avg_x, avg_y, avg_x, avg_z_smooth


# ==================== 主控制器 ====================
class RehabController:
    def __init__(self):
        self.state = "IDLE"
        self.x_win, self.y_win, self.z_win = [], [], []
        self.mp_hands = mp.solutions.hands.Hands(
            static_image_mode=False, max_num_hands=2, min_detection_confidence=0.5
        )
        self.last_target_time = 0
        self.reach_start_time = 0
        self.touch_ready = False
        self.last_point_time = 0
        rehab_logger.init_log()

    def step(self, frame):
        H, W = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        hand_results = self.mp_hands.process(rgb)

        detections = detect_objects(None, frame)
        depth_map = estimate_depth(frame)
        x1, y1, smooth_x, smooth_z = smooth_ball_data(
            detections, depth_map, self.x_win, self.y_win, self.z_win
        )
        depth_m = smooth_z

        if detections:
            x1, y1, x2, y2, conf, cls = detections[0]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(frame, f"Ball {conf:.2f}", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

        if hand_results.multi_hand_landmarks:
            for hand in hand_results.multi_hand_landmarks:
                mp.solutions.drawing_utils.draw_landmarks(
                    frame, hand, mp.solutions.hands.HAND_CONNECTIONS
                )

        mode = rehab_config.mode

        if mode == "Manual":
            cv2.putText(frame, "Mode: Manual", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return frame

        elif mode == "TouchRehab":
            cv2.putText(frame, "Mode: TouchRehab", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 165, 0), 2)

            fx, fy = None, None
            if not self.touch_ready and hand_results.multi_hand_landmarks and smooth_x is not None:
                for hand in hand_results.multi_hand_landmarks:
                    fx = int(hand.landmark[8].x * W)
                    fy = int(hand.landmark[8].y * H)
                    if abs(fx - smooth_x) < 120:
                        if time.time() - self.last_target_time > 0.5:
                            cv2.putText(frame, "✅ POINTING!", (10, 70),
                                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                            if fx is not None and fy is not None:
                                rehab_logger.log_event("target_selected", x=fx, y=fy, depth_m=depth_m)
                            self.touch_ready = True
                            self.last_target_time = time.time()
                        break

            if self.touch_ready:
                if depth_m is not None and depth_m <= 0.35:
                    cv2.putText(frame, "👉 REACH OUT & TOUCH!", (10, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

                    if hand_results.multi_hand_landmarks:
                        hand = hand_results.multi_hand_landmarks[0]
                        wrist_y = hand.landmark[0].y
                        finger_y = hand.landmark[8].y
                        palm_open = hand.landmark[4].y < hand.landmark[20].y

                        if wrist_y < 0.75 and finger_y < 0.6 and palm_open:
                            if self.reach_start_time == 0:
                                self.reach_start_time = time.time()
                                rehab_logger.log_event("reach_start", x=smooth_x, y=y1, depth_m=depth_m)

                            delay = time.time() - self.last_target_time
                            touch_error_px = abs(smooth_x - fx) if fx is not None else 0
                            rehab_logger.log_event(
                                "patient_touch",
                                x=smooth_x, y=y1, depth_m=depth_m,
                                hand_open=int(palm_open),
                                reach_delay=delay,
                                touch_error=touch_error_px,
                                success=True
                            )
                            cv2.putText(frame, "👏 YOU TOUCHED IT!", (10, 130),
                                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                            self.touch_ready = False
                            self.reach_start_time = 0

                else:
                    cv2.putText(frame, "→ Move closer...", (10, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

            return frame

        else:
            cv2.putText(frame, f"Unknown Mode: {mode}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            return frame


# ==================== 執行 ====================
if __name__ == "__main__":
    if USE_VIDEO_FILE:
        cap = cv2.VideoCapture(VIDEO_PATH)
        if not cap.isOpened():
            print(f"❌ 無法開啟影片檔：{VIDEO_PATH}")
            exit()
        fps = cap.get(cv2.CAP_PROP_FPS)
        print(f"📽️  影片模式：{VIDEO_PATH} (FPS: {fps:.1f})")
    else:
        cap = cv2.VideoCapture(CAMERA_ID)
        if not cap.isOpened():
            print("❌ 無法開啟攝影機！請檢查權限或裝置。")
            exit()
        fps = 30
        print("📷 攝影機模式")

    controller = RehabController()

    print("=" * 60)
    print("🎯【復健 Demo v2.0】影片/攝影機切換版")
    print("📌 步驟：")
    print("  1. 將黃色球放在鏡頭前（30~50cm）")
    print("  2. 伸出右手食指，指向球 → 畫面顯示 ✅ POINTING!")
    print("  3. 當出現 👉 REACH OUT & TOUCH! 時，努力伸手觸碰球")
    print("  4. 成功觸碰 → 畫面顯示 👏 YOU TOUCHED IT!，並寫入 rehab_session.csv")
    print("=" * 60)

    while True:
        ret, frame = cap.read()
        if not ret:
            if USE_VIDEO_FILE:
                print("🔄 影片播放完畢，重新開始...")
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            else:
                break

        frame = controller.step(frame)
        cv2.imshow("Rehab Test", frame)

        key = cv2.waitKey(int(1000 / fps)) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('m'):
            modes = ["Manual", "TouchRehab"]
            current = rehab_config.mode
            idx = (modes.index(current) + 1) % len(modes)
            with open("rehab_config.py", "w") as f:
                f.write(f'mode = "{modes[idx]}"\n')
            print(f"→ 切換至 {modes[idx]}")

    cap.release()
    cv2.destroyAllWindows()
    print("\n✅ 測試結束！請查看 rehab_session.csv")