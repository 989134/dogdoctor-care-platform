#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import cv2
import time
import queue
import pickle
import logging
import threading
import importlib
from datetime import datetime
from collections import deque
from enum import Enum

import numpy as np
import mediapipe as mp
import requests
from flask import Flask, Response, jsonify, render_template_string, request, send_file
from flask_cors import CORS

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ===== 匯入原本模組 =====
from depth_model import load_depth_anything, estimate_depth
from yolo_model import load_yolo, detect_objects
from draw_yolo_detections import draw_yolo_detections
from smooth_ball_data import smooth_ball_data
from move_decision_raw import move_decision_raw
import shared_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(threadName)s - %(message)s")
logger = logging.getLogger(__name__)

# =========================
# 基本設定
# =========================
APP_TITLE = os.getenv("APP_TITLE", "DogDoctor 居家照護機器人")
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "5000"))

STREAM_URL = os.getenv("STREAM_URL", "rtsp://10.121.187.243:8554/mystream")
ROBOT_CONTROL_URL = os.getenv("ROBOT_CONTROL_URL", "http://10.121.187.243:5000/control")
SNAPSHOT_FILE = os.getenv("SNAPSHOT_FILE", "latest_snapshot.jpg")

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

MEDICATION_TIMES = [t.strip() for t in os.getenv("MEDICATION_TIMES", "08:00,12:00,20:00").split(",") if t.strip()]
FALL_DURATION_THRESHOLD = float(os.getenv("FALL_DURATION_THRESHOLD", "3.0"))
IMMOBILE_DURATION_THRESHOLD = float(os.getenv("IMMOBILE_DURATION_THRESHOLD", "30.0"))
OBSTACLE_WARNING_DEPTH = float(os.getenv("OBSTACLE_WARNING_DEPTH", "60"))
OBSTACLE_STOP_DEPTH = float(os.getenv("OBSTACLE_STOP_DEPTH", "40"))

# ===== 選配：SOS 觸控按鈕 =====
ENABLE_SOS_BUTTON = os.getenv("ENABLE_SOS_BUTTON", "1") == "1"
SOS_GPIO_PIN = int(os.getenv("SOS_GPIO_PIN", "17"))

try:
    from gpiozero import Button
    GPIOZERO_AVAILABLE = True
except Exception:
    GPIOZERO_AVAILABLE = False

# =========================
# 第三方服務
# =========================
LINE_ENABLED = False
line_bot_api = None
TextSendMessage = None

if LINE_CHANNEL_ACCESS_TOKEN:
    try:
        from linebot import LineBotApi
        from linebot.models import TextSendMessage
        line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
        LINE_ENABLED = True
    except Exception as e:
        logger.warning(f"LINE 初始化失敗: {e}")
        LINE_ENABLED = False

GEMINI_ENABLED = False
gemini_model = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel("gemini-2.5-flash")
        GEMINI_ENABLED = True
    except Exception as e:
        logger.warning(f"Gemini 初始化失敗: {e}")
        GEMINI_ENABLED = False

# =========================
# 狀態定義
# =========================
class RobotState(Enum):
    IDLE = 0
    SEARCH_AND_MOVE = 1
    PICK_UP = 2
    RETURN_BALL = 3
    WAITING = 4

latest_frame_lock = threading.Lock()
latest_jpeg = None

latest_status_lock = threading.Lock()
latest_status = {
    "mode": "Manual",
    "state": "IDLE",
    "ball_detected": False,
    "ball_depth": None,
    "fall_alert": False,
    "fall_duration": 0.0,
    "immobile_alert": False,
    "immobile_duration": 0.0,
    "obstacle": "safe",
    "time": "--:--:--",
    "line_enabled": LINE_ENABLED,
    "gemini_enabled": GEMINI_ENABLED,
    "button_enabled": False,
    "button_alert": False,
    "last_line_ok": None,
    "last_medicine_text": "",
    "system_message": "啟動中",
}

health_status = {
    "fall_alert_sent": False,
    "immobile_alert_sent": False,
    "last_alert_time": 0.0,
    "last_movement_time": time.time(),
    "last_medication_reminder": {},
}

def update_latest_status(**kwargs):
    with latest_status_lock:
        latest_status.update(kwargs)

def get_latest_status_copy():
    with latest_status_lock:
        return dict(latest_status)

def send_line_message(text: str) -> bool:
    if not LINE_ENABLED or line_bot_api is None or TextSendMessage is None:
        logger.warning("LINE 未啟用，訊息未送出")
        update_latest_status(last_line_ok=False, system_message="LINE 未啟用")
        return False
    try:
        line_bot_api.broadcast(TextSendMessage(text=text))
        logger.info(f"LINE 已發送: {text}")
        update_latest_status(last_line_ok=True, system_message="LINE 發送成功")
        return True
    except Exception as e:
        logger.error(f"LINE 發送失敗: {e}")
        update_latest_status(last_line_ok=False, system_message=f"LINE 發送失敗: {e}")
        return False

def post_robot_command(command: str) -> bool:
    try:
        r = requests.post(ROBOT_CONTROL_URL, json={"command": command}, timeout=2.0)
        ok = r.status_code == 200
        if ok:
            logger.info(f"控制成功: {command}")
        else:
            logger.error(f"控制失敗: {command} | {r.text}")
        return ok
    except Exception as e:
        logger.error(f"控制例外: {e}")
        return False

def write_shared_mode(new_mode: str):
    with open("shared_config.py", "w", encoding="utf-8") as f:
        f.write(f'mode = "{new_mode}"\n')

# =========================
# 背景吃藥提醒
# =========================
class MedicationReminderThread(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(name="MedicationReminder", daemon=True)
        self.stop_event = stop_event

    def run(self):
        logger.info("MedicationReminder 啟動")
        while not self.stop_event.is_set():
            try:
                current_time = datetime.now().strftime("%H:%M")
                current_date = datetime.now().strftime("%Y-%m-%d")
                if current_time in MEDICATION_TIMES:
                    key = f"{current_date}_{current_time}"
                    if key not in health_status["last_medication_reminder"]:
                        text = f"💊 吃藥提醒：現在是 {current_time}，請記得服藥"
                        send_line_message(text)
                        health_status["last_medication_reminder"][key] = time.time()
                        update_latest_status(last_medicine_text=text)
                time.sleep(10)
            except Exception as e:
                logger.error(f"MedicationReminder 例外: {e}")
                time.sleep(3)

# =========================
# SOS 按鈕背景執行緒
# =========================
class SosButtonThread(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(name="SosButtonThread", daemon=True)
        self.stop_event = stop_event
        self.button = None

    def run(self):
        if not ENABLE_SOS_BUTTON or not GPIOZERO_AVAILABLE:
            logger.info("SOS 按鈕未啟用")
            update_latest_status(button_enabled=False)
            return

        try:
            self.button = Button(SOS_GPIO_PIN, pull_up=False)
            update_latest_status(button_enabled=True, system_message="SOS 按鈕已啟用")
            logger.info(f"SOS 按鈕已啟用 GPIO{SOS_GPIO_PIN}")

            while not self.stop_event.is_set():
                if self.button.is_pressed:
                    update_latest_status(button_alert=True, system_message="SOS 按鈕觸發")
                    send_line_message("🚨 SOS 緊急按鈕已觸發，請立即查看長輩狀況")
                    time.sleep(1.5)
                    update_latest_status(button_alert=False)
                time.sleep(0.1)

        except Exception as e:
            logger.error(f"SOS 按鈕啟動失敗: {e}")
            update_latest_status(button_enabled=False, system_message=f"SOS 按鈕啟動失敗: {e}")

# =========================
# 影像讀取
# =========================
class FrameReader:
    def __init__(self, stream_url, frame_queue, stop_event):
        self.stream_url = stream_url
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="FrameReader", daemon=True)
        self.K, self.D = None, None
        self.map1, self.map2 = None, None
        self.initialized = False

        try:
            with open("calibration_data.pkl", "rb") as f:
                data = pickle.load(f)
            self.K, self.D = data["K"], data["D"]
            logger.info("成功載入 calibration_data.pkl")
        except Exception as e:
            logger.warning(f"未載入校正檔: {e}")

    def start(self):
        self.thread.start()

    def _open_cap(self):
        cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self):
        logger.info(f"FrameReader 啟動: {self.stream_url}")
        cap = self._open_cap()
        if not cap.isOpened():
            logger.error(f"無法開啟 RTSP 串流: {self.stream_url}")
            update_latest_status(system_message="RTSP 串流開啟失敗")
            self.stop_event.set()
            return

        while not self.stop_event.is_set():
            cap.grab()
            ret, frame = cap.retrieve()
            if not ret:
                logger.warning("讀影像失敗，1 秒後重連")
                time.sleep(1)
                cap.release()
                cap = self._open_cap()
                continue

            if (not self.initialized) and (self.K is not None and self.D is not None):
                h, w = frame.shape[:2]
                K_scaled = self.K.copy()
                K_scaled[0, 0] *= (w / 640)
                K_scaled[0, 2] *= (w / 640)
                K_scaled[1, 1] *= (h / 480)
                K_scaled[1, 2] *= (h / 480)
                try:
                    K_new = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                        K_scaled, self.D, (w, h), np.eye(3), balance=0.0
                    )
                    self.map1, self.map2 = cv2.fisheye.initUndistortRectifyMap(
                        K_scaled, self.D, np.eye(3), K_new, (w, h), cv2.CV_16SC2
                    )
                    self.initialized = True
                except Exception as e:
                    logger.warning(f"去畸變初始化失敗: {e}")

            if self.map1 is not None and self.map2 is not None:
                try:
                    frame = cv2.remap(frame, self.map1, self.map2, interpolation=cv2.INTER_LINEAR)
                except Exception as e:
                    logger.warning(f"去畸變失敗: {e}")

            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass
            self.frame_queue.put(frame)

        cap.release()
        logger.info("FrameReader 停止")

# =========================
# 推論執行緒
# =========================
class InferenceWorker:
    def __init__(self, frame_queue, result_queue, stop_event):
        self.frame_queue = frame_queue
        self.result_queue = result_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="InferenceWorker", daemon=True)

        logger.info("載入 YOLO 與 DepthAnything...")
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
        while not self.stop_event.is_set():
            try:
                frame = self.frame_queue.get(timeout=1)
            except queue.Empty:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            hand_results = self.hands.process(rgb)
            detections = detect_objects(self.yolo_model, frame)
            depth_map = estimate_depth(self.depth_model, frame)

            result = {
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
            self.result_queue.put_nowait(result)

        self.hands.close()
        logger.info("InferenceWorker 停止")

# =========================
# 狀態機控制
# =========================
class StateMachineController:
    def __init__(self):
        self.state = RobotState.IDLE
        self.rotation_active = False
        self.search_angle = 0
        self.turn_step_deg = 10
        self.turn_step_duration = 0.09
        self.move_pulse_duration = 0.09
        self.return_plan_ready = False
        self.movement_path = []
        self.movement_command_queue = []
        self.x_window = deque(maxlen=5)
        self.y_window = deque(maxlen=5)
        self.z_window = deque(maxlen=5)

        self.mp_draw = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands
        self.mp_pose = mp.solutions.pose
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        self.wait_until_time = 0.0
        self.next_state_after_wait = None
        self.fall_start_time = None
        self.last_position = None
        self.latest_fall_duration = 0.0
        self.latest_immobile_duration = 0.0

    def close(self):
        self.pose.close()

    def _start_wait(self, duration, next_state):
        self.state = RobotState.WAITING
        self.wait_until_time = time.time() + float(duration)
        self.next_state_after_wait = next_state

    def _record_move(self, command, duration):
        if command in ("forward", "backward", "left", "right", "pre"):
            self.movement_path.append((command, float(duration)))

    def _reset_search(self):
        self.rotation_active = False
        self.search_angle = 0

    @staticmethod
    def get_reverse_command(command):
        return {
            "forward": "backward",
            "backward": "forward",
            "left": "right",
            "right": "left",
            "pre": "re",
        }.get(command)

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

    def _check_fall(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.pose.process(rgb)
        if not results.pose_landmarks:
            self.fall_start_time = None
            self.latest_fall_duration = 0.0
            return False, 0.0

        landmarks = results.pose_landmarks.landmark
        try:
            ls = landmarks[self.mp_pose.PoseLandmark.LEFT_SHOULDER.value]
            rs = landmarks[self.mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
            lh = landmarks[self.mp_pose.PoseLandmark.LEFT_HIP.value]
            rh = landmarks[self.mp_pose.PoseLandmark.RIGHT_HIP.value]

            shoulder_center = ((ls.x + rs.x) / 2, (ls.y + rs.y) / 2)
            hip_center = ((lh.x + rh.x) / 2, (lh.y + rh.y) / 2)
            dx = shoulder_center[0] - hip_center[0]
            dy = shoulder_center[1] - hip_center[1]
            angle = abs(np.arctan2(dx, dy) * 180 / np.pi)
            is_on_ground = hip_center[1] > 0.7

            self.mp_draw.draw_landmarks(frame, results.pose_landmarks, self.mp_pose.POSE_CONNECTIONS)

            if angle < 45 and is_on_ground:
                if self.fall_start_time is None:
                    self.fall_start_time = time.time()
                duration = time.time() - self.fall_start_time
                self.latest_fall_duration = duration
                return duration >= FALL_DURATION_THRESHOLD, duration

            self.fall_start_time = None
            self.latest_fall_duration = 0.0
            return False, 0.0

        except Exception:
            self.fall_start_time = None
            self.latest_fall_duration = 0.0
            return False, 0.0

    def _check_immobile(self, smooth_x, smooth_depth):
        now = time.time()
        if smooth_x is not None and smooth_depth is not None:
            if self.last_position is not None:
                dx = abs(smooth_x - self.last_position[0])
                if dx > 20:
                    health_status["last_movement_time"] = now
            self.last_position = (smooth_x, smooth_depth)
            self.latest_immobile_duration = 0.0
            return False, 0.0

        immobile_duration = now - health_status["last_movement_time"]
        self.latest_immobile_duration = immobile_duration
        return immobile_duration >= IMMOBILE_DURATION_THRESHOLD, immobile_duration

    def _check_obstacle(self, depth_map):
        if depth_map is None:
            return None, "safe"
        h, w = depth_map.shape[:2]
        cy, cx = h // 2, w // 2
        y1 = max(0, cy - 50)
        y2 = min(h, cy + 50)
        x1 = max(0, cx - 50)
        x2 = min(w, cx + 50)
        roi = depth_map[y1:y2, x1:x2]
        if roi.size == 0:
            return None, "safe"
        min_depth = float(np.min(roi))
        if min_depth < OBSTACLE_STOP_DEPTH:
            return "stop", "danger"
        if min_depth < OBSTACLE_WARNING_DEPTH:
            return "avoid", "warning"
        return None, "safe"

    def _draw_big_fall_alert(self, frame, fall_duration):
        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 120), (0, 0, 255), -1)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)
        cv2.putText(frame, "FALL ALERT", (25, 50), cv2.FONT_HERSHEY_DUPLEX, 1.4, (255, 255, 255), 3)
        cv2.putText(frame, f"duration: {fall_duration:.1f}s", (25, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (255, 255, 255), 2)

    def send_command(self, command):
        return post_robot_command(command)

    def step(self, result):
        try:
            importlib.reload(shared_config)
            current_mode = getattr(shared_config, "mode", "Manual")
        except Exception:
            current_mode = "Manual"

        frame = result["frame"]
        detections = result.get("detections")
        depth_map = result.get("depth_map")
        hand_results = result.get("hand_results")

        if detections is None or depth_map is None or hand_results is None:
            return frame

        h, w = frame.shape[:2]
        _, _, smooth_x, smooth_depth = smooth_ball_data(
            detections, depth_map, self.x_window, self.y_window, self.z_window
        )

        ball_detected = smooth_x is not None
        ball_depth_text = f"{smooth_depth:.2f}" if smooth_depth is not None else "--"

        is_falling, fall_duration = self._check_fall(frame)
        if is_falling:
            self._draw_big_fall_alert(frame, fall_duration)
            if not health_status["fall_alert_sent"]:
                now = time.time()
                if now - health_status["last_alert_time"] > 60:
                    send_line_message(f"⚠ 跌倒警報：偵測到疑似跌倒，持續 {fall_duration:.1f} 秒")
                    health_status["fall_alert_sent"] = True
                    health_status["last_alert_time"] = now
        if not is_falling:
            health_status["fall_alert_sent"] = False

        is_immobile, immobile_duration = self._check_immobile(smooth_x, smooth_depth)
        if is_immobile and not health_status["immobile_alert_sent"]:
            now = time.time()
            if now - health_status["last_alert_time"] > 60:
                send_line_message(f"⚠ 久未活動提醒：已超過 {immobile_duration:.0f} 秒")
                health_status["immobile_alert_sent"] = True
                health_status["last_alert_time"] = now
        if not is_immobile:
            health_status["immobile_alert_sent"] = False

        obstacle_command, obstacle_status = self._check_obstacle(depth_map)

        frame = draw_yolo_detections(frame, detections, {0: "Ping Pong Ball", 1: "Person"})
        if hand_results.multi_hand_landmarks:
            for hand in hand_results.multi_hand_landmarks:
                self.mp_draw.draw_landmarks(frame, hand, self.mp_hands.HAND_CONNECTIONS)

        if current_mode != "Auto":
            self.state = RobotState.IDLE
            self.return_plan_ready = False
            self._reset_search()
            self.movement_path.clear()
            self.movement_command_queue.clear()
            cv2.putText(frame, f"Mode: {current_mode}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.putText(frame, f"Ball Depth: {ball_depth_text}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        else:
            if self.state == RobotState.WAITING:
                if time.time() >= self.wait_until_time:
                    self.state = self.next_state_after_wait
                else:
                    update_latest_status(
                        mode=current_mode,
                        state=self.state.name,
                        ball_detected=ball_detected,
                        ball_depth=ball_depth_text,
                        fall_alert=health_status["fall_alert_sent"],
                        fall_duration=round(self.latest_fall_duration, 1),
                        immobile_alert=health_status["immobile_alert_sent"],
                        immobile_duration=round(self.latest_immobile_duration, 1),
                        obstacle=obstacle_status,
                        time=datetime.now().strftime("%H:%M:%S"),
                    )
                    return frame

            if obstacle_command == "stop":
                self.send_command("stop")
                self._start_wait(0.3, RobotState.SEARCH_AND_MOVE)
            elif obstacle_command == "avoid":
                self.send_command("left")
                self._record_move("left", self.move_pulse_duration)
                self._start_wait(self.move_pulse_duration, RobotState.SEARCH_AND_MOVE)
                return frame

            if self.state == RobotState.IDLE:
                self.state = RobotState.SEARCH_AND_MOVE
                self._reset_search()

            elif self.state == RobotState.SEARCH_AND_MOVE:
                if smooth_depth is not None:
                    command = move_decision_raw(smooth_x, smooth_depth, w)
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
                else:
                    self._reset_search()

                if not self.rotation_active:
                    self.rotation_active = True
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
                    self._reset_search()
                    self.state = RobotState.IDLE

            elif self.state == RobotState.PICK_UP:
                self.send_command("pick")
                self._start_wait(1.0, RobotState.RETURN_BALL)

            elif self.state == RobotState.RETURN_BALL:
                self._plan_return_once()
                if self.movement_command_queue:
                    command, duration = self.movement_command_queue.pop(0)
                    self.send_command(command)
                    self._start_wait(duration, RobotState.RETURN_BALL)
                    return frame
                self.send_command("arrive")
                self._start_wait(1.0, RobotState.IDLE)
                try:
                    write_shared_mode("Manual")
                except Exception:
                    pass
                self.return_plan_ready = False
                self._reset_search()

            cv2.putText(frame, f"State: {self.state.name}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(frame, f"Ball Depth: {ball_depth_text}", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)

        cv2.putText(frame, f"Fall: {'ALERT' if health_status['fall_alert_sent'] else 'OK'}", (10, h - 85), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        cv2.putText(frame, f"Move: {'ALERT' if health_status['immobile_alert_sent'] else 'OK'}", (10, h - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        cv2.putText(frame, f"Obstacle: {obstacle_status}", (10, h - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 255) if obstacle_status != 'safe' else (0, 255, 0), 2)

        try:
            cv2.imwrite(SNAPSHOT_FILE, frame)
        except Exception:
            pass

        update_latest_status(
            mode=current_mode,
            state=self.state.name,
            ball_detected=ball_detected,
            ball_depth=ball_depth_text,
            fall_alert=health_status["fall_alert_sent"],
            fall_duration=round(self.latest_fall_duration, 1),
            immobile_alert=health_status["immobile_alert_sent"],
            immobile_duration=round(self.latest_immobile_duration, 1),
            obstacle=obstacle_status,
            time=datetime.now().strftime("%H:%M:%S"),
        )
        return frame

# =========================
# Web UI
# =========================
class WebUI(threading.Thread):
    def __init__(self, stop_event):
        super().__init__(name="WebUI", daemon=True)
        self.stop_event = stop_event
        self.app = Flask(__name__)
        CORS(self.app)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.route("/")
        def index():
            return render_template_string(self._html())

        @self.app.route("/video_feed")
        def video_feed():
            return Response(self._mjpeg_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @self.app.route("/status")
        def status():
            return jsonify(get_latest_status_copy())

        @self.app.route("/set_mode/<new_mode>", methods=["POST"])
        def set_mode(new_mode):
            if new_mode not in ("Auto", "Manual"):
                return jsonify({"ok": False, "message": "未知模式"}), 400
            try:
                write_shared_mode(new_mode)
                update_latest_status(mode=new_mode, system_message=f"模式切換為 {new_mode}")
                return jsonify({"ok": True, "mode": new_mode})
            except Exception as e:
                return jsonify({"ok": False, "message": str(e)}), 500

        @self.app.route("/robot/<cmd>", methods=["POST"])
        def manual_robot(cmd):
            valid = {"forward", "backward", "left", "right", "stop", "pick", "arrive", "pre", "re"}
            if cmd not in valid:
                return jsonify({"ok": False, "message": "invalid command"}), 400
            ok = post_robot_command(cmd)
            return jsonify({"ok": ok, "command": cmd})

        @self.app.route("/ai", methods=["POST"])
        def ai_chat():
            data = request.get_json(silent=True) or {}
            text = str(data.get("text", "你好")).strip() or "你好"
            if not GEMINI_ENABLED or gemini_model is None:
                return jsonify({"reply": "Gemini 尚未啟用，請確認 .env 與 python-dotenv"})
            try:
                response = gemini_model.generate_content(text)
                return jsonify({"reply": getattr(response, "text", "Gemini 沒有回應文字")})
            except Exception as e:
                return jsonify({"reply": f"Gemini 錯誤：{e}"})

        @self.app.route("/test_line")
        def test_line():
            ok = send_line_message("LINE 測試成功：系統目前正常運作")
            return jsonify({"ok": ok})

        @self.app.route("/health/medication", methods=["POST"])
        def medication():
            data = request.get_json(silent=True) or {}
            medicine = str(data.get("medicine", "藥物")).strip() or "藥物"
            text = f"💊 吃藥提醒：{medicine}"
            ok = send_line_message(text)
            update_latest_status(last_medicine_text=text)
            return jsonify({"ok": ok, "medicine": medicine})

        @self.app.route("/health/emergency", methods=["POST"])
        def emergency():
            ok = send_line_message("🚨 緊急求助：請立即查看長輩目前狀況")
            return jsonify({"ok": ok})

        @self.app.route("/snapshot")
        def snapshot():
            if os.path.exists(SNAPSHOT_FILE):
                return send_file(SNAPSHOT_FILE, mimetype="image/jpeg")
            return jsonify({"ok": False, "message": "尚未產生 snapshot"}), 404

    def _mjpeg_stream(self):
        while not self.stop_event.is_set():
            jpeg = None
            with latest_frame_lock:
                if latest_jpeg is not None:
                    jpeg = latest_jpeg
            if jpeg:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(1 / 15)

    def _html(self):
        return f"""
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>{APP_TITLE}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;background:#0f172a;color:#fff;padding:12px}}
.wrap{{max-width:1380px;margin:0 auto}}
.card{{background:#111827;border:1px solid #334155;border-radius:18px;padding:14px;margin-bottom:14px;box-shadow:0 10px 30px rgba(0,0,0,.2)}}
h1{{font-size:30px;margin-bottom:8px}}
h3{{margin-bottom:10px}}
.sub{{color:#cbd5e1;font-size:14px}}
.grid{{display:grid;grid-template-columns:1.5fr 1fr;gap:14px}}
@media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
.video{{position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:14px;overflow:hidden}}
.video img{{width:100%;height:100%;object-fit:cover}}
.status{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}}
@media (max-width:800px){{.status{{grid-template-columns:repeat(2,1fr)}}}}
@media (max-width:520px){{.status{{grid-template-columns:1fr}}}}
.pill{{background:#1e293b;border-radius:14px;padding:12px 12px;font-size:14px;text-align:center}}
.pill.alert{{background:#7f1d1d;border:1px solid #ef4444}}
.pill.ok{{background:#14532d;border:1px solid #22c55e}}
.btns{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-top:12px}}
.btn{{border:none;border-radius:12px;padding:14px 10px;color:#fff;font-size:16px;font-weight:700;cursor:pointer}}
.btn.green{{background:#16a34a}} .btn.red{{background:#dc2626}} .btn.blue{{background:#2563eb}} .btn.gray{{background:#475569}} .btn.orange{{background:#ea580c}}
.manual{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.chatlog{{height:260px;overflow:auto;background:#020617;border-radius:12px;padding:10px;border:1px solid #334155}}
.msg{{padding:10px 12px;border-radius:12px;margin:8px 0;font-size:14px;line-height:1.5}}
.user{{background:#1d4ed8}} .ai{{background:#166534}}
.row{{display:flex;gap:8px;margin-top:10px}}
input{{flex:1;border:none;border-radius:12px;padding:12px;background:#0f172a;color:#fff;border:1px solid #334155}}
.small{{font-size:13px;color:#94a3b8;margin-top:8px;line-height:1.6}}
.big-alert{{
 display:none;position:fixed;top:14px;right:14px;z-index:9999;
 min-width:320px;max-width:88vw;padding:18px 20px;border-radius:18px;
 background:linear-gradient(135deg,#b91c1c,#ef4444);color:#fff;
 box-shadow:0 20px 50px rgba(0,0,0,.35);border:2px solid rgba(255,255,255,.2);
 animation:flash 0.9s infinite alternate;
}}
.big-alert h2{{font-size:28px;margin-bottom:8px}}
.big-alert p{{font-size:16px;line-height:1.5}}
@keyframes flash {{
 from {{ transform:scale(1); opacity:1; }}
 to {{ transform:scale(1.02); opacity:.85; }}
}}
.badge{{display:inline-block;padding:6px 10px;border-radius:999px;font-size:12px;font-weight:700;margin-right:6px;background:#1e293b}}
.badge.on{{background:#14532d;color:#d1fae5}}
.badge.off{{background:#7f1d1d;color:#fee2e2}}
</style>
</head>
<body>
<div id="fallAlertBox" class="big-alert">
  <h2>⚠ 跌倒警報</h2>
  <p id="fallAlertText">偵測到疑似跌倒，已啟動即時警示。</p>
</div>

<div class="wrap">
  <div class="card">
    <h1>{APP_TITLE}</h1>
    <div class="sub">乒乓球追蹤 / LINE Bot / Gemini AI / 健康提醒 / Web UI / RTSP 串流</div>
    <div style="margin-top:10px;">
      <span id="badgeLine" class="badge off">LINE 未啟用</span>
      <span id="badgeGemini" class="badge off">Gemini 未啟用</span>
      <span id="badgeButton" class="badge off">SOS 按鈕未啟用</span>
    </div>
  </div>

  <div class="grid">
    <div>
      <div class="card">
        <div class="video"><img src="/video_feed" alt="video"></div>
        <div class="status">
          <div class="pill" id="mode">模式：--</div>
          <div class="pill" id="state">狀態：--</div>
          <div class="pill" id="ball">球：--</div>
          <div class="pill" id="depth">深度：--</div>
          <div class="pill" id="fall">跌倒：--</div>
          <div class="pill" id="immobile">久未活動：--</div>
          <div class="pill" id="obstacle">避障：--</div>
          <div class="pill" id="clock">時間：--</div>
          <div class="pill" id="buttonState">SOS按鈕：--</div>
          <div class="pill" id="systemMsg" style="grid-column:span 2;">系統訊息：--</div>
        </div>
        <div class="btns">
          <button class="btn green" onclick="setMode('Auto')">自動模式</button>
          <button class="btn red" onclick="setMode('Manual')">手動模式</button>
          <button class="btn blue" onclick="testLine()">測試 LINE</button>
          <button class="btn orange" onclick="sendEmergency()">緊急求助</button>
        </div>
      </div>

      <div class="card">
        <h3>手動控制</h3>
        <div class="manual">
          <button class="btn gray" onclick="cmd('forward')">前進</button>
          <button class="btn gray" onclick="cmd('pre')">快前</button>
          <button class="btn gray" onclick="cmd('backward')">後退</button>
          <button class="btn gray" onclick="cmd('left')">左轉</button>
          <button class="btn red" onclick="cmd('stop')">停止</button>
          <button class="btn gray" onclick="cmd('right')">右轉</button>
          <button class="btn blue" onclick="cmd('pick')">撿球</button>
          <button class="btn blue" onclick="cmd('arrive')">到達</button>
          <button class="btn gray" onclick="cmd('re')">回退快走</button>
        </div>
      </div>
    </div>

    <div>
      <div class="card">
        <h3>Gemini AI 助手</h3>
        <div class="chatlog" id="chatlog">
          <div class="msg ai">你好，我是居家照護助手。</div>
        </div>
        <div class="row">
          <input id="msg" placeholder="輸入訊息..." onkeypress="if(event.key==='Enter')sendChat()">
          <button class="btn blue" onclick="sendChat()">送出</button>
        </div>
      </div>

      <div class="card">
        <h3>健康提醒</h3>
        <div class="btns">
          <button class="btn green" onclick="sendMedication('早上藥')">早上吃藥</button>
          <button class="btn green" onclick="sendMedication('中午藥')">中午吃藥</button>
          <button class="btn green" onclick="sendMedication('晚上藥')">晚上吃藥</button>
          <button class="btn blue" onclick="window.open('/snapshot', '_blank')">查看截圖</button>
        </div>
        <div class="small" id="medicineInfo">提醒時間：{", ".join(MEDICATION_TIMES)}</div>
      </div>
    </div>
  </div>
</div>

<script>
function setMode(mode){{fetch('/set_mode/'+mode,{{method:'POST'}}).then(r=>r.json()).then(d=>alert(d.ok?'已切換到 '+mode:(d.message||'失敗')))}}
function cmd(c){{fetch('/robot/'+c,{{method:'POST'}}).then(r=>r.json()).then(d=>console.log(d))}}
function testLine(){{fetch('/test_line').then(r=>r.json()).then(d=>alert(d.ok?'LINE 測試成功':'LINE 未啟用或失敗'))}}
function sendEmergency(){{fetch('/health/emergency',{{method:'POST'}}).then(r=>r.json()).then(d=>alert(d.ok?'已送出緊急求助':'送出失敗'))}}
function sendMedication(m){{fetch('/health/medication',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{medicine:m}})}}).then(r=>r.json()).then(d=>alert(d.ok?'提醒已送出':'提醒失敗'))}}
function addMsg(cls,text){{const log=document.getElementById('chatlog');log.innerHTML += `<div class="msg ${{cls}}">${{text}}</div>`;log.scrollTop=log.scrollHeight;}}
function sendChat(){{const inp=document.getElementById('msg');const text=inp.value.trim();if(!text)return;addMsg('user','你：'+text);inp.value='';fetch('/ai',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{text:text}})}}).then(r=>r.json()).then(d=>addMsg('ai','助手：'+d.reply));}}
function setPill(id,text,alertState=false){{
  const el=document.getElementById(id);
  el.innerText=text;
  el.className='pill ' + (alertState ? 'alert' : 'ok');
}}
function setBadge(id,text,on){{
  const el=document.getElementById(id);
  el.innerText=text;
  el.className='badge ' + (on ? 'on':'off');
}}
function refreshStatus(){{
  fetch('/status').then(r=>r.json()).then(d=>{{
    document.getElementById('mode').innerText='模式：'+(d.mode||'--');
    document.getElementById('state').innerText='狀態：'+(d.state||'--');
    document.getElementById('ball').innerText='球：'+(d.ball_detected?'YES':'NO');
    document.getElementById('depth').innerText='深度：'+(d.ball_depth??'--');
    setPill('fall','跌倒：'+(d.fall_alert?('ALERT / '+(d.fall_duration||0)+'s'):'OK'), !!d.fall_alert);
    setPill('immobile','久未活動：'+(d.immobile_alert?('ALERT / '+(d.immobile_duration||0)+'s'):'OK'), !!d.immobile_alert);
    setPill('obstacle','避障：'+(d.obstacle||'safe'), (d.obstacle||'safe')!=='safe');
    document.getElementById('clock').innerText='時間：'+(d.time||'--');
    setPill('buttonState','SOS按鈕：'+(d.button_alert?'觸發':'待命'), !!d.button_alert);
    document.getElementById('systemMsg').innerText='系統訊息：'+(d.system_message||'--');
    document.getElementById('medicineInfo').innerText='提醒時間：'+(d.medicine_times||[]).join(', ')+' ｜ 最近提醒：'+(d.last_medicine_text||'--');
    setBadge('badgeLine','LINE '+(d.line_enabled?'已啟用':'未啟用'), !!d.line_enabled);
    setBadge('badgeGemini','Gemini '+(d.gemini_enabled?'已啟用':'未啟用'), !!d.gemini_enabled);
    setBadge('badgeButton','SOS按鈕 '+(d.button_enabled?'已啟用':'未啟用'), !!d.button_enabled);

    const box=document.getElementById('fallAlertBox');
    const boxText=document.getElementById('fallAlertText');
    if(d.fall_alert){{
      box.style.display='block';
      boxText.innerText='偵測到疑似跌倒，持續 '+(d.fall_duration||0)+' 秒，請立即查看。';
    }} else {{
      box.style.display='none';
    }}
  }}).catch(err=>console.log(err));
}}
setInterval(refreshStatus, 1500); refreshStatus();
</script>
</body>
</html>
"""

    def run(self):
        logger.info("Web UI 啟動：http://%s:%s", WEB_HOST, WEB_PORT)
        self.app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)

# =========================
# 主程式
# =========================
def main():
    logger.info("=" * 70)
    logger.info("%s 啟動", APP_TITLE)
    logger.info("RTSP 來源：%s", STREAM_URL)
    logger.info("控制端點：%s", ROBOT_CONTROL_URL)
    logger.info("Web UI：http://localhost:%s", WEB_PORT)
    logger.info("=" * 70)

    stop_event = threading.Event()
    frame_queue = queue.Queue(maxsize=1)
    result_queue = queue.Queue(maxsize=1)

    reader = FrameReader(STREAM_URL, frame_queue, stop_event)
    worker = InferenceWorker(frame_queue, result_queue, stop_event)
    controller = StateMachineController()
    web_ui = WebUI(stop_event)
    medicine_thread = MedicationReminderThread(stop_event)
    sos_thread = SosButtonThread(stop_event)

    reader.start()
    worker.start()
    web_ui.start()
    medicine_thread.start()
    sos_thread.start()

    try:
        while not stop_event.is_set():
            try:
                result = result_queue.get(timeout=1)
            except queue.Empty:
                continue

            frame = controller.step(result)
            ret, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
            if ret:
                global latest_jpeg
                with latest_frame_lock:
                    latest_jpeg = buf.tobytes()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，準備結束")
    finally:
        stop_event.set()
        controller.close()
        logger.info("系統已關閉")

if __name__ == "__main__":
    main()