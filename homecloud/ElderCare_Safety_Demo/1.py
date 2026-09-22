# web_server.py
import cv2
import time
import numpy as np
import mediapipe as mp
import threading
import safety_config
import safety_logger
from flask import Flask, render_template, Response
from flask_socketio import SocketIO, emit

# ==================== 初始化 ====================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# MediaPipe Pose
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(static_image_mode=False, model_complexity=1,
                    min_detection_confidence=0.5, min_tracking_confidence=0.5)

# ==================== 全局設定 ====================
# 視頻源設定
USE_VIDEO_FILE = True  # False = 筆電鏡頭，True = 影片檔
VIDEO_PATH = "test_fall.mp4"
CAMERA_ID = 0

# 隱私模式
privacy_mode = True

# 進階跌倒偵測參數
STILL_THRESHOLD = 0.03
IMMOBILE_LIMIT = 10
SEATING_AREA = [0.2, 0.8, 0.3, 0.9]

# 全局狀態
global_state = {
    "height": 165,
    "x": 50,
    "y": 50,
    "status": "normal",
    "immobilityTimer": 0,
    "inSeat": True,
    "alert": False,
    "alert_msg": "",
    "log": False,
    "log_type": "info",
    "log_msg": ""
}


# 跌倒狀態機
class FallState:
    def __init__(self):
        self.is_fallen = False
        self.fall_count = 0
        self.last_pos = None
        self.still_start_time = None
        self.prev_nose_y = 0
        self.last_move_time = time.time()


fall_state = FallState()


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
    global fall_state

    if fall_state.last_pos is None:
        fall_state.last_pos = current_pos
        return False, 0

    dist = ((current_pos[0] - fall_state.last_pos[0]) ** 2 +
            (current_pos[1] - fall_state.last_pos[1]) ** 2) ** 0.5

    if is_suspicious and dist < STILL_THRESHOLD:
        if fall_state.still_start_time is None:
            fall_state.still_start_time = time.time()

        elapsed = time.time() - fall_state.still_start_time
        return elapsed > IMMOBILE_LIMIT, elapsed
    else:
        if dist > STILL_THRESHOLD or not is_suspicious:
            fall_state.still_start_time = None
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


def gen_frames():
    """視頻流 - 支援影片檔"""
    global privacy_mode

    result = open_video_source()
    if result is None:
        while True:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "Camera Offline", (200, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (50, 50, 50), 2)
            ret, buffer = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(0.5)
        return

    cap, fps = result
    frame_delay = int(1000 / fps) if fps > 0 else 33

    print(f"✅ 視頻流攝影機已開啟 (FPS: {fps:.1f})")

    retry_count = 0
    max_retries = 10

    while True:
        ret, frame = cap.read()

        if not ret:
            if USE_VIDEO_FILE:
                print("🔄 影片播放完畢，重新開始...")
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                retry_count = 0
                continue

            retry_count += 1
            if retry_count >= max_retries:
                print(f"⚠️ 視頻流讀取失敗 {retry_count} 次，嘗試重啟攝影機...")
                cap.release()
                time.sleep(1)
                result = open_video_source()
                if result:
                    cap, fps = result
                    retry_count = 0
            time.sleep(0.1)
            continue

        retry_count = 0

        if privacy_mode:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "Privacy Mode", (200, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (50, 50, 50), 2)

        ret, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

        time.sleep(frame_delay / 1000.0)

    cap.release()


def camera_loop():
    """AI 偵測 - 整合進階跌倒邏輯"""
    global privacy_mode, global_state, fall_state

    max_retries = 5
    retry_count = 0
    retry_delay = 3

    while retry_count < max_retries:
        result = open_video_source()
        if result is None:
            print(f"⚠️ 無法開啟視頻源！重試 {retry_count + 1}/{max_retries}")
            retry_count += 1
            time.sleep(retry_delay)
            continue

        cap, fps = result
        frame_delay = int(1000 / fps) if fps > 0 else 33

        print("✅ AI 偵測已就緒，開始偵測...")
        print(f"🔒 隱私模式：{'開啟' if privacy_mode else '關閉'}")
        print(f"⚙️  跌倒參數：靜止閾值={STILL_THRESHOLD}, 警報時間={IMMOBILE_LIMIT}秒")
        retry_count = 0

        frame_count = 0
        last_emit_time = time.time()
        camera_error_count = 0

        while True:
            ret, frame = cap.read()

            if not ret:
                if USE_VIDEO_FILE:
                    print("🔄 AI 偵測影片播放完畢，重新開始...")
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    camera_error_count = 0
                    continue

                camera_error_count += 1
                if camera_error_count >= 10:
                    print("🔄 攝影機斷開，嘗試重新初始化...")
                    cap.release()
                    time.sleep(2)
                    break

                time.sleep(0.1)
                continue

            camera_error_count = 0

            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(rgb)

            current_time = time.time()
            if current_time - last_emit_time >= 0.2:

                status_msg = "Normal"
                is_suspicious = False

                if results.pose_landmarks:
                    landmarks = results.pose_landmarks.landmark

                    nose = landmarks[mp_pose.PoseLandmark.NOSE.value]
                    shoulder = landmarks[mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                    hip = landmarks[mp_pose.PoseLandmark.LEFT_HIP.value]
                    knee = landmarks[mp_pose.PoseLandmark.LEFT_KNEE.value]

                    global_state["x"] = int(nose.x * 100)
                    global_state["y"] = int(nose.y * 100)
                    global_state["height"] = int((1.0 - nose.y) * 200)

                    if abs(nose.y - fall_state.prev_nose_y) > 0.01:
                        fall_state.last_move_time = current_time
                    fall_state.prev_nose_y = nose.y

                    # 跌倒判定
                    is_fallen, confidence, is_suspicious = check_fall_logic(landmarks, h, w)

                    # 長時間不動判定
                    current_pos = (hip.x, hip.y)
                    is_immobile, elapsed_time = check_immobility(current_pos, is_suspicious)
                    global_state["immobilityTimer"] = int(elapsed_time)

                    # 座位區判定
                    in_seat = check_seating_area(hip.x, hip.y)
                    global_state["inSeat"] = in_seat

                    # 狀態更新
                    if is_immobile:
                        global_state["status"] = "alert"
                        global_state["alert"] = True
                        global_state["alert_msg"] = f"跌倒且靜止 {int(elapsed_time)}秒！"
                        global_state["log"] = True
                        global_state["log_type"] = "alert"
                        global_state["log_msg"] = f"跌倒警報 - 靜止 {int(elapsed_time)}秒"
                        safety_logger.log_event("FALL_IMMOBILE", confidence=confidence,
                                                action_taken="Alert Sent")
                        print(f"⚠️ 跌倒警報！可信度：{confidence:.2f}, 靜止：{int(elapsed_time)}秒")
                    elif is_suspicious:
                        global_state["status"] = "warning"
                    else:
                        global_state["status"] = "normal"
                        global_state["alert"] = False

                    if not is_suspicious and fall_state.still_start_time:
                        fall_state.still_start_time = None

                    print(f"📊 數據：x={global_state['x']}, y={global_state['y']}, "
                          f"height={global_state['height']}, seat={in_seat}")

                socketio.emit('data_update', global_state)

                global_state["alert"] = False
                global_state["log"] = False

                last_emit_time = current_time

            frame_count += 1
            time.sleep(frame_delay / 1000.0)

        cap.release()
        print("❌ AI 偵測循環已結束")

    print("❌ 達到最大重試次數，系統停止")


# ==================== Flask 路由 ====================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@socketio.on('connect')
def handle_connect():
    print('✅ 客戶端已連接')
    emit('connected', {'message': '已連接至監控伺服器'})


@socketio.on('toggle_privacy')
def handle_toggle_privacy():
    global privacy_mode
    privacy_mode = not privacy_mode
    print(f"🔒 隱私模式切換為：{'開啟' if privacy_mode else '關閉'}")
    emit('privacy_updated', {'mode': privacy_mode})


# ==================== 主程式 ====================

if __name__ == '__main__':
    t = threading.Thread(target=camera_loop, daemon=True)
    t.start()

    print("=" * 60)
    print("🚀 啟動智慧居家照護監控伺服器 v2.0")
    print(f"📽️  模式：{'影片檔' if USE_VIDEO_FILE else '攝影機'}")
    if USE_VIDEO_FILE:
        print(f"🎬 影片：{VIDEO_PATH}")
    print(f"🔒 隱私模式：{'開啟' if privacy_mode else '關閉'}")
    print(f"⚙️  跌倒參數：靜止閾值={STILL_THRESHOLD}, 警報時間={IMMOBILE_LIMIT}秒")
    print("🌐 請在瀏覽器打開：http://127.0.0.1:5000")
    print("=" * 60)

    socketio.run(app, debug=False, port=5000, host='0.0.0.0')