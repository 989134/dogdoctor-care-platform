#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import queue
import threading
import time
from pathlib import Path
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import requests
from flask import Flask, Response, jsonify, render_template_string, request

try:
    from flask_cors import CORS
except Exception:
    CORS = None

import config
import shared_config
from depth_model import estimate_depth, load_depth_anything
from draw_yolo_detections import draw_yolo_detections
from move_decision_raw import move_decision_raw
from smooth_ball_data import smooth_ball_data
from yolo_model import detect_objects, load_yolo

try:
    from audio_guardian import AudioGuardian
except Exception:
    AudioGuardian = None

try:
    from emergency_monitor import EmergencyMonitor
except Exception:
    EmergencyMonitor = None

from care_service import SERVICE as care_service
from care_web import create_blueprint
from care_speaker import CareSpeaker, watch_service

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

CONTROL_URL = config.ROBOT_CONTROL_URL
GLOBAL_FRAME_LOCK = threading.Lock()
GLOBAL_JPEG = None
STOP_EVENT = threading.Event()

STATE: Dict[str, Any] = {
    "system_mode": getattr(config, "DEFAULT_SYSTEM_MODE", "CARE").upper(),
    "drive_mode": getattr(shared_config, "mode", "Manual"),
    "last_command": "stop",
    "last_command_time": "",
    "line_enabled": False,
    "gemini_enabled": False,
    "ball_detected": False,
    "ball_depth": None,
    "person_detected": False,
    "person_depth": None,
    "obstacle_depth": None,
    "ultrasonic_cm": None,
    "last_movement_time": time.time(),
    "fall_alert_sent": False,
    "immobile_alert_sent": False,
    "last_alert_text": "",
    "last_chat_reply": "",
    "medication_times": list(config.MEDICATION_TIMES),
    "last_medication": "",
    "last_water_reminder": 0.0,
    "started_at": time.time(),
    "audio": {
        "active": False,
        "mic_available": False,
        "vosk_available": False,
        "voice_enabled": bool(config.VOICE_CONTROL_ENABLED),
        "sound_enabled": bool(config.SOUND_DETECTION_ENABLED),
        "last_voice_text": "",
        "last_command": "",
        "last_sound_event": "",
        "last_sound_score": 0.0,
        "last_update": "",
    },
    "emergency": {
        "gpio_available": False,
        "button_enabled": bool(config.EMERGENCY_BUTTON_ENABLED),
        "button_pin": config.EMERGENCY_BUTTON_PIN,
        "button_mode": getattr(config, "EMERGENCY_BUTTON_MODE", "touch"),
        "last_event": "",
        "last_update": "",
        "active": False,
    },
}

BALL_X_BUFFER = deque(maxlen=5)
BALL_DEPTH_BUFFER = deque(maxlen=5)
PERSON_X_BUFFER = deque(maxlen=5)
PERSON_DEPTH_BUFFER = deque(maxlen=5)

LINE_BOT_API = None
TextSendMessage = None
GEMINI_MODEL = None


def init_external_services() -> None:
    global LINE_BOT_API, TextSendMessage, GEMINI_MODEL

    try:
        from linebot import LineBotApi
        from linebot.models import TextSendMessage as _TextSendMessage

        if config.LINE_CHANNEL_ACCESS_TOKEN and config.LINE_USER_ID:
            LINE_BOT_API = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
            TextSendMessage = _TextSendMessage
            STATE["line_enabled"] = True
            logger.info("LINE 初始化成功")
        else:
            logger.info("LINE token 或 LINE_USER_ID 未設定，LINE 功能停用")
    except Exception as exc:
        logger.warning("LINE 初始化失敗：%s", exc)

    try:
        import google.generativeai as genai

        if config.GEMINI_API_KEY:
            genai.configure(api_key=config.GEMINI_API_KEY)
            GEMINI_MODEL = genai.GenerativeModel(config.GEMINI_MODEL_NAME)
            STATE["gemini_enabled"] = True
            logger.info("Gemini 初始化成功")
        else:
            logger.info("GEMINI_API_KEY 未設定，Gemini 功能停用")
    except Exception as exc:
        logger.warning("Gemini 初始化失敗：%s", exc)


def send_line_alert(message: str) -> bool:
    STATE["last_alert_text"] = message
    logger.warning(message)

    if not STATE["line_enabled"] or LINE_BOT_API is None or TextSendMessage is None:
        return False

    try:
        LINE_BOT_API.push_message(config.LINE_USER_ID, TextSendMessage(text=message))
        return True
    except Exception as exc:
        logger.error("LINE 發送失敗：%s", exc)
        return False


def ask_gemini(text: str) -> str:
    if not STATE["gemini_enabled"] or GEMINI_MODEL is None:
        return "Gemini 尚未啟用，請先設定 GEMINI_API_KEY。"

    prompt = (
        "你是居家照護機器人助手，回覆要短、清楚、溫和，"
        "優先提供照護、安全與操作建議。\n"
        f"使用者：{text}"
    )

    try:
        resp = GEMINI_MODEL.generate_content(prompt)
        reply = getattr(resp, "text", "") or "目前無法回覆，請稍後再試。"
        STATE["last_chat_reply"] = reply
        return reply
    except Exception as exc:
        logger.error("Gemini 回覆失敗：%s", exc)
        return f"AI 暫時無法回覆：{exc}"


def write_drive_mode(mode: str) -> None:
    with open("shared_config.py", "w", encoding="utf-8") as f:
        f.write(f'mode = "{mode}"\n')
    STATE["drive_mode"] = mode
    logger.info("drive_mode 已切換為 %s", mode)


def issue_robot_command(command: str) -> bool:
    if not command:
        return False

    if command == "MODE_AUTO":
        write_drive_mode("Auto")
        return True

    if command == "MODE_MANUAL":
        write_drive_mode("Manual")
        return True

    try:
        res = requests.post(CONTROL_URL, json={"command": command}, timeout=1.5)
        ok = bool(res.ok)
        if ok:
            STATE["last_command"] = command
            STATE["last_command_time"] = datetime.now().strftime("%H:%M:%S")
            if command in {"forward", "pre", "left", "right", "backward"}:
                STATE["last_movement_time"] = time.time()
            logger.info("控制指令送出成功：%s", command)
        else:
            logger.warning("控制指令送出失敗 HTTP=%s command=%s", res.status_code, command)
        return ok
    except Exception as exc:
        logger.error("控制指令送出失敗：%s", exc)
        return False


def fetch_ultrasonic_distance() -> Optional[float]:
    if not config.ULTRASONIC_ENABLED:
        return None

    try:
        res = requests.get(config.ULTRASONIC_API_URL, timeout=0.8)
        data = res.json()
        value = data.get("distance_cm") or data.get("distance")
        if value is None and isinstance(data.get("distance_text"), str):
            text = data["distance_text"].split(":")[-1].strip()
            value = float(text)
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def parse_detection(det: Any) -> Tuple[Optional[List[float]], float, Optional[int]]:
    try:
        if isinstance(det, (list, tuple)) and len(det) >= 3:
            box, conf, cls_id = det[0], float(det[1]), int(det[2])
            box = list(map(float, box))
            return box, conf, cls_id
        if isinstance(det, dict):
            box = det.get("box") or det.get("bbox")
            conf = float(det.get("conf", 0.0))
            cls_id = det.get("class_id")
            if box is not None and cls_id is not None:
                return list(map(float, box)), conf, int(cls_id)
    except Exception:
        pass
    return None, 0.0, None


def find_best_detection(detections: List[Any], class_id: int) -> Optional[Tuple[List[float], float, int]]:
    best = None
    best_conf = -1.0
    for det in detections or []:
        box, conf, cls_id = parse_detection(det)
        if box is None or cls_id != class_id:
            continue
        if conf > best_conf:
            best = (box, conf, cls_id)
            best_conf = conf
    return best


def center_depth(depth_map: Optional[np.ndarray], x: float, y: float) -> Optional[float]:
    if depth_map is None:
        return None
    h, w = depth_map.shape[:2]
    xi = max(0, min(w - 1, int(x)))
    yi = max(0, min(h - 1, int(y)))
    return float(depth_map[yi, xi])


def smooth_value(buf: deque, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    buf.append(float(value))
    return float(sum(buf) / len(buf))


def decide_follow_command(person_x: Optional[float], person_depth: Optional[float], frame_w: int) -> Optional[str]:
    if person_x is None or person_depth is None:
        return None

    center = frame_w / 2.0
    tol = float(config.FOLLOW_TOLERANCE_X)

    if person_x < center - tol:
        return "right"
    if person_x > center + tol:
        return "left"
    if person_depth < config.FOLLOW_TARGET_DEPTH_MIN:
        return "pre"
    if person_depth > config.FOLLOW_TARGET_DEPTH_MAX:
        return "stop"
    return "forward"


def decide_obstacle_command(depth_map: Optional[np.ndarray]) -> Optional[str]:
    if depth_map is None or not config.OBSTACLE_DETECTION_ENABLED:
        return None

    h, w = depth_map.shape[:2]
    y1, y2 = int(h * 0.45), int(h * 0.85)
    x1, x2 = int(w * 0.35), int(w * 0.65)

    center = depth_map[y1:y2, x1:x2]
    if center.size == 0:
        return None

    center_mean = float(np.mean(center))
    STATE["obstacle_depth"] = center_mean

    if center_mean >= config.OBSTACLE_STOP_DEPTH:
        return None

    left = float(np.mean(depth_map[y1:y2, : int(w * 0.35)])) if int(w * 0.35) > 0 else 0.0
    right = float(np.mean(depth_map[y1:y2, int(w * 0.65):])) if int(w * 0.35) > 0 else 0.0
    return "left" if left > right else "right"


class FrameReader:
    def __init__(self, stream_url: str, out_queue: queue.Queue, stop_event: threading.Event):
        self.stream_url = stream_url
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="FrameReader", daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        logger.info("FrameReader 啟動，準備開啟串流：%s", self.stream_url)
        cap = cv2.VideoCapture(self.stream_url)

        if not cap.isOpened():
            logger.error("無法開啟串流：%s", self.stream_url)
            return

        logger.info("RTSP 串流已開啟成功")

        while not self.stop_event.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.1)
                continue

            if self.out_queue.full():
                try:
                    self.out_queue.get_nowait()
                except queue.Empty:
                    pass

            # Pose has its own latest-frame worker; slow Depth inference cannot block it.
            # Full application conservatively pauses posture accumulation in Auto mode.
            moving = STATE["drive_mode"] == "Auto" or STATE["last_command"] not in {"stop", ""}
            care_service.submit(frame, camera_moving=moving)
            self.out_queue.put(frame)

        cap.release()
        logger.info("FrameReader 已停止")


class VisionWorker:
    def __init__(self, frame_queue: queue.Queue, stop_event: threading.Event):
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="VisionWorker", daemon=True)
        self.care_notified = set()
        self.care_retry_at = {}

        logger.info("VisionWorker：開始載入 YOLO")
        self.yolo = load_yolo(config.YOLO_MODEL_PATH)
        logger.info("VisionWorker：YOLO 載入完成")

        logger.info("VisionWorker：開始載入 DepthAnything")
        self.depth_model = load_depth_anything(config.DEPTH_MODEL_PATH)
        logger.info("VisionWorker：DepthAnything 載入完成")


    def start(self):
        self.thread.start()

    def _run(self):
        global GLOBAL_JPEG
        target_interval = 1.0 / max(1, config.PROCESS_FPS)

        while not self.stop_event.is_set():
            t0 = time.time()

            try:
                frame = self.frame_queue.get(timeout=1)
            except queue.Empty:
                continue

            depth_map = estimate_depth(self.depth_model, frame)
            detections = detect_objects(self.yolo, frame)
            display = frame.copy()

            try:
                display = draw_yolo_detections(display, detections)
            except Exception:
                pass

            h, w = frame.shape[:2]
            ball = find_best_detection(detections, config.BALL_CLASS_ID)
            person = find_best_detection(detections, config.PERSON_CLASS_ID)

            ball_cmd = None
            person_cmd = None
            obstacle_cmd = decide_obstacle_command(depth_map)
            ultrasonic_cm = fetch_ultrasonic_distance()
            STATE["ultrasonic_cm"] = ultrasonic_cm

            if ball is not None:
                box = ball[0]
                cx = (box[0] + box[2]) / 2.0
                cy = (box[1] + box[3]) / 2.0
                raw_depth = center_depth(depth_map, cx, cy)
                smooth_x = smooth_value(BALL_X_BUFFER, cx)
                smooth_depth = smooth_value(BALL_DEPTH_BUFFER, raw_depth)
                ball_cmd = move_decision_raw(smooth_x, smooth_depth, w)
                STATE["ball_detected"] = True
                STATE["ball_depth"] = smooth_depth
                cv2.putText(
                    display,
                    f"Ball depth: {smooth_depth:.1f}" if smooth_depth is not None else "Ball depth: --",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 0),
                    2,
                )
            else:
                STATE["ball_detected"] = False
                STATE["ball_depth"] = None

            if person is not None:
                box = person[0]
                cx = (box[0] + box[2]) / 2.0
                cy = (box[1] + box[3]) / 2.0
                depth = center_depth(depth_map, cx, cy)
                sx = smooth_value(PERSON_X_BUFFER, cx)
                sd = smooth_value(PERSON_DEPTH_BUFFER, depth)
                person_cmd = decide_follow_command(sx, sd, w)
                STATE["person_detected"] = True
                STATE["person_depth"] = sd
                cv2.putText(
                    display,
                    f"Person depth: {sd:.1f}" if sd is not None else "Person depth: --",
                    (20, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 0),
                    2,
                )
            else:
                STATE["person_detected"] = False
                STATE["person_depth"] = None

            care = care_service.snapshot()
            STATE["care"] = care
            STATE["fall_alert_sent"] = any(e["kind"] in {"fall", "tracking_review"} and e["status"] == "alert" for e in care["active_events"])
            STATE["immobile_alert_sent"] = any(e["kind"] == "immobile" and e["status"] == "alert" for e in care["active_events"])
            for event in care["active_events"]:
                event_id = event["id"]
                event_key = event.get("event_uid") or f'{event["kind"]}:{event_id}'
                if (event["status"] == "alert" and event_key not in self.care_notified
                        and time.monotonic() >= self.care_retry_at.get(event_key, 0)):
                    self.care_retry_at[event_key] = time.monotonic() + 10
                    label = {"fall": "高風險疑似跌倒", "tracking_review": "高風險疑似跌倒（跌倒後失去追蹤）",
                             "immobile": "久未移動", "wandering": "疑似來回徘徊"}[event["kind"]]
                    when=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    reason=event.get('escalation_reason') or '倒數結束前未收到取消或重新站起'
                    message=(f"【居家照護高風險警報】\n時間：{when}\n事件：{label}\n"
                             f"原因：{reason}\n識別碼：{event_key}\n請立即聯絡或前往查看長者狀況。")
                    if send_line_alert(message):
                        self.care_notified.add(event_key)
            cv2.putText(display, "Pose: " + care["posture"], (20, 105),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

            final_cmd = None
            if STATE["drive_mode"] == "Auto":
                mode = STATE["system_mode"]
                if ultrasonic_cm is not None and ultrasonic_cm <= config.ULTRASONIC_STOP_CM:
                    final_cmd = "stop"
                elif obstacle_cmd and mode in {"CARE", "FOLLOW", "BALL"}:
                    final_cmd = obstacle_cmd
                elif mode == "BALL":
                    final_cmd = ball_cmd or "stop"
                elif mode == "FOLLOW":
                    final_cmd = person_cmd or "stop"
                elif mode == "CARE":
                    final_cmd = person_cmd or obstacle_cmd or "stop"

            if final_cmd:
                issue_robot_command(final_cmd)
                cv2.putText(
                    display,
                    f"CMD: {final_cmd}",
                    (20, h - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 0, 255),
                    2,
                )

            ok, buf = cv2.imencode(".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), int(config.JPEG_QUALITY)])
            if ok:
                with GLOBAL_FRAME_LOCK:
                    GLOBAL_JPEG = buf.tobytes()

            spent = time.time() - t0
            if spent < target_interval:
                time.sleep(target_interval - spent)


class ReminderWorker:
    def __init__(self, stop_event: threading.Event):
        self.stop_event = stop_event
        self.thread = threading.Thread(target=self._run, name="ReminderWorker", daemon=True)
        self.last_medication_sent = {}

    def start(self):
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            now = datetime.now()
            hhmm = now.strftime("%H:%M")

            if config.HEALTH_REMINDER_ENABLED:
                for t in STATE["medication_times"]:
                    if hhmm == t and self.last_medication_sent.get(t) != now.date().isoformat():
                        msg = f"吃藥提醒：現在是 {t}，請記得服藥。"
                        send_line_alert(msg)
                        self.last_medication_sent[t] = now.date().isoformat()
                        STATE["last_medication"] = msg

                if time.time() - STATE["last_water_reminder"] >= config.WATER_REMINDER_MINUTES * 60:
                    STATE["last_water_reminder"] = time.time()
                    send_line_alert("喝水提醒：請記得補充水分。")

            time.sleep(1)


class WebServer:
    def __init__(self, stop_event: threading.Event):
        self.stop_event = stop_event
        self.app = Flask(__name__)
        self.app.register_blueprint(create_blueprint(care_service))

        if CORS is not None:
            CORS(self.app, resources={r"/*": {"origins": config.WEB_UI_CORS_ORIGINS or ["*"]}})

        self._routes()
        self.thread = threading.Thread(target=self.run, name="WebServer", daemon=True)

    def start(self):
        self.thread.start()

    def _routes(self):
        @self.app.route("/")
        def index():
            return render_template_string(self._html())

        @self.app.route("/video_feed")
        def video_feed():
            return Response(self._stream(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @self.app.route("/status")
        def status():
            with GLOBAL_FRAME_LOCK:
                stream_ready = GLOBAL_JPEG is not None

            return jsonify(
                {
                    "status": "ok",
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "stream_ready": stream_ready,
                    "uptime_sec": int(time.time() - STATE["started_at"]),
                    **STATE,
                }
            )

        @self.app.route("/set_drive_mode/<mode>", methods=["POST"])
        def set_drive_mode(mode: str):
            if mode not in {"Auto", "Manual"}:
                return jsonify({"ok": False, "message": "mode 只能是 Auto 或 Manual"}), 400
            write_drive_mode(mode)
            return jsonify({"ok": True, "message": f"drive_mode={mode}"})

        @self.app.route("/set_system_mode/<mode>", methods=["POST"])
        def set_system_mode(mode: str):
            mode = mode.upper()
            if mode not in {"CARE", "BALL", "FOLLOW", "MANUAL"}:
                return jsonify({"ok": False, "message": "system_mode 只能是 CARE/BALL/FOLLOW/MANUAL"}), 400
            STATE["system_mode"] = mode
            if mode == "MANUAL":
                write_drive_mode("Manual")
            return jsonify({"ok": True, "message": f"system_mode={mode}"})

        @self.app.route("/command", methods=["POST"])
        def command():
            data = request.get_json(silent=True) or {}
            cmd = str(data.get("command", "")).strip()
            if not cmd:
                return jsonify({"ok": False, "message": "缺少 command"}), 400
            ok = issue_robot_command(cmd)
            return jsonify({"ok": ok, "command": cmd})

        @self.app.route("/ai", methods=["POST"])
        def ai_chat():
            data = request.get_json(silent=True) or {}
            text = str(data.get("text", "")).strip() or "你好"
            return jsonify({"reply": ask_gemini(text)})

        @self.app.route("/sos", methods=["POST"])
        def sos():
            data = request.get_json(silent=True) or {}
            note = str(data.get("note", "需要協助")).strip()
            msg = f"SOS 緊急通知：{note}"
            sent = send_line_alert(msg)
            return jsonify({"ok": True, "sent": sent, "message": msg})

        @self.app.route("/health/medication_times", methods=["POST"])
        def update_medication_times():
            data = request.get_json(silent=True) or {}
            times = data.get("times") or []
            clean = []
            for item in times:
                item = str(item).strip()
                if len(item) == 5 and item[2] == ":":
                    clean.append(item)
            if not clean:
                return jsonify({"ok": False, "message": "至少提供一個 HH:MM"}), 400
            STATE["medication_times"] = clean
            return jsonify({"ok": True, "times": clean})

    def run(self):
        logger.info("WebServer 啟動：http://%s:%s", config.WEB_UI_HOST, config.WEB_UI_PORT)
        self.app.run(
            host=config.WEB_UI_HOST,
            port=config.WEB_UI_PORT,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    def _stream(self):
        while not self.stop_event.is_set():
            with GLOBAL_FRAME_LOCK:
                frame = GLOBAL_JPEG
            if frame is not None:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(1 / 15)

    def _html(self) -> str:
        return """
<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>居家照護控制台</title>
<style>
body{font-family:Arial,'Microsoft JhengHei',sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}
.wrap{max-width:1280px;margin:0 auto}.grid{display:grid;grid-template-columns:1.5fr 1fr;gap:18px}.card{background:#111827;border:1px solid #334155;border-radius:18px;padding:16px}
img{width:100%;border-radius:14px;background:#000}.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}.btn{padding:10px 14px;border:0;border-radius:12px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}
.btn.gray{background:#475569}.btn.red{background:#dc2626} .item{padding:6px 0;border-bottom:1px solid #1f2937} input{width:100%;padding:10px;border-radius:10px;border:1px solid #334155;background:#0b1220;color:#fff}
pre{white-space:pre-wrap;background:#0b1220;padding:10px;border-radius:12px;min-height:80px}
</style></head><body><div class='wrap'><h1>居家照護智慧控制台</h1><p><a href="/care" style="color:#67e8f9">人體骨架、異常活動與警報取消介面</a></p><div class='grid'><div class='card'><img src='/video_feed'><div class='row'>
<button class='btn' onclick="post('/set_drive_mode/Auto')">自動</button><button class='btn gray' onclick="post('/set_drive_mode/Manual')">手動</button>
<button class='btn' onclick="post('/set_system_mode/CARE')">CARE</button><button class='btn' onclick="post('/set_system_mode/FOLLOW')">FOLLOW</button>
<button class='btn' onclick="post('/set_system_mode/BALL')">BALL</button>
<button class='btn red' onclick="sos()">SOS</button></div></div>
<div class='card'><div id='status'></div><h3>AI 對話</h3><input id='q' placeholder='輸入要問 Gemini 的內容'><div class='row'><button class='btn' onclick='askAi()'>送出</button></div><pre id='ans'></pre></div></div></div>
<script>
async function post(url, body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):null});return r.json()}
async function load(){const r=await fetch('/status');const d=await r.json();document.getElementById('status').innerHTML=`
<div class='item'>drive_mode: ${d.drive_mode}</div><div class='item'>system_mode: ${d.system_mode}</div>
<div class='item'>ball: ${d.ball_detected} / depth=${d.ball_depth ?? '--'}</div>
<div class='item'>person: ${d.person_detected} / depth=${d.person_depth ?? '--'}</div>
<div class='item'>obstacle_depth: ${d.obstacle_depth ?? '--'}</div><div class='item'>ultrasonic_cm: ${d.ultrasonic_cm ?? '--'}</div>
<div class='item'>last_command: ${d.last_command} @ ${d.last_command_time}</div>
<div class='item'>audio: ${d.audio?.last_voice_text || '待命'} / ${d.audio?.last_sound_event || '正常'}</div>
<div class='item'>emergency: ${d.emergency?.last_event || '待命'}</div>
<div class='item'>fall_alert: ${d.fall_alert_sent} / immobile_alert: ${d.immobile_alert_sent}</div>`}
async function askAi(){const text=document.getElementById('q').value.trim();const d=await post('/ai',{text});document.getElementById('ans').textContent=d.reply||''}
async function sos(){const d=await post('/sos',{note:'前端手動觸發'});alert(d.message||'已送出')}
setInterval(load,1500);load();
</script></body></html>
"""


def main():
    logger.info("A1 main start")

    init_external_services()
    logger.info("A2 external services done")

    if STATE["drive_mode"] not in {"Auto", "Manual"}:
        write_drive_mode("Manual")
    logger.info("A3 drive mode checked")

    frame_queue: queue.Queue = queue.Queue(maxsize=1)
    logger.info("A4 frame queue created")

    logger.info("A5 before FrameReader")
    reader = FrameReader(config.RTSP_INPUT_URL, frame_queue, STOP_EVENT)
    logger.info("A6 FrameReader ok")

    logger.info("A7 before VisionWorker")
    worker = VisionWorker(frame_queue, STOP_EVENT)
    logger.info("A8 VisionWorker ok")

    logger.info("A9 before ReminderWorker")
    reminder = ReminderWorker(STOP_EVENT)
    logger.info("A10 ReminderWorker ok")

    logger.info("A11 before WebServer")
    web = WebServer(STOP_EVENT)
    logger.info("A12 WebServer ok")

    audio = None
    if AudioGuardian is not None:
        try:
            logger.info("A13 before AudioGuardian")
            audio = AudioGuardian(STOP_EVENT, STATE, issue_robot_command, send_line_alert,
                                  care_service.respond_to_fall)
            logger.info("A14 AudioGuardian ok")
        except Exception as exc:
            logger.error("AudioGuardian 初始化失敗：%s", exc)

    emergency = None
    if EmergencyMonitor is not None:
        try:
            logger.info("A15 before EmergencyMonitor")
            emergency = EmergencyMonitor(STOP_EVENT, STATE, send_line_alert)
            logger.info("A16 EmergencyMonitor ok")
        except Exception as exc:
            logger.error("EmergencyMonitor 初始化失敗：%s", exc)

    logger.info("A17 before reader.start")
    care_root=Path(__file__).resolve().parents[1]
    care_service.enable_persistence(care_root/'mec_output'/'care_events.sqlite3',
                                    care_root/'mec_output'/'care_snapshots')
    care_service.start()
    reader.start()
    logger.info("A18 reader started")

    worker.start()
    logger.info("A19 worker started")

    reminder.start()
    logger.info("A20 reminder started")

    web.start()
    logger.info("A21 web started")

    if audio is not None:
        audio.start()
        logger.info("A22 audio started")

    if emergency is not None:
        emergency.start()
        logger.info("A23 emergency started")

    logger.info("A24 system ready")
    care_speaker = CareSpeaker() if config.CARE_LOCAL_TTS_ENABLED else None
    if care_speaker:
        if care_speaker.start():
            threading.Thread(target=watch_service,
                             args=(care_service,care_speaker,STOP_EVENT),
                             name='CareVoiceWatch',daemon=True).start()
            logger.info("本機跌倒語音詢問已啟用")
        else:
            logger.warning("本機跌倒語音詢問啟用失敗")
    logger.info("系統啟動完成 Web=http://%s:%s", config.WEB_UI_HOST, config.WEB_UI_PORT)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("A25 keyboard interrupt")
    finally:
        STOP_EVENT.set()
        if care_speaker:
            care_speaker.close()
        care_service.close()
        logger.info("A26 stop event set")


if __name__ == "__main__":
    main()
