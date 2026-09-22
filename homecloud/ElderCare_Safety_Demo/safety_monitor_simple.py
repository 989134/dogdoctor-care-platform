# safety_monitor_simple.py
import cv2
import time
import numpy as np
import mediapipe as mp
import safety_config
import safety_logger

# ==================== 初始化 MediaPipe Pose ====================
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(
    static_image_mode=False,
    model_complexity=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
mp_drawing = mp.solutions.drawing_utils

# ==================== 設定 ====================
# 視頻源設定
USE_VIDEO_FILE = True  # False = 筆電鏡頭，True = 影片檔
VIDEO_PATH = "1.mp4"
CAMERA_ID = 0

# 隱私模式（只顯示骨架，不顯示真實影像）
PRIVACY_MODE =False

# 跌倒偵測參數
STILL_THRESHOLD = 0.03
IMMOBILE_LIMIT = 10
SEATING_AREA = [0.2, 0.8, 0.3, 0.9]
FALL_THRESHOLD_RATIO = 0.6


# ==================== 狀態變數 ====================
class MonitorState:
    def __init__(self):
        self.is_fallen = False
        self.fall_count = 0
        self.last_pos = None
        self.still_start_time = None
        self.prev_nose_y = 0
        self.last_move_time = time.time()
        self.status = "normal"  # normal, warning, alert
        self.immobility_timer = 0
        self.in_seat = True


state = MonitorState()


# ==================== 核心函數 ====================

def check_fall_logic(landmarks, h, w):
    """進階跌倒偵測邏輯"""
    if not landmarks:
        return False, 0.0, False

    try:
        nose = landmarks[mp_pose.PoseLandmark.NOSE.value]
        shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
        hip = landmarks[mp_pose.PoseLandmark.LEFT_HIP.value]
        knee = landmarks[mp_pose.PoseLandmark.LEFT_KNEE.value]

        # 條件 1: 身體扁平化
        flat_body = abs(shoulder.y - knee.y) < 0.15

        # 條件 2: 鼻子低於膝蓋
        nose_lower_than_knee = nose.y > knee.y

        # 條件 3: 鼻子低於臀部
        hip_y = (landmarks[mp_pose.PoseLandmark.LEFT_HIP.value].y +
                 landmarks[mp_pose.PoseLandmark.RIGHT_HIP.value].y) / 2
        nose_below_hip = nose.y > hip_y

        # 綜合判定
        is_fallen = flat_body or nose_lower_than_knee or nose_below_hip
        confidence = (float(flat_body) + float(nose_lower_than_knee) + float(nose_below_hip)) / 3

        return is_fallen, confidence, flat_body or nose_lower_than_knee
    except Exception as e:
        print(f"❌ 跌倒檢測錯誤：{e}")
        return False, 0.0, False


def check_seating_area(hip_x, hip_y):
    """座位區判定"""
    return (SEATING_AREA[0] < hip_x < SEATING_AREA[1] and
            SEATING_AREA[2] < hip_y < SEATING_AREA[3])


def check_immobility(current_pos, is_suspicious):
    """長時間不動判定"""
    if state.last_pos is None:
        state.last_pos = current_pos
        return False, 0

    dist = ((current_pos[0] - state.last_pos[0]) ** 2 +
            (current_pos[1] - state.last_pos[1]) ** 2) ** 0.5

    if is_suspicious and dist < STILL_THRESHOLD:
        if state.still_start_time is None:
            state.still_start_time = time.time()

        elapsed = time.time() - state.still_start_time
        return elapsed > IMMOBILE_LIMIT, elapsed
    else:
        if dist > STILL_THRESHOLD or not is_suspicious:
            state.still_start_time = None
        return False, 0


def open_video_source():
    """統一開啟視頻源"""
    if USE_VIDEO_FILE:
        cap = cv2.VideoCapture(VIDEO_PATH)
        if not cap.isOpened():
            print(f"❌ 無法開啟影片檔：{VIDEO_PATH}")
            return None
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        print(f"📽️  影片已載入：{VIDEO_PATH}")
        print(f"📊 FPS: {fps:.1f} | 總幀數：{frame_count} | 時長：{duration:.1f}秒")
        return cap, fps
    else:
        cap = cv2.VideoCapture(CAMERA_ID)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not cap.isOpened():
            print("❌ 無法開啟攝影機！")
            return None
        print("📷 攝影機已開啟")
        return cap, 30


def draw_frame(frame, results, status_msg, alert_color):
    """繪製顯示畫面"""
    h, w, _ = frame.shape

    # 隱私模式：黑色背景 + 骨架
    if PRIVACY_MODE:
        display_frame = np.zeros_like(frame)
    else:
        display_frame = frame.copy()

    # 繪製骨架
    if results.pose_landmarks:
        mp_drawing.draw_landmarks(
            display_frame,
            results.pose_landmarks,
            mp_pose.POSE_CONNECTIONS,
            mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=2),
            mp_drawing.DrawingSpec(color=(255, 0, 0), thickness=2, circle_radius=2)
        )

    # 繪製座位區
    overlay = display_frame.copy()
    cv2.rectangle(overlay,
                  (int(SEATING_AREA[0] * w), int(SEATING_AREA[2] * h)),
                  (int(SEATING_AREA[1] * w), int(SEATING_AREA[3] * h)),
                  (0, 255, 255), 2)
    cv2.addWeighted(overlay, 0.3, display_frame, 0.7, 0, display_frame)
    cv2.putText(display_frame, "SAFE_ZONE",
                (int(SEATING_AREA[0] * w) + 10, int(SEATING_AREA[2] * h) + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    # 繪製狀態資訊
    cv2.putText(display_frame, status_msg, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, alert_color, 2)

    # 繪製 KPI
    cv2.putText(display_frame, f"Height: {state.immobility_timer}s", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(display_frame, f"Seat: {'IN' if state.in_seat else 'OUT'}", (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(display_frame, f"Privacy: {'ON' if PRIVACY_MODE else 'OFF'}", (10, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # 繪製警報覆蓋層
    if state.status == "alert":
        overlay = display_frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, h), (0, 0, 255), -1)
        cv2.addWeighted(overlay, 0.3, display_frame, 0.7, 0, display_frame)
        cv2.putText(display_frame, "⚠️ FALL ALERT!", (w // 2 - 150, h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)

    return display_frame


# ==================== 主循環 ====================

def main():
    safety_logger.init_log()

    # 開啟視頻源
    result = open_video_source()
    if result is None:
        return

    cap, fps = result
    frame_delay = int(1000 / fps) if fps > 0 else 33

    print("=" * 60)
    print("🛡️  居家安全監控系統 (簡化版)")
    print("=" * 60)
    print("📌 操作說明：")
    print("  1. 按 'p' 切換 隱私模式")
    print("  2. 按 's' 截圖")
    print("  3. 按 'q' 退出")
    print("=" * 60)

    last_emit_time = time.time()
    frame_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            if USE_VIDEO_FILE:
                print("🔄 影片播放完畢，重新開始...")
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            else:
                break

        frame_count += 1
        h, w, _ = frame.shape
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        current_time = time.time()
        if current_time - last_emit_time >= 0.2:  # 5 FPS 更新

            status_msg = "Status: Normal"
            alert_color = (0, 255, 0)
            is_suspicious = False

            if results.pose_landmarks:
                landmarks = results.pose_landmarks.landmark

                nose = landmarks[mp_pose.PoseLandmark.NOSE.value]
                shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                hip = landmarks[mp_pose.PoseLandmark.LEFT_HIP.value]
                knee = landmarks[mp_pose.PoseLandmark.LEFT_KNEE.value]

                # 跌倒判定
                is_fallen, confidence, is_suspicious = check_fall_logic(landmarks, h, w)

                # 長時間不動判定
                current_pos = (hip.x, hip.y)
                is_immobile, elapsed_time = check_immobility(current_pos, is_suspicious)
                state.immobility_timer = int(elapsed_time)

                # 座位區判定
                state.in_seat = check_seating_area(hip.x, hip.y)

                # 狀態更新
                if is_immobile:
                    state.status = "alert"
                    status_msg = f"⚠️ ALERT: FALLEN {int(elapsed_time)}s!"
                    alert_color = (0, 0, 255)
                    safety_logger.log_event("FALL_IMMOBILE", confidence=confidence,
                                            action_taken="Alert Sent")
                    print(f"⚠️ 跌倒警報！可信度：{confidence:.2f}, 靜止：{int(elapsed_time)}秒")
                elif is_suspicious:
                    state.status = "warning"
                    status_msg = "⚠️ WARNING: UNUSUAL POSTURE"
                    alert_color = (0, 165, 255)
                else:
                    state.status = "normal"
                    status_msg = "Status: Normal"
                    alert_color = (0, 255, 0)

                if not is_suspicious and state.still_start_time:
                    state.still_start_time = None

            # 繪製畫面
            display_frame = draw_frame(frame, results, status_msg, alert_color)

            # 顯示 FPS
            current_fps = frame_count / (current_time - last_emit_time) if current_time > last_emit_time else 0
            cv2.putText(display_frame, f"FPS: {current_fps:.1f}", (w - 120, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # 顯示視窗
            cv2.imshow("Safety Monitor", display_frame)

            # 按鍵處理
            key = cv2.waitKey(frame_delay) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('p'):  # 切換隱私模式
                global PRIVACY_MODE
                PRIVACY_MODE = not PRIVACY_MODE
                print(f"🔒 隱私模式：{'開啟' if PRIVACY_MODE else '關閉'}")
            elif key == ord('s'):  # 截圖
                cv2.imwrite(f"safety_snapshot_{frame_count}.jpg", display_frame)
                print(f"📸 已截圖：safety_snapshot_{frame_count}.jpg")

            last_emit_time = current_time
        else:
            # 不處理時仍顯示畫面
            display_frame = draw_frame(frame, results, "Status: Normal", (0, 255, 0))
            cv2.imshow("Safety Monitor", display_frame)
            cv2.waitKey(frame_delay)

    cap.release()
    cv2.destroyAllWindows()
    print("\n✅ 監控結束！請查看 safety_session.csv")


if __name__ == "__main__":
    main()