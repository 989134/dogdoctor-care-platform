import cv2
import ffmpeg
import time
import logging
import threading
import queue
import numpy as np
import mediapipe as mp
import requests
import pickle
import importlib

from collections import deque
from enum import Enum
from flask import Flask, Response, jsonify, render_template_string

# === 匯入模型與工具 ===
from depth_model import load_depth_anything, estimate_depth
from yolo_model import load_yolo, detect_objects
from draw_yolo_detections import draw_yolo_detections
from smooth_ball_data import smooth_ball_data
from move_decision_raw import move_decision_raw
import shared_config

# =============================
# 集中設定：只要改這裡
# =============================
PI_IP = "10.121.187.243"
RTSP_IN = f"rtsp://{PI_IP}:8554/mystream"
RTSP_OUT = f"rtsp://{PI_IP}:8554/result"
CONTROL_URL = f"http://{PI_IP}:5000/control"
WEB_UI_PORT = 8000
ENABLE_PUSH_RESULT = True  # 若 result 推流不需要，可改成 False


# === 狀態機 ===
class RobotState(Enum):
    IDLE = 0
    SEARCH_AND_MOVE = 1
    PICK_UP = 2
    RETURN_BALL = 3
    WAITING = 4


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(threadName)s - %(message)s")
logger = logging.getLogger(__name__)

# === 全域變數：用於 AI 與 Web UI 執行緒間傳遞影像 ===
global_frame_lock = threading.Lock()
global_jpeg_output = None


# === Class 1: FrameReader ===
class FrameReader:
    def __init__(self, stream_url, frame_queue, stop_event):
        self.stream_url = stream_url
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="FrameReader", daemon=True)

        # === 載入相機校正資料 ===
        try:
            with open("calibration_data.pkl", "rb") as f:
                data = pickle.load(f)
            self.K, self.D = data["K"], data["D"]
            logger.info("成功載入相機校正資料 calibration_data.pkl")
        except Exception as e:
            logger.warning(f"載入相機校正資料失敗，將略過去畸變: {e}")
            self.K, self.D = None, None

        self.map1, self.map2 = None, None
        self.initialized = False

    def start(self):
        self.thread.start()

    def _open_capture(self):
        logger.info(f"開啟串流: {self.stream_url}")
        cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self):
        logger.info("FrameReader started")
        cap = self._open_capture()

        if not cap.isOpened():
            logger.error("無法開啟攝影機串流！")
            self.stop_event.set()
            return

        while not self.stop_event.is_set():
            cap.grab()
            ret, frame = cap.retrieve()

            if not ret or frame is None:
                logger.warning("讀取影像失敗，1 秒後重連...")
                cap.release()
                time.sleep(1)
                cap = self._open_capture()
                continue

            # === 初始化去畸變映射表（只做一次） ===
            if (not self.initialized) and (self.K is not None and self.D is not None):
                h, w = frame.shape[:2]
                dim = (w, h)

                k_scaled = self.K.copy()
                scale_x = w / 640
                scale_y = h / 480
                k_scaled[0, 0] *= scale_x
                k_scaled[0, 2] *= scale_x
                k_scaled[1, 1] *= scale_y
                k_scaled[1, 2] *= scale_y

                try:
                    k_new = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                        k_scaled, self.D, dim, np.eye(3), balance=0.0
                    )
                    self.map1, self.map2 = cv2.fisheye.initUndistortRectifyMap(
                        k_scaled, self.D, np.eye(3), k_new, dim, cv2.CV_16SC2
                    )
                    self.initialized = True
                    logger.info(f"初始化去畸變映射完成，畫面大小：{w}x{h}")
                except Exception as e:
                    logger.warning(f"去畸變初始化失敗，將略過: {e}")
                    self.map1, self.map2 = None, None

            if self.map1 is not None and self.map2 is not None:
                try:
                    frame = cv2.remap(
                        frame,
                        self.map1,
                        self.map2,
                        interpolation=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT,
                    )
                except Exception as e:
                    logger.warning(f"去畸變映射失敗: {e}")

            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self.frame_queue.put(frame)

        cap.release()
        logger.info("FrameReader stopped")


# === Class 2: InferenceWorker ===
class InferenceWorker:
    def __init__(self, frame_queue, result_queue, stop_event):
        self.frame_queue = frame_queue
        self.result_queue = result_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="InferenceWorker", daemon=True)

        logger.info("載入 YOLO 和 DepthAnything 模型...")
        self.yolo_model = load_yolo()
        self.depth_model = load_depth_anything()
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.5,
        )

    def start(self):
        self.thread.start()

    def _run(self):
        logger.info("InferenceWorker started")
        last_depth_map = None
        last_yolo_results = None

        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
            except queue.Empty:
                continue

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hand_results = self.hands.process(rgb_frame)

            try:
                t0 = time.perf_counter()
                detections = detect_objects(self.yolo_model, frame)
                yolo_ms = (time.perf_counter() - t0) * 1000
                logger.info(f"YOLO 推論時間: {yolo_ms:.2f} ms")
                last_yolo_results = detections
            except Exception as e:
                logger.error(f"YOLO 推論失敗，沿用上次結果: {e}")
                detections = last_yolo_results if last_yolo_results is not None else []

            try:
                t1 = time.perf_counter()
                depth_map = estimate_depth(self.depth_model, frame)
                depth_ms = (time.perf_counter() - t1) * 1000
                logger.info(f"Depth 推論時間: {depth_ms:.2f} ms")
                last_depth_map = depth_map
            except Exception as e:
                logger.error(f"Depth 推論失敗，沿用上次結果: {e}")
                depth_map = last_depth_map if last_depth_map is not None else None

            result_package = {
                "frame": frame,
                "hand_results": hand_results,
                "detections": detections,
                "depth_map": depth_map,
            }

            if self.result_queue.full():
                try:
                    self.result_queue.get_nowait()
                except queue.Empty:
                    pass

            self.result_queue.put_nowait(result_package)

        self.hands.close()
        logger.info("InferenceWorker stopped")


# === Class 3: StateMachineController ===
class StateMachineController:
    def __init__(self):
        self.state = RobotState.IDLE
        self.rotation_active = False
        self.search_start_time = None
        self.search_angle = 0
        self.movement_path = []
        self.movement_command_queue = []

        self.turn_step_deg = 10
        self.turn_step_duration = 0.09
        self.move_pulse_duration = 0.09

        self.return_plan_ready = False

        self.x_window = deque(maxlen=5)
        self.y_window = deque(maxlen=5)
        self.z_window = deque(maxlen=5)

        self.mp_draw = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands

        self.wait_until_time = 0.0
        self.next_state_after_wait = None

    def _start_wait(self, duration, next_state):
        self.state = RobotState.WAITING
        self.wait_until_time = time.time() + float(duration)
        self.next_state_after_wait = next_state
        logger.info(f"進入等待狀態 {duration:.2f}s，之後切換到 {next_state.name}")

    def send_command(self, command):
        try:
            r = requests.post(
                CONTROL_URL,
                json={"command": command},
                timeout=2.0,
            )
            if r.status_code == 200:
                logger.info(f"[指令] 發送至樹莓派: {command}, 回應: {r.text}")
            else:
                logger.error(f"[指令] 失敗: {command}, 狀態碼: {r.status_code}, 回應: {r.text}")
        except Exception as e:
            logger.error(f"[指令] 發送錯誤: {e}")

    @staticmethod
    def get_reverse_command(command):
        return {
            "forward": "backward",
            "backward": "forward",
            "left": "right",
            "right": "left",
            "pre": "re",
        }.get(command, None)

    def _record_move(self, command, duration):
        if command in ("forward", "backward", "left", "right", "pre"):
            self.movement_path.append((command, float(duration)))

    def _reset_search(self):
        self.rotation_active = False
        self.search_start_time = None
        self.search_angle = 0

    def _plan_return_once(self):
        if self.return_plan_ready:
            return

        self.movement_command_queue.clear()
        for cmd, dur in reversed(self.movement_path):
            rev = self.get_reverse_command(cmd)
            if rev:
                self.movement_command_queue.append((rev, dur))

        self.movement_path.clear()
        self.return_plan_ready = True
        logger.info(f"[返程規劃] 產生 {len(self.movement_command_queue)} 筆返程指令。")

    def step(self, result):
        global global_jpeg_output

        try:
            importlib.reload(shared_config)
            current_mode = shared_config.mode
        except Exception as e:
            logger.warning(f"無法載入 shared_config.py: {e}，改用 Manual")
            current_mode = "Manual"

        frame = result["frame"]
        detections = result.get("detections")
        depth_map = result.get("depth_map")
        hand_results = result.get("hand_results")

        if detections is None or depth_map is None or hand_results is None:
            return frame

        h, w = frame.shape[:2]

        x1, y1, smooth_x, smooth_raw_depth = smooth_ball_data(
            detections,
            depth_map,
            self.x_window,
            self.y_window,
            self.z_window,
        )
        dis = smooth_raw_depth

        frame = draw_yolo_detections(frame, detections, {0: "Ping Pong Ball"})
        if hand_results.multi_hand_landmarks:
            for hand in hand_results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(frame, hand, self.mp_hands.HAND_CONNECTIONS)

        # === 手動模式 ===
        if current_mode != "Auto":
            if self.state != RobotState.IDLE:
                logger.info(f"模式為 {current_mode}，自動狀態機暫停並重置")
            self.state = RobotState.IDLE
            self.return_plan_ready = False
            self._reset_search()
            self.movement_path.clear()
            self.movement_command_queue.clear()

            cv2.putText(frame, f"Mode: {current_mode}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            if dis is not None:
                cv2.putText(frame, f"Ball Depth: {dis:.2f}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            else:
                cv2.putText(frame, "Ball Depth: --", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            return frame

        # === WAITING ===
        if self.state == RobotState.WAITING:
            now = time.time()
            if now >= self.wait_until_time:
                logger.info(f"等待結束 → {self.next_state_after_wait.name}")
                self.state = self.next_state_after_wait
            else:
                cv2.putText(frame, "Waiting...", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                txt = f"Ball Depth: {dis:.2f}" if dis is not None else "Ball Depth: --"
                cv2.putText(frame, txt, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                return frame

        # === 各狀態 ===
        if self.state == RobotState.IDLE:
            logger.info("收到 Auto 模式，從 IDLE 切換到 SEARCH_AND_MOVE")
            self.state = RobotState.SEARCH_AND_MOVE
            self._reset_search()

        elif self.state == RobotState.SEARCH_AND_MOVE:
            logger.info("同時搜尋並靠近球...")

            if dis is not None:
                command = move_decision_raw(smooth_x, dis, w)
                if command:
                    if command in ("left", "right", "forward", "backward"):
                        self.send_command(command)
                        self._record_move(command, self.move_pulse_duration)
                        self._start_wait(self.move_pulse_duration, RobotState.SEARCH_AND_MOVE)
                        return frame
                    elif command == "pre":
                        self.send_command("pre")
                        self._record_move("pre", 1.0)
                        self._start_wait(1.0, RobotState.SEARCH_AND_MOVE)
                        return frame
                    elif command == "pick":
                        self.state = RobotState.PICK_UP
                        self._reset_search()
                        self.return_plan_ready = False
                        return frame
                else:
                    self._reset_search()

            if not self.rotation_active:
                self.rotation_active = True
                self.search_start_time = time.time()
                self.search_angle = 0
                self.send_command("right")
                self._record_move("right", self.turn_step_duration)
                self._start_wait(self.turn_step_duration, RobotState.SEARCH_AND_MOVE)
                return frame
            else:
                self.search_angle += self.turn_step_deg
                if self.search_angle < 360:
                    self.send_command("right")
                    self._record_move("right", self.turn_step_duration)
                    self._start_wait(self.turn_step_duration, RobotState.SEARCH_AND_MOVE)
                    return frame
                else:
                    self._reset_search()
                    self.state = RobotState.IDLE

        elif self.state == RobotState.PICK_UP:
            logger.info("撿球中...")
            self.send_command("pick")
            self._start_wait(1.0, RobotState.RETURN_BALL)

        elif self.state == RobotState.RETURN_BALL:
            logger.info("回到起始位置（倒播路徑）...")
            self._plan_return_once()

            if self.movement_command_queue:
                command, duration = self.movement_command_queue.pop(0)
                self.send_command(command)
                self._start_wait(duration, RobotState.RETURN_BALL)
                return frame
            else:
                self.send_command("arrive")
                self._start_wait(1.0, RobotState.IDLE)
                with open("shared_config.py", "w", encoding="utf-8") as f:
                    f.write('mode = "Manual"\n')
                self.return_plan_ready = False
                self._reset_search()

        cv2.putText(frame, f"State: {self.state.name}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        if dis is not None:
            cv2.putText(frame, f"Ball Depth: {dis:.2f}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        else:
            cv2.putText(frame, "Ball Depth: --", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

        return frame


class FramePusher:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.ffmpeg_process = None
        self.is_initialized = False

    def start(self, width, height, fps):
        if self.is_initialized:
            logger.warning("FramePusher 已初始化，略過重複 start")
            return

        logger.info(f"FramePusher started, target RTSP: {self.rtsp_url}")
        self.ffmpeg_process = (
            ffmpeg
            .input('pipe:', format='rawvideo', pix_fmt='bgr24', s=f'{width}x{height}', framerate=fps)
            .output(
                self.rtsp_url,
                vcodec='libx264',
                preset='ultrafast',
                tune='zerolatency',
                pix_fmt='yuv420p',
                g=fps,
                f='rtsp',
                rtsp_transport='tcp',
                r=fps,
            )
            .global_args('-re')
            .run_async(pipe_stdin=True)
        )
        self.is_initialized = True

    def push(self, frame):
        if self.ffmpeg_process is None:
            return

        if self.ffmpeg_process.poll() is not None:
            logger.error("FFmpeg 已停止，停止推流")
            self.stop()
            return

        try:
            self.ffmpeg_process.stdin.write(frame.astype('uint8').tobytes())
        except (BrokenPipeError, OSError) as e:
            logger.error(f"FFmpeg pipe error: {e}")
            self.stop()

    def stop(self):
        if self.ffmpeg_process:
            try:
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
            except Exception as e:
                logger.warning(f"關閉 FFmpeg 時發生錯誤: {e}")
            finally:
                self.ffmpeg_process = None
                self.is_initialized = False
        logger.info("FramePusher stopped")


class WebUI(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(name="WebUI", daemon=True)
        self.stop_event = stop_event
        self.flask_app = Flask(__name__)

        flask_log = logging.getLogger('werkzeug')
        flask_log.setLevel(logging.ERROR)

        @self.flask_app.route('/')
        def index():
            return render_template_string("""
                <html lang="zh-Hant">
                <head>
                    <title>機器人控制台</title>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=0.85">
                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.1/dist/css/bootstrap.min.css" rel="stylesheet">
                </head>
                <body>
                    <style>
                        body {
                            background-color: #f0f8ff;
                            background-image: radial-gradient(#bdc3c7 1px, transparent 1px);
                            background-size: 20px 20px;
                            min-height: 100vh;
                        }
                        .container {
                            background-color: #f0f8ff;
                            border-radius: 25px;
                            margin-top: 8px;
                            padding: 12px;
                            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.1);
                            max-width: 1200px;
                        }
                        .btn {
                            font-size: 28px;
                            border: 3px solid;
                            border-radius: 12px;
                        }
                        .header {
                            text-align: center;
                            background: linear-gradient(143deg, #6de4e4, #f1ed68);
                            padding: 12px;
                        }
                        .stream {
                            border: 2px solid gray;
                            border-radius: 10px;
                            box-shadow: 0 0 0 7px #DCDCDC, 0 0 0 12px #625B57, 0 20px 40px rgba(0, 0, 0, 0.45);
                            max-height: 540px;
                            max-width: 100%;
                        }
                    </style>

                    <div class="header text-dark fw-bolder">
                        <h1 style="font-size: 56px; font-weight: bolder; text-shadow: 2px 2px 6px #00000055;">
                            AI AGENT ROBOT VIDEO STREAM
                        </h1>
                    </div>

                    <div class="container">
                        <div id="video-stream" class="row align-items-center justify-content-around text-center mt-3 mb-4">
                            <img src="{{ url_for('video_feed') }}" class="img-fluid stream" alt="Streaming...">
                        </div>
                        <div class="row row-cols-2 justify-content-center mb-2">
                            <div class="col-3 text-center">
                                <button id="start-button" type="button" class="btn btn-outline-success fw-bolder">START</button>
                            </div>
                            <div class="col-3 text-center">
                                <button id="stop-button" type="button" class="btn btn-outline-danger fw-bolder">RESET</button>
                            </div>
                        </div>
                    </div>

                    <div class="text-center mt-3 text-muted" style="font-size: 16px; opacity: 0.7;">
                        <p>SYSTEM STATUS: ONLINE | v1.1.0</p>
                    </div>

                    <script>
                        document.getElementById('start-button').addEventListener('click', function() {
                            fetch('/set_mode/Auto', { method: 'POST' })
                        });
                        document.getElementById('stop-button').addEventListener('click', function() {
                            fetch('/set_mode/Manual', { method: 'POST' })
                        });
                    </script>
                </body>
                </html>
            """)

        @self.flask_app.route('/video_feed')
        def video_feed():
            return Response(self.generate_mjpeg_stream(), mimetype='multipart/x-mixed-replace; boundary=frame')

        @self.flask_app.route('/set_mode/<new_mode>', methods=['POST'])
        def set_mode(new_mode):
            try:
                if new_mode in ("Auto", "Manual"):
                    with open("shared_config.py", "w", encoding="utf-8") as f:
                        f.write(f'mode = "{new_mode}"\n')
                    logger.info(f"模式已切換為 [{new_mode}]")
                    return jsonify({"status": "success", "message": f"模式已切換為 [{new_mode}]"})
                return jsonify({"status": "error", "message": "未知的模式"}), 400
            except Exception as e:
                logger.error(f"切換模式失敗: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500

    def run(self):
        logger.info(f"Web UI started on http://0.0.0.0:{WEB_UI_PORT}")
        try:
            self.flask_app.run(host='0.0.0.0', port=WEB_UI_PORT, debug=False, use_reloader=False)
        except Exception as e:
            if not self.stop_event.is_set():
                logger.error(f"Web UI encountered an error: {e}")

    def generate_mjpeg_stream(self):
        logger.info("WebUI video stream generator started")
        while not self.stop_event.is_set():
            jpeg_frame = None
            with global_frame_lock:
                if global_jpeg_output is not None:
                    jpeg_frame = global_jpeg_output

            if jpeg_frame:
                yield (
                    b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + jpeg_frame + b'\r\n'
                )

            time.sleep(1 / 15)


# === 主程式 ===
if __name__ == "__main__":
    logger.info(f"使用 PI_IP = {PI_IP}")
    logger.info(f"RTSP_IN = {RTSP_IN}")
    logger.info(f"CONTROL_URL = {CONTROL_URL}")
    logger.info(f"Web UI = http://127.0.0.1:{WEB_UI_PORT}")

    stop_event = threading.Event()
    frame_queue = queue.Queue(maxsize=1)
    result_queue = queue.Queue(maxsize=1)

    reader = FrameReader(RTSP_IN, frame_queue, stop_event)
    worker = InferenceWorker(frame_queue, result_queue, stop_event)
    controller = StateMachineController()
    pusher = FramePusher(rtsp_url=RTSP_OUT) if ENABLE_PUSH_RESULT else None
    web_ui = WebUI(stop_event)

    reader.start()
    worker.start()
    web_ui.start()

    try:
        while not stop_event.is_set():
            try:
                result = result_queue.get(timeout=1)
                frame = controller.step(result)

                if ENABLE_PUSH_RESULT and pusher is not None:
                    if not pusher.is_initialized:
                        hh, ww, _ = frame.shape
                        pusher.start(width=ww, height=hh, fps=10)
                    pusher.push(frame)

                ret, jpeg_buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ret:
                    with global_frame_lock:
                        global_jpeg_output = jpeg_buffer.tobytes()

            except queue.Empty:
                continue

    except KeyboardInterrupt:
        logger.info("Ctrl+C pressed, exiting...")

    finally:
        logger.info("Stopping all threads...")
        stop_event.set()
        if pusher is not None:
            pusher.stop()
