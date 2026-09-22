#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py - 機器人狀態機控制器（完整版）
功能：球偵測 + 深度估計 + 障礙物繞行 + 自動返回
"""

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
import os
from collections import deque
from enum import Enum
from flask import Flask, Response, request, jsonify, render_template_string

# === 匯入模型與工具 ===
from depth_model import load_depth_anything, estimate_depth
from yolo_model import load_yolo, detect_objects
from draw_yolo_detections import draw_yolo_detections
from smooth_ball_data import smooth_ball_data
from move_decision_raw import move_decision_raw
import shared_config


# === 狀態機 ===
class RobotState(Enum):
    IDLE = 0
    SEARCH_AND_MOVE = 1
    PICK_UP = 2
    RETURN_BALL = 3
    WAITING = 4
    OBSTACLE_AVOID = 5  # 新增：障礙物繞行狀態


# === 日誌設定 ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(threadName)s - %(message)s")
logger = logging.getLogger(__name__)

# === 全域變數 ===
global_frame_lock = threading.Lock()
global_jpeg_output = None

# === 全域控制 URL（會在 __main__ 中設定）===
CONTROL_URL = ""


# ============================================================================
# Class 1: FrameReader - 讀取 RTSP 串流
# ============================================================================
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
            logger.info("✅ 成功載入相機校正資料 calibration_data.pkl")
        except Exception as e:
            logger.error(f"⚠️ 載入相機校正資料失敗：{e}")
            self.K, self.D = None, None

        self.map1, self.map2 = None, None
        self.initialized = False

    def start(self):
        self.thread.start()

    def _run(self):
        logger.info("🎬 FrameReader started")

        # RTSP 參數：使用 TCP 傳輸 + 緩衝區設定
        rtsp_url_with_params = self.stream_url + "?rtsp_transport=tcp&buffer_size=102400"
        cap = cv2.VideoCapture(rtsp_url_with_params, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            logger.error(f"❌ 無法開啟攝影機串流：{self.stream_url}")
            logger.error("💡 請確認：")
            logger.error("   1. 樹莓派上的 MediaMTX 有在運行")
            logger.error("   2. rpivid-vid + ffmpeg 有在發布串流到 mystream")
            logger.error("   3. IP 正確且網路可達")
            self.stop_event.set()
            return

        while not self.stop_event.is_set():
            cap.grab()  # 取得最新畫面
            ret, frame = cap.retrieve()

            if not ret:
                logger.warning("⚠️ 讀取影像失敗，嘗試重連中...")
                time.sleep(1)
                cap = cv2.VideoCapture(rtsp_url_with_params, cv2.CAP_FFMPEG)
                continue

            # === 初始化去畸變映射表（只做一次）===
            if (not self.initialized) and (self.K is not None and self.D is not None):
                h, w = frame.shape[:2]
                DIM = (w, h)

                # 動態縮放 K 矩陣
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
                    logger.info(f"✅ 初始化去畸變映射完成，畫面大小：{w}x{h}")
                except Exception as e:
                    logger.error(f"⚠️ 去畸變初始化失敗：{e}")
                    self.map1, self.map2 = None, None

            # === 套用去畸變（若成功載入校正檔）===
            if self.map1 is not None and self.map2 is not None:
                try:
                    frame = cv2.remap(
                        frame, self.map1, self.map2,
                        interpolation=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT
                    )
                except Exception as e:
                    logger.error(f"⚠️ 去畸變映射失敗：{e}")

            # === 確保 frame 是三通道 BGR（避免深度圖污染）===
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

            # === 推入佇列（若滿則丟棄舊幀）===
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self.frame_queue.put(frame)

        cap.release()
        logger.info("🛑 FrameReader stopped")


# ============================================================================
# Class 2: InferenceWorker - YOLO + Depth 推論
# ============================================================================
class InferenceWorker:
    def __init__(self, frame_queue, result_queue, stop_event):
        self.frame_queue = frame_queue
        self.result_queue = result_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="InferenceWorker", daemon=True)

        logger.info("🤖 載入 YOLO 和 DepthAnything 模型...")
        self.yolo_model = load_yolo()
        self.depth_model = load_depth_anything()
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            min_detection_confidence=0.5
        )

    def start(self):
        self.thread.start()

    def _run(self):
        logger.info("🔍 InferenceWorker started")
        frame_count = 0
        last_depth_map = None
        last_yolo_results = None

        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
            except queue.Empty:
                continue

            # === 手部偵測（MediaPipe）===
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hand_results = self.hands.process(rgb_frame)

            frame_count += 1

            # === YOLO 物件偵測（每幀執行）===
            if frame_count % 1 == 0 or last_yolo_results is None:
                t0 = time.perf_counter()
                detections = detect_objects(self.yolo_model, frame)
                yolo_ms = (time.perf_counter() - t0) * 1000
                logger.info(f"⚡ YOLO 推論時間：{yolo_ms:.2f} ms")
                last_yolo_results = detections
            else:
                detections = last_yolo_results

            # === Depth 深度估計（每幀執行）===
            if frame_count % 1 == 0 or last_depth_map is None:
                t1 = time.perf_counter()
                depth_map = estimate_depth(self.depth_model, frame)  # 返回 0-255 單通道
                depth_ms = (time.perf_counter() - t1) * 1000
                logger.info(f"⚡ Depth 推論時間：{depth_ms:.2f} ms")
                last_depth_map = depth_map
            else:
                depth_map = last_depth_map

            # === 打包結果 ===
            # depth_map 是單通道 (H,W) uint8，只用於計算，不用於顯示
            result_package = {
                "frame": frame,  # ← 三通道 BGR
                "hand_results": hand_results,
                "detections": detections,
                "depth_map": depth_map,  # ← 單通道 0-255
            }

            if self.result_queue.full():
                self.result_queue.get_nowait()
            self.result_queue.put_nowait(result_package)

        self.hands.close()
        logger.info("🛑 InferenceWorker stopped")


# ============================================================================
# Class 3: StateMachineController - 狀態機 + 障礙物繞行
# ============================================================================
class StateMachineController:
    def __init__(self):
        # === 狀態與路徑 ===
        self.state = RobotState.IDLE
        self.rotation_active = False
        self.search_start_time = None
        self.search_angle = 0
        self.movement_path = []
        self.movement_command_queue = []

        # === 搜索/移動節拍 ===
        self.turn_step_deg = 10
        self.turn_step_duration = 0.09
        self.move_pulse_duration = 0.09

        # === 返程規劃 ===
        self.return_plan_ready = False

        # === 平滑窗口 ===
        self.x_window = deque(maxlen=5)
        self.y_window = deque(maxlen=5)
        self.z_window = deque(maxlen=5)

        # === MediaPipe 畫圖工具 ===
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands

        # === 非阻塞等待 ===
        self.wait_until_time = 0.0
        self.next_state_after_wait = None

        # === 障礙物繞行參數 ===
        self.obstacle_detected = False
        self.avoid_step_count = 0
        self.max_avoid_steps = 3
        self.obstacle_depth_threshold = 40  # 深度值 < 40 判定為障礙物（0-255 範圍）

    def _start_wait(self, duration, next_state):
        """啟動非阻塞等待"""
        self.state = RobotState.WAITING
        self.wait_until_time = time.time() + float(duration)
        self.next_state_after_wait = next_state
        logger.info(f"⏱️ 進入等待狀態 {duration:.2f}s，之後切換到 {next_state.name}")

    def send_command(self, command):
        """發送指令到樹莓派控制伺服器"""
        global CONTROL_URL
        try:
            r = requests.post(
                CONTROL_URL,
                json={"command": command},
                timeout=2.0
            )
            if r.status_code == 200:
                logger.info(f"📤 [指令] 發送至樹莓派：{command}, 回應：{r.text}")
            else:
                logger.error(f"❌ [指令] 失敗：{command}, 狀態碼：{r.status_code}, 回應：{r.text}")
        except Exception as e:
            logger.error(f"❌ [指令] 發送錯誤：{e}")

    @staticmethod
    def get_reverse_command(command):
        """取得反向指令（用於返程）"""
        return {
            "forward": "backward",
            "backward": "forward",
            "left": "right",
            "right": "left",
            "pre": "re"
        }.get(command, None)

    def _record_move(self, command, duration):
        """記錄移動指令到路徑（用於返程規劃）"""
        if command in ("forward", "backward", "left", "right", "pre"):
            self.movement_path.append((command, float(duration)))

    def _reset_search(self):
        """重置搜索狀態"""
        self.rotation_active = False
        self.search_start_time = None
        self.search_angle = 0

    def _plan_return_once(self):
        """規劃返程路徑（倒播 movement_path）"""
        if self.return_plan_ready:
            return
        self.movement_command_queue.clear()

        for cmd, dur in reversed(self.movement_path):
            rev = self.get_reverse_command(cmd)
            if rev:
                self.movement_command_queue.append((rev, dur))

        self.movement_path.clear()
        self.return_plan_ready = True
        logger.info(f"🔄 [返程規劃] 產生 {len(self.movement_command_queue)} 筆返程指令。")

    def _detect_obstacle(self, dis):
        """檢測障礙物：深度值 < 閾值且球在前方"""
        if dis is not None and dis < self.obstacle_depth_threshold:
            return True
        return False

    def step(self, result):
        """狀態機主循環：執行一次狀態更新，回傳疊字後的 frame"""

        # === 動態載入模式設定 ===
        try:
            importlib.reload(shared_config)
            current_mode = shared_config.mode
        except Exception as e:
            logger.warning(f"⚠️ 無法載入 shared_config: {e}, 保持 'Manual' 模式")
            current_mode = "Manual"

        frame = result["frame"]
        detections = result.get("detections")
        depth_map = result.get("depth_map")
        hand_results = result.get("hand_results")

        if detections is None or depth_map is None or hand_results is None:
            return frame

        H, W = frame.shape[:2]

        # === 確保 frame 是三通道 BGR ===
        if len(frame.shape) == 2:
            logger.warning("⚠️ frame 是單通道！轉換為三通道")
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        # === 距離/位置估計 ===
        x1, y1, smooth_x, smooth_raw_depth = smooth_ball_data(
            detections, depth_map, self.x_window, self.y_window, self.z_window
        )
        dis = smooth_raw_depth  # 0-255 的歸一化深度值

        # === 畫 YOLO 框與手部骨架 ===
        frame = draw_yolo_detections(frame, detections, {0: "Ping Pong Ball"})
        if hand_results.multi_hand_landmarks:
            for hand in hand_results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(frame, hand, self.mp_hands.HAND_CONNECTIONS)

        # ==== 手動模式：暫停自動狀態機 ====
        if current_mode != "Auto":
            logger.info(f"🔧 模式為 {current_mode}，自動狀態機暫停中...")
            self.state = RobotState.IDLE
            self.return_plan_ready = False
            self._reset_search()
            self.movement_path.clear()
            self.movement_command_queue.clear()

            cv2.putText(frame, f"Mode: {current_mode}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            if dis is not None:
                cv2.putText(frame, f"Depth: {dis:.1f} (0-255)", (10, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            else:
                cv2.putText(frame, "Depth: --", (10, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
            return frame

        # ==== Auto 模式：執行狀態機 ====
        elif current_mode == "Auto":

            # --- WAITING 狀態（非阻塞等待）---
            if self.state == RobotState.WAITING:
                now = time.time()
                if now >= self.wait_until_time:
                    logger.info(f"⏱️ 等待結束 → {self.next_state_after_wait.name}")
                    self.state = self.next_state_after_wait
                else:
                    cv2.putText(frame, "Waiting...", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                    txt = f"Depth: {dis:.1f}" if dis is not None else "Depth: --"
                    cv2.putText(frame, txt, (10, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                    return frame

            # ================== 各狀態處理 ==================

            # --- IDLE ---
            if self.state == RobotState.IDLE:
                logger.info("🟢 收到指令！從 IDLE 切換到 SEARCH_AND_MOVE。")
                self.state = RobotState.SEARCH_AND_MOVE
                self._reset_search()

            # --- SEARCH_AND_MOVE ---
            elif self.state == RobotState.SEARCH_AND_MOVE:
                logger.info("🔍 狀態：SEARCH_AND_MOVE - 搜尋並靠近球...")

                # === 障礙物檢測（模擬臂障功能）===
                if self._detect_obstacle(dis) and not self.obstacle_detected:
                    self.obstacle_detected = True
                    self.avoid_step_count = 0
                    self.state = RobotState.OBSTACLE_AVOID
                    logger.info(f"⚠️ 檢測到障礙物！深度值={dis:.1f}，開始繞行...")
                    return frame

                # 有距離估計：用決策器移動
                if dis is not None and smooth_x is not None:
                    command = move_decision_raw(smooth_x, dis, W)

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
                            logger.info("🎯 準備抓取球")
                            return frame
                    else:
                        self._reset_search()

                # 沒有距離/目標：做原地逐格搜索
                if not self.rotation_active:
                    self.rotation_active = True
                    self.search_start_time = time.time()
                    self.search_angle = 0
                    logger.info("🔄 開始原地搜索...")

                if self.search_angle < 360:
                    command = "right"
                    self.send_command(command)
                    self._record_move(command, self.turn_step_duration)
                    self._start_wait(self.turn_step_duration, RobotState.SEARCH_AND_MOVE)
                    self.search_angle += self.turn_step_deg
                    return frame
                else:
                    # 搜索 360° 仍找不到 → 回到 IDLE
                    self._reset_search()
                    self.state = RobotState.IDLE

            # --- OBSTACLE_AVOID（新增：障礙物繞行）---
            elif self.state == RobotState.OBSTACLE_AVOID:
                logger.info(f"🔄 狀態：OBSTACLE_AVOID - 繞行中... {self.avoid_step_count + 1}/{self.max_avoid_steps}")

                # 繞行策略：右 → 前 → 左（回到原路線）
                avoid_pattern = ["right", "forward", "left"]

                if self.avoid_step_count < self.max_avoid_steps:
                    command = avoid_pattern[self.avoid_step_count % len(avoid_pattern)]
                    self.send_command(command)
                    self._record_move(command, self.move_pulse_duration)
                    self._start_wait(self.move_pulse_duration, RobotState.OBSTACLE_AVOID)
                    self.avoid_step_count += 1
                    return frame
                else:
                    # 繞行完成，重置標誌，回到搜索移動狀態
                    self.obstacle_detected = False
                    self.avoid_step_count = 0
                    self.state = RobotState.SEARCH_AND_MOVE
                    logger.info("✅ 繞行完成，繼續朝球移動")
                    return frame

            # --- PICK_UP ---
            elif self.state == RobotState.PICK_UP:
                logger.info("🤲 狀態：PICK_UP - 執行抓取...")
                self.send_command("pick")
                self._start_wait(1.0, RobotState.RETURN_BALL)

            # --- RETURN_BALL ---
            elif self.state == RobotState.RETURN_BALL:
                logger.info("🔙 狀態：RETURN_BALL - 帶球返回...")
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
                    logger.info("✅ 返程完成，切換回 Manual 模式")

        # ==== 疊字資訊（所有模式共用）====
        cv2.putText(frame, f"State: {self.state.name}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        if dis is not None:
            color = (0, 255, 0) if dis > self.obstacle_depth_threshold else (0, 0, 255)
            cv2.putText(frame, f"Depth: {dis:.1f} (0-255)", (10, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

        if self.obstacle_detected:
            cv2.putText(frame, f"Avoid: {self.avoid_step_count + 1}/{self.max_avoid_steps}",
                        (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        fps = 1.0 / (time.time() - self.wait_until_time + 0.001) if hasattr(self, 'wait_until_time') else 0
        cv2.putText(frame, f"FPS: {fps:.1f}", (W - 100, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

        return frame


# ============================================================================
# Class 4: FramePusher - 推流到 RTSP
# ============================================================================
class FramePusher:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.ffmpeg_process = None
        self.is_initialized = False

    def start(self, width, height, fps):
        if self.is_initialized:
            logger.warning("⚠️ FramePusher has already been started!")
            return

        logger.info(f"📡 FramePusher started, target RTSP: {self.rtsp_url}")

        self.ffmpeg_process = (
            ffmpeg
            .input(
                'pipe:',
                format='rawvideo',
                pix_fmt='bgr24',
                s=f'{width}x{height}',
                framerate=fps
            )
            .output(
                self.rtsp_url,
                vcodec='libx264',  # CPU 編碼，避免 nvenc 問題
                preset='ultrafast',
                tune='zerolatency',
                pix_fmt='yuv420p',
                g=fps,  # keyframe interval
                f='rtsp',
                rtsp_transport='tcp',  # 一定要用 TCP
                r=fps,
                b='2000k',  # bitrate
            )
            .global_args('-re')
            .run_async(pipe_stdin=True)
        )
        self.is_initialized = True

    def push(self, frame):
        if self.ffmpeg_process is None:
            return

        if self.ffmpeg_process.poll() is not None:
            logger.error("❌ FFmpeg 已停止，跳過 push")
            self.stop()
            return

        # === 確保 frame 是三通道 BGR ===
        if len(frame.shape) == 2:
            logger.warning("⚠️ push 收到單通道 frame，轉換為三通道")
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        # === 確保 dtype 是 uint8 ===
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)

        try:
            self.ffmpeg_process.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError) as e:
            logger.error(f"❌ FFmpeg pipe error: {e}, stopping FramePusher...")
            self.stop()

    def stop(self):
        if self.ffmpeg_process:
            try:
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
            except Exception as e:
                logger.warning(f"⚠️ close FFmpeg, Error: {e}")
            finally:
                self.ffmpeg_process = None
                self.is_initialized = False
        logger.info("🛑 FramePusher stopped")


# ============================================================================
# Class 5: WebUI - Flask 網頁控制台
# ============================================================================
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
                <!DOCTYPE html>
                <html lang="zh-TW">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=0.67">
                    <title>🤖 機器人控制台</title>
                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.1/dist/css/bootstrap.min.css" rel="stylesheet">
                    <style>
                        body {
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            min-height: 100vh;
                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        }
                        .container {
                            background: white;
                            border-radius: 20px;
                            padding: 20px;
                            margin-top: 20px;
                            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                        }
                        .stream {
                            border: 3px solid #667eea;
                            border-radius: 15px;
                            width: 100%;
                            max-width: 700px;
                            height: auto;
                        }
                        .btn-custom {
                            font-size: 24px;
                            padding: 15px 30px;
                            border-radius: 12px;
                            font-weight: bold;
                        }
                        .status-badge {
                            font-size: 14px;
                            padding: 5px 15px;
                            border-radius: 20px;
                        }
                    </style>
                </head>
                <body>
                    <div class="container text-center">
                        <h1 class="mb-4">🤖 AI Agent Robot Control</h1>

                        <div class="mb-4">
                            <img src="{{ url_for('video_feed') }}" class="stream" alt="Video Stream">
                        </div>

                        <div class="row justify-content-center mb-4">
                            <div class="col-auto">
                                <button id="startBtn" class="btn btn-success btn-custom me-3">▶️ START</button>
                            </div>
                            <div class="col-auto">
                                <button id="stopBtn" class="btn btn-danger btn-custom">⏹️ RESET</button>
                            </div>
                        </div>

                        <div class="text-muted">
                            <span class="status-badge bg-success text-white">● Online</span>
                            <small class="ms-3">v1.0.0 | Mode: <span id="modeDisplay">Manual</span></small>
                        </div>
                    </div>

                    <script>
                        document.getElementById('startBtn').addEventListener('click', async () => {
                            const res = await fetch('/set_mode/Auto', {method: 'POST'});
                            const data = await res.json();
                            document.getElementById('modeDisplay').textContent = 'Auto';
                            alert(data.message);
                        });
                        document.getElementById('stopBtn').addEventListener('click', async () => {
                            const res = await fetch('/set_mode/Manual', {method: 'POST'});
                            const data = await res.json();
                            document.getElementById('modeDisplay').textContent = 'Manual';
                            alert(data.message);
                        });
                    </script>
                </body>
                </html>
            """)

        @self.flask_app.route('/video_feed')
        def video_feed():
            return Response(self.generate_mjpeg_stream(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

        @self.flask_app.route('/set_mode/<new_mode>', methods=['POST'])
        def set_mode(new_mode):
            try:
                if new_mode in ("Auto", "Manual"):
                    with open("shared_config.py", "w") as f:
                        f.write(f'mode = "{new_mode}"\n')
                    logger.info(f"🔄 模式已切換為 [{new_mode}]")
                    return jsonify({"status": "success", "message": f"✅ 模式已切換為 [{new_mode}]"})
                else:
                    return jsonify({"status": "error", "message": "❌ 未知的模式"}), 400
            except Exception as e:
                logger.error(f"❌ 切換模式失敗：{e}")
                return jsonify({"status": "error", "message": str(e)}), 500

    def run(self):
        logger.info("🌐 Web UI started on http://0.0.0.0:5000")
        try:
            self.flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
        except Exception as e:
            if not self.stop_event.is_set():
                logger.error(f"❌ Web UI encountered an error: {e}")

    def generate_mjpeg_stream(self):
        logger.info("📹 WebUI video stream generator started")
        while not self.stop_event.is_set():
            jpeg_frame = None
            with global_frame_lock:
                if global_jpeg_output is not None:
                    jpeg_frame = global_jpeg_output
            if jpeg_frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg_frame + b'\r\n')
            time.sleep(1 / 15)


# ============================================================================
# 主程式
# ============================================================================
if __name__ == "__main__":
    logger.info("🚀 啟動機器人控制系統...")

    # ====== 集中配置（改 IP 只改這裡）======
    PI_IP = "10.121.187.243"  # ← 你的樹莓派實際 IP
    RTSP_IN = f"rtsp://{PI_IP}:8554/mystream"
    RTSP_OUT = f"rtsp://{PI_IP}:8554/result"
    CONTROL_URL = f"http://{PI_IP}:5000/control"

    # 設定全域 CONTROL_URL
    import sys

    sys.modules[__name__].CONTROL_URL = CONTROL_URL
    logger.info(f"🔗 控制 URL: {CONTROL_URL}")

    # 初始化佇列與事件
    stop_event = threading.Event()
    frame_queue = queue.Queue(maxsize=1)
    result_queue = queue.Queue(maxsize=1)

    # 初始化元件
    reader = FrameReader(RTSP_IN, frame_queue, stop_event)
    worker = InferenceWorker(frame_queue, result_queue, stop_event)
    controller = StateMachineController()
    pusher = FramePusher(rtsp_url=RTSP_OUT)
    web_ui = WebUI(stop_event)

    # 啟動執行緒
    reader.start()
    worker.start()
    web_ui.start()

    logger.info("✅ 所有執行緒已啟動，等待影像處理...")

    try:
        while not stop_event.is_set():
            try:
                result = result_queue.get(timeout=1)

                # 初始化推流器（第一次拿到 frame 時）
                if not pusher.is_initialized:
                    h, w, _ = result["frame"].shape
                    pusher.start(width=w, height=h, fps=10)

                # 執行狀態機
                frame = controller.step(result)

                # 推流（若不想推流可註解此行）
                pusher.push(frame)

                # 更新 WebUI MJPEG
                ret, jpeg_buffer = cv2.imencode('.jpg', frame,
                                                [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ret:
                    with global_frame_lock:
                        global_jpeg_output = jpeg_buffer.tobytes()

            except queue.Empty:
                continue

    except KeyboardInterrupt:
        logger.info("⌨️  Ctrl+C pressed, exiting...")
    finally:
        logger.info("🛑 Stopping all threads...")
        stop_event.set()
        pusher.stop()
        logger.info("✅ 程式結束")