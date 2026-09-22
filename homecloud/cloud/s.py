import cv2, ffmpeg, time, logging, threading, queue, numpy as np, mediapipe as mp, requests, pickle, importlib, os
from collections import deque
from enum import Enum
from flask import Flask, render_template, Response, request, jsonify, render_template_string

# === 匯入模型與工具 ===
from depth_model import load_depth_anything, estimate_depth
from yolo_model import load_yolo, detect_objects
from draw_yolo_detections import draw_yolo_detections
from smooth_ball_data import smooth_ball_data
from move_decision_raw import move_decision_raw
import shared_config
from uart_controller import create_uart_controller


# === 狀態機 ===
class RobotState(Enum):
    IDLE = 0
    SEARCH_AND_MOVE = 1
    PICK_UP = 2
    RETURN_BALL = 3
    WAITING = 4  # 用於非阻塞等待


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(threadName)s - %(message)s")
logger = logging.getLogger(__name__)

# === 全域變數：用於在 AI 和 Web UI 執行緒間傳遞影像 ===
global_frame_lock = threading.Lock()
global_jpeg_output = None
robot_controller = None


# === Class 1: FrameReader ===
class FrameReader:
    def __init__(self, stream_url, frame_queue, stop_event):
        self.stream_url = stream_url
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="FrameReader", daemon=True)

        # === 載入校正資料 ===
        try:
            with open("calibration_data.pkl", "rb") as f:
                data = pickle.load(f)
            self.K, self.D = data["K"], data["D"]
            logger.info("成功載入相機校正資料 calibration_data.pkl")
        except Exception as e:
            logger.error(f"載入相機校正資料失敗: {e}")
            self.K, self.D = None, None  # 若失敗則不做校正

        # 狀態旗標
        self.map1, self.map2 = None, None
        self.initialized = False

    def start(self):
        self.thread.start()

    def _run(self):
        logger.info("FrameReader started")
        cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            logger.error("無法開啟攝影機串流！")
            self.stop_event.set()
            return

        while not self.stop_event.is_set():
            cap.grab()  # 取得最新的畫面
            ret, frame = cap.retrieve()
            if not ret:
                logger.warning("讀取影像失敗，嘗試重連中...")
                time.sleep(1)
                cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
                continue

            # === 初始化去畸變映射表（只做一次） ===
            if (not self.initialized) and (self.K is not None and self.D is not None):
                h, w = frame.shape[:2]
                DIM = (w, h)

                # 動態縮放 K
                K_scaled = self.K.copy()
                scale_x = w / 640
                scale_y = h / 480
                K_scaled[0, 0] *= scale_x
                K_scaled[0, 2] *= scale_x
                K_scaled[1, 1] *= scale_y
                K_scaled[1, 2] *= scale_y

                try:
                    K_new = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                        K_scaled, self.D, DIM, np.eye(3), balance=0.0
                    )
                    self.map1, self.map2 = cv2.fisheye.initUndistortRectifyMap(
                        K_scaled, self.D, np.eye(3), K_new, DIM, cv2.CV_16SC2
                    )
                    self.initialized = True
                    logger.info(f"初始化去畸變映射完成，畫面大小：{w}x{h}")
                except Exception as e:
                    logger.error(f"去畸變初始化失敗: {e}")
                    self.map1, self.map2 = None, None

            # === 套用去畸變（若成功載入校正檔） ===
            if self.map1 is not None and self.map2 is not None:
                try:
                    frame = cv2.remap(
                        frame, self.map1, self.map2,
                        interpolation=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT
                    )
                except Exception as e:
                    logger.error(f"去畸變映射失敗: {e}")

            # === 推入佇列 ===
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

        # 載入模型
        logger.info("載入 YOLO 和 DepthAnything 模型...")
        self.yolo_model = load_yolo()
        self.depth_model = load_depth_anything()
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(static_image_mode=False, max_num_hands=2, min_detection_confidence=0.5)

    def start(self):
        self.thread.start()

    def _run(self):
        logger.info("InferenceWorker started")
        frame_count = 0
        last_depth_map = None
        last_yolo_results = None

        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
            except queue.Empty:
                continue

            # Hand detection
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hand_results = self.hands.process(rgb_frame)

            frame_count += 1

            # YOLO
            if frame_count % 1 == 0 or last_yolo_results is None:
                t0 = time.perf_counter()
                detections = detect_objects(self.yolo_model, frame)
                yolo_ms = (time.perf_counter() - t0) * 1000
                logger.info(f"YOLO 推論時間: {yolo_ms:.2f} ms")
                last_yolo_results = detections
            else:
                detections = last_yolo_results

            # Depth (每 5 幀做一次)
            if frame_count % 1 == 0 or last_depth_map is None:
                t1 = time.perf_counter()
                depth_map = estimate_depth(self.depth_model, frame)
                depth_ms = (time.perf_counter() - t1) * 1000
                logger.info(f"Depth 推論時間: {depth_ms:.2f} ms")
                last_depth_map = depth_map
            else:
                depth_map = last_depth_map

            result_package = {
                "frame": frame,
                "hand_results": hand_results,
                "detections": detections,
                "depth_map": depth_map,
            }

            if self.result_queue.full():
                self.result_queue.get_nowait()
            self.result_queue.put_nowait(result_package)

        self.hands.close()
        logger.info("InferenceWorker stopped")


# === Class 3: StateMachineController ===
class StateMachineController:
    def __init__(self, uart=None):
        # === 狀態與路徑 ===
        self.state = RobotState.IDLE
        self.rotation_active = False
        self.search_start_time = None
        self.search_angle = 0  # 已累積搜索角度（度）
        self.movement_path = []  # [(cmd:str, duration:float), ...] 依發出的動作逐筆加入（正向）
        self.movement_command_queue = []  # 返程或批次執行用的佇列（先入先出）

        # === 搜索/移動節拍一致化 ===
        self.turn_step_deg = 10  # 每次搜索轉動 10°
        self.turn_step_duration = 0.09  # 轉 10° 所需時間（秒）←請以實測修正
        self.move_pulse_duration = 0.09  # 前/後/左/右 一次點動的時間（秒）←與 Arduino 配合

        # 返程規劃旗標（只規劃一次）
        self.uart = uart or create_uart_controller()
        self.return_plan_ready = False

        # === 平滑用視覺窗口 ===
        self.x_window, self.y_window, self.z_window = deque(maxlen=5), deque(maxlen=5), deque(maxlen=5)

        # === MediaPipe Hands 畫圖工具 ===
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands

        # === 非阻塞等待 ===
        self.wait_until_time = 0.0
        self.next_state_after_wait = None

    # ---------- 小工具 ----------
    def _start_wait(self, duration, next_state):
        """啟動非阻塞等待；等待完自動切回 next_state。"""
        self.state = RobotState.WAITING
        self.wait_until_time = time.time() + float(duration)
        self.next_state_after_wait = next_state
        logger.info(f"進入等待狀態 {duration:.2f}s，之後切換到 {next_state.name}")

    def send_command(self, command):
        global CONTROL_URL
        try:
            result = self.uart.send(command)
            logger.info(f"[UART command] {command} -> {result.get('packet')}")
            return result
        except Exception as e:
            logger.error(f"[UART command] failed to send {command}: {e}")
            return {"ok": False, "error": str(e), "command": command}

        try:
            r = requests.post(
                CONTROL_URL,
                json={"command": command},
                timeout=2.0
            )
            if r.status_code == 200:
                logger.info(f"[指令] 發送至樹莓派: {command}, 回應: {r.text}")
            else:
                logger.error(f"[指令] 失敗: {command}, 狀態碼: {r.status_code}, 回應: {r.text}")
        except Exception as e:
            logger.error(f"[指令] 發送錯誤: {e}")

    @staticmethod
    def get_reverse_command(command):
        return {"forward": "backward",
                "backward": "forward",
                "left": "right",
                "right": "left",
                "pre": "re"}.get(command, None)

    def _record_move(self, command, duration):
        """只在實際發出移動指令時記錄到 movement_path。"""
        if command in ("forward", "backward", "left", "right", "pre"):
            self.movement_path.append((command, float(duration)))

    def _reset_search(self):
        self.rotation_active = False
        self.search_start_time = None
        self.search_angle = 0

    def _plan_return_once(self):
        """把已走過的 movement_path 倒播成返程佇列，僅規劃一次。"""
        if self.return_plan_ready:
            return
        self.movement_command_queue.clear()

        # 直接「倒播 movement_path」：最後動作先還原
        for cmd, dur in reversed(self.movement_path):
            rev = self.get_reverse_command(cmd)
            if rev:
                self.movement_command_queue.append((rev, dur))

        # 規劃完成後清空 path，避免下次再被規劃進來
        self.movement_path.clear()
        self.return_plan_ready = True
        logger.info(f"[返程規劃] 產生 {len(self.movement_command_queue)} 筆返程指令。")

    # ---------- 主狀態機 ----------
    def step(self, result):
        """執行一次狀態更新，回傳疊字後的 frame。"""
        try:
            # 強制 Python 重新從硬碟讀取 shared_config.py
            importlib.reload(shared_config)
            current_mode = shared_config.mode
        except Exception as e:
            logger.warning(f"無法載入 shared_config: {e}, 保持 '手動' 模式")
            current_mode = "手動"

        frame = result["frame"]
        detections = result.get("detections")
        depth_map = result.get("depth_map")
        hand_results = result.get("hand_results")
        if detections is None or depth_map is None or hand_results is None:
            return frame  # 必要輸入缺失，直接回傳

        H, W = frame.shape[:2]

        # 距離/位置估計
        x1, y1, smooth_x, smooth_raw_depth = smooth_ball_data(
            detections, depth_map, self.x_window, self.y_window, self.z_window
        )
        dis = smooth_raw_depth
        ball_box = (x1, y1, x1 + 30, y1 + 30) if (x1 is not None and y1 is not None) else None

        # 畫 YOLO 與手部
        frame = draw_yolo_detections(frame, detections, {0: "Ping Pong Ball"})
        if hand_results.multi_hand_landmarks:
            for hand in hand_results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(frame, hand, self.mp_hands.HAND_CONNECTIONS)

        # ---- 手動模式暫停 ----
        if current_mode != "Auto":
            logger.info(f"模式為 {current_mode}，自動狀態機暫停中...")
            self.state = RobotState.IDLE
            self.return_plan_ready = False
            self._reset_search()
            self.movement_path.clear()  # 清空正向路徑
            self.movement_command_queue.clear()  # 清空返程佇列
            logger.info("[狀態重置] 搜索狀態、路徑記錄與返程計畫已清空。")  # 新增 Log
            cv2.putText(frame, f"Mode: {current_mode}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            if dis is not None:
                cv2.putText(frame, f"Ball Depth: {dis:.2f}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            else:
                cv2.putText(frame, "Ball Depth: --", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            return frame

        elif current_mode == "Auto":

            # ---- WAITING（非阻塞）----
            if self.state == RobotState.WAITING:
                now = time.time()
                if now >= self.wait_until_time:
                    logger.info(f"等待結束 → {self.next_state_after_wait.name}")
                    self.state = self.next_state_after_wait
                else:
                    cv2.putText(frame, "Waiting...", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                    txt = f"Ball Depth: {dis:.2f}" if dis is not None else "Ball Depth: --"
                    cv2.putText(frame, txt, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                    return frame  # 等待中直接回傳

            # ================== 各狀態 ==================
            if self.state == RobotState.IDLE:
                logger.info("收到指令！從 IDLE 切換到 SEARCH_AND_MOVE。")
                self.state = RobotState.SEARCH_AND_MOVE
                self._reset_search()

            elif self.state == RobotState.SEARCH_AND_MOVE:
                logger.info("同時搜尋並靠近球...")
                # 有距離估計：用決策器移動
                if dis is not None:
                    command = move_decision_raw(smooth_x, dis, W)
                    if command:
                        if command in ("left", "right", "forward", "backward"):
                            self.send_command(command)
                            self._record_move(command, self.move_pulse_duration)
                            self._start_wait(self.move_pulse_duration, RobotState.SEARCH_AND_MOVE)
                            return frame
                        elif command == "pre":
                            # 快速前進
                            self.send_command("pre")
                            self._record_move("pre", 1.0)
                            self._start_wait(1.0, RobotState.SEARCH_AND_MOVE)
                            return frame
                        elif command == "pick":
                            # 進入撿球 → 之後會進 RETURN_BALL
                            self.state = RobotState.PICK_UP
                            self._reset_search()
                            self.return_plan_ready = False
                            return frame
                    else:
                        # 有畫面卻暫無建議：重置搜索狀態累積
                        self._reset_search()

                # 沒有距離/目標：做原地逐格搜索（每格 turn_step_deg）
                if not self.rotation_active:
                    self.rotation_active = True
                    self.search_start_time = time.time()
                    self.search_angle = 0
                    self.send_command("right")
                    self._record_move("right", self.turn_step_duration)
                    self._start_wait(self.turn_step_duration, RobotState.SEARCH_AND_MOVE)
                    return frame
                else:
                    # 前一次已經轉了一格，等回來後判斷是否要再轉下一格
                    # 這裡不靠 time 比較，而是透過 WAITING 節拍控制；回到此處表示一格已完成
                    self.search_angle += self.turn_step_deg
                    if self.search_angle < 360:
                        self.send_command("right")
                        self._record_move("right", self.turn_step_duration)
                        self._start_wait(self.turn_step_duration, RobotState.SEARCH_AND_MOVE)
                        return frame
                    else:
                        # 搜索 360° 仍找不到 → 回到 IDLE
                        self._reset_search()
                        self.state = RobotState.IDLE

            elif self.state == RobotState.PICK_UP:
                logger.info("撿球中...")
                # 這裡先用簡化邏輯：向前點動一次後，進入返程
                self.send_command("pick")
                # self._record_move("forward", self.move_pulse_duration)
                self._start_wait(1.0, RobotState.RETURN_BALL)

            elif self.state == RobotState.RETURN_BALL:
                logger.info("回到起始位置（倒播路徑）...")
                # 只在第一次進來時規劃返程
                self._plan_return_once()

                if self.movement_command_queue:
                    command, duration = self.movement_command_queue.pop(0)
                    self.send_command(command)
                    self._start_wait(duration, RobotState.RETURN_BALL)
                    return frame
                else:
                    # 返程完畢
                    self.send_command("arrive")
                    self._start_wait(1.0, RobotState.IDLE)
                    with open("shared_config.py", "w") as f:
                        f.write('mode = "Manual"\n')
                    self.return_plan_ready = False
                    self._reset_search()
        # ---- 疊字資訊 ----
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
            logger.warning("FramePusher has already been started, please do not start it again!")
            return
        logger.info(f"FramePusher started, target RTSP: {self.rtsp_url}")
        self.ffmpeg_process = (
            ffmpeg
            .input('pipe:', format='rawvideo', pix_fmt='bgr24', s=f'{width}x{height}', framerate=fps)
            .output(
                self.rtsp_url,
                vcodec='h264_nvenc',
                preset='p1',
                tune='ull',
                pix_fmt='yuv420p',
                g=fps,
                f='rtsp',
                rtsp_transport='udp',
                r=fps,
            )
            .global_args('-re')
            .run_async(pipe_stdin=True)
        )
        self.is_initialized = True

    def push(self, frame):
        if self.ffmpeg_process is None:
            raise RuntimeError("推流器尚未啟动，请先调用 start()")

        try:
            self.ffmpeg_process.stdin.write(frame.astype('uint8').tobytes())
        except BrokenPipeError:
            logger.error("FFmpeg pipline broken, stopping FramePusher...")
            self.stop()

    def stop(self):
        if self.ffmpeg_process:
            try:
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
            except Exception as e:
                logger.warning(f"close FFmpeg, Error: {e}")
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
                <html lang="en">
                <head>
                    <title>機器人控制台</title>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=0.67">

                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.1/dist/css/bootstrap.min.css" rel="stylesheet"
                        integrity="sha384-F3w7mX95PdgyTmZZMECAngseQB83DfGTowi0iMjiWaeVhAn4FJkqJByhZMI3AhiU" crossorigin="anonymous">
                </head>
                <body>
                    <style>
                        @font-face {
                            font-family: 'myfont';
                            src: url('/static/HouseCamping.otf') format('opentype');
                        }
                        body {
                            background-color: #f0f8ff;
                            background-image: radial-gradient(#bdc3c7 1px, transparent 1px);
                            background-size: 20px 20px;
                            min-height: 100vh;
                        }
                        .container {
                            background-color: #f0f8ff;
                            border-radius: 25px;
                            margin-top: 5px;
                            padding: 5px;
                            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.1);
                            max-width: 1200px;
                        }
                        .btn {
                            font-family: 'myfont', sans-serif;
                            font-size: 30px;
                            border: 3px solid;
                            border-radius: 12px;
                        }
                        .header {
                            text-align: center;
                            font-family: 'myfont', sans-serif;
                            background: linear-gradient(143deg, #6de4e4, #f1ed68);
                        }
                        .stream {
                            border: 2px solid gray;
                            border-radius: 10px;
                            box-shadow: 0 0 0 7px #DCDCDC, 0 0 0 12px #625B57, 0 20px 40px rgba(0, 0, 0, 0.8);
                            height: 540px;
                            width: 700px;
                        }
                    </style>

                    <div class="header text-dark fw-bolder">
                        <h1 style="font-size: 80px; font-weight: bolder; text-shadow: 2px 2px 6px #000000;">AI AGENT ROBOT_VIDEO STREAM
                        </h1>
                    </div>
                    <div class="container">
                        <div id="video-stream" class="row align-items-center justify-content-around text-center mt-4 mb-5">
                            <img src="{{ url_for('video_feed') }}" class="img-fluid stream" alt="Streaming...">
                        </div>
                        <div class="row row-cols-2 justify-content-center mb-2">
                            <div class="col-2 text-center">
                                <button id="start-button" type="button" class="btn btn-outline-success fw-bolder">START</button>
                            </div>
                            <div class="col-2 text-center">
                                <button id="stop-button" type="button" class="btn btn-outline-danger fw-bolder">RESET</button>
                            </div>
                        </div>
                    </div>

                    <div class="text-center mt-3 text-muted" style="font-size: 16px; opacity: 0.7;">
                        <p>SYSTEM STATUS: ONLINE | v1.0.0
                        </p>
                    </div>

                    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.1/dist/js/bootstrap.bundle.min.js"
                        integrity="sha384-/bQdsTh/da6pkI1MST/rWKFNjaCP5gBSY4sEBT38Q/9RBh9AH40zEOg7Hlq2THRZ"
                        crossorigin="anonymous"></script>
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
                if new_mode == "Auto" or new_mode == "Manual":
                    # 覆蓋 shared_config.py 檔案
                    with open("shared_config.py", "w") as f:
                        f.write(f'mode = "{new_mode}"\n')

                    logger.info(f"模式已切換為 [{new_mode}]")
                    return jsonify({"status": "success", "message": f"模式已切換為 [{new_mode}]"})
                else:
                    return jsonify({"status": "error", "message": "未知的模式"}), 400
            except Exception as e:
                logger.error(f"切換模式失敗: {e}")
                return jsonify({"status": "error", "message": str(e)}), 500

    def run(self):
        logger.info("Web UI started on http://0.0.0.0:5000")
        try:
            self.flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
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
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg_frame + b'\r\n')

            time.sleep(1 / 15)  # 約 15 FPS


# === 主程式 ===
def send_robot_command(command):
    if robot_controller is None:
        return {"ok": False, "error": "robot system is not started", "command": command}
    return robot_controller.send_command(command)


def start_robot_system():
    global robot_controller

    stop_event = threading.Event()
    frame_queue = queue.Queue(maxsize=1)
    result_queue = queue.Queue(maxsize=1)

    stream_url = os.getenv(
        "STREAM_URL",
        "rtsp://10.238.167.243:8554/mystream?fflags=nobuffer&flags=low_delay&max_delay=0",
    )
    result_url = os.getenv("RTSP_OUTPUT_URL", "rtsp://10.238.167.243:8554/result")
    enable_push_result = os.getenv("ENABLE_PUSH_RESULT", "1").strip() != "0"

    uart = create_uart_controller()
    reader = FrameReader(stream_url, frame_queue, stop_event)
    worker = InferenceWorker(frame_queue, result_queue, stop_event)
    controller = StateMachineController(uart=uart)
    pusher = FramePusher(rtsp_url=result_url) if enable_push_result else None
    robot_controller = controller

    reader.start()
    worker.start()

    def robot_loop():
        global global_jpeg_output
        try:
            while not stop_event.is_set():
                try:
                    result = result_queue.get(timeout=1)
                    frame = controller.step(result)

                    if pusher is not None:
                        if not pusher.is_initialized:
                            h, w, _ = result["frame"].shape
                            pusher.start(width=w, height=h, fps=10)
                        pusher.push(frame)

                    ret, jpeg_buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    if ret:
                        with global_frame_lock:
                            global_jpeg_output = jpeg_buffer.tobytes()
                except queue.Empty:
                    continue
        finally:
            stop_event.set()
            if pusher is not None:
                pusher.stop()
            uart.close()

    threading.Thread(target=robot_loop, name="RobotLoop", daemon=True).start()
    return stop_event


if __name__ == "__main__":
    stop_event = threading.Event()
    frame_queue = queue.Queue(maxsize=1)
    result_queue = queue.Queue(maxsize=1)

    reader = FrameReader(
        "rtsp://10.238.167.243:8554/mystream?fflags=nobuffer&flags=low_delay&max_delay=0", frame_queue,
        stop_event)
    worker = InferenceWorker(frame_queue, result_queue, stop_event)
    controller = StateMachineController()
    pusher = FramePusher(rtsp_url="rtsp://10.238.167.243:8554/result")

    web_ui = WebUI(stop_event)

    reader.start()
    worker.start()
    web_ui.start()

    try:
        while not stop_event.is_set():
            try:
                result = result_queue.get(timeout=1)

                if not pusher.is_initialized:
                    h, w, _ = result["frame"].shape
                    pusher.start(width=w, height=h, fps=10)

                frame = controller.step(result)
                pusher.push(frame)

                # === 更新全域 JPEG 影像供 Web UI 使用 ===
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
        pusher.stop()
