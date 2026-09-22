"""Any-person forward mode with cautious Ranger gateway safety checks."""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit


class FollowError(RuntimeError):
    pass


class ArrivalReached(FollowError):
    """Raised when the ultrasonic sensor says the robot reached the person."""

    def __init__(self, front_cm: float):
        self.front_cm = front_cm
        super().__init__(f"已到主人身邊（前方 {front_cm:.1f} cm）")


def _gateway_root(control_url: str) -> str:
    value = control_url.strip().rstrip("/")
    if value.endswith("/control"):
        value = value[:-8]
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("機器人控制網址格式不正確")
    return value


class JsonGateway:
    def __init__(self, control_url: str, timeout: float = 4.0):
        self.root = _gateway_root(control_url)
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.root + path, data=body, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FollowError(f"Ranger 連線失敗：{exc}") from exc


class FollowRuntime:
    """Moves forward while any upright person is visible, one safe step at a time."""

    unsafe_postures = {
        "suspected_fall", "lying_unverified", "resting", "upper_horizontal",
        "upper_resting", "stream_unavailable",
    }

    def __init__(self, model_path: Path, control_url: str, detector=None, gateway=None,
                 infer_interval: float = 0.20, on_arrival=None):
        self.model_path = Path(model_path)
        self.gateway = gateway or JsonGateway(control_url)
        self.detector = detector
        self.infer_interval = infer_interval
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.pending_lock = None
        self.track_id = None
        self.target_hist = None
        self.target_bbox = None
        self.desired_height_ratio = None
        self.last_seen = None
        self.last_infer = 0.0
        self.frame_size = (640, 480)
        self.detections = []
        self.enabled = False
        self.model_ready = False
        self.target_visible = False
        self.decision = "stop"
        self.reason = "人物前進模式尚未啟用"
        self.last_command = "stop"
        self.last_error = ""
        self.front_cm = None
        self.moving_until = 0.0
        self.on_arrival = on_arrival
        self.at_owner = False
        self.arrival_announced = False
        self.arrival_stop_cm = 35.0
        self.arrival_release_cm = 55.0
        self._next_arrival_poll = 0.0
        self.command_history = deque(maxlen=40)
        self._next_detection_id = 1
        self._reid_candidate = None
        self._reid_count = 0
        self.thread = threading.Thread(target=self._motion_loop, name="CareFollowMotion", daemon=True)
        self.thread.start()

    def close(self):
        self.disable("跟隨服務已停止")
        self.stop_event.set()
        self.thread.join(timeout=5)

    def is_moving(self):
        with self.lock:
            return time.monotonic() < self.moving_until

    def request_lock(self, x: float, y: float):
        """Keep the old API harmless for clients that have not refreshed yet."""
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("鎖定位置必須在畫面內")
        with self.lock:
            self.pending_lock = None
            self.reason = "目前不需鎖定人物，請直接按「開始跟隨」"
            self.last_error = ""

    def enable(self):
        with self.lock:
            self.enabled = True
            self.last_error = ""
            self.reason = ("已偵測到人，持續向前" if self.target_visible
                           else "已啟用，等待畫面出現人物")

    def disable(self, reason="已停止自主跟隨"):
        with self.lock:
            self.enabled = False
            self.decision = "stop"
            self.reason = reason
        try:
            self.gateway.request("POST", "/control", {"command": "stop"})
        except Exception:
            pass

    def clear_target(self):
        self.disable("已停止人物前進模式")
        with self.lock:
            self.pending_lock = None
            self.track_id = None
            self.target_hist = None
            self.target_bbox = None
            self.desired_height_ratio = None
            self.target_visible = False

    def process(self, frame, now: float, posture: str = ""):
        if now - self.last_infer < self.infer_interval:
            return
        self.last_infer = now
        try:
            detections = self._detect(frame)
            self._update_target(frame, detections, now, posture)
        except Exception as exc:
            with self.lock:
                self.model_ready = False
                self.target_visible = False
                self.decision = "stop"
                self.last_error = str(exc)
                self.reason = "人體跟蹤模型無法執行"

    def _load_detector(self):
        if self.detector is not None:
            return
        if not self.model_path.is_file():
            raise FollowError(f"找不到人體跟蹤模型：{self.model_path}")
        from ultralytics import YOLO
        self.detector = YOLO(str(self.model_path))

    def _detect(self, frame):
        self._load_detector()
        # ``YOLO.track`` imports the optional ``lap`` package.  The care demo
        # only needs one-person continuity, so keep the deployment small and
        # assign stable IDs by matching consecutive detection boxes instead.
        results = self.detector.predict(
            frame, verbose=False, classes=[1], conf=0.45, iou=0.55, imgsz=640,
        )
        found = []
        if not results or results[0].boxes is None:
            return found
        boxes = results[0].boxes
        xyxy = boxes.xyxy.cpu().tolist()
        confidences = boxes.conf.cpu().tolist()
        raw = [{"bbox": tuple(float(v) for v in box),
                "confidence": float(confidence)}
               for box, confidence in zip(xyxy, confidences)]
        return self._assign_detection_ids(raw)

    @staticmethod
    def _bbox_iou(a, b):
        left, top = max(a[0], b[0]), max(a[1], b[1])
        right, bottom = min(a[2], b[2]), min(a[3], b[3])
        intersection = max(0.0, right-left) * max(0.0, bottom-top)
        if intersection <= 0:
            return 0.0
        area_a = max(0.0, a[2]-a[0]) * max(0.0, a[3]-a[1])
        area_b = max(0.0, b[2]-b[0]) * max(0.0, b[3]-b[1])
        union = area_a + area_b - intersection
        return intersection/union if union > 0 else 0.0

    def _assign_detection_ids(self, detections):
        """Keep a person's ID stable between nearby frames without ByteTrack."""
        with self.lock:
            previous = list(self.detections)
        unused = set(range(len(previous)))
        assigned = []
        for item in sorted(detections, key=lambda value: value["confidence"], reverse=True):
            match = None
            score = 0.0
            for index in unused:
                candidate = self._bbox_iou(item["bbox"], previous[index]["bbox"])
                if candidate > score:
                    match, score = index, candidate
            if match is not None and score >= .20:
                ident = previous[match]["id"]
                unused.remove(match)
            else:
                ident = self._next_detection_id
                self._next_detection_id += 1
            assigned.append(dict(item, id=int(ident)))
        return assigned

    @staticmethod
    def _histogram(frame, bbox):
        import cv2
        x1, y1, x2, y2 = bbox
        h, w = frame.shape[:2]
        x1, x2 = max(0, int(x1)), min(w, int(x2))
        y1, y2 = max(0, int(y1)), min(h, int(y2))
        if x2 - x1 < 12 or y2 - y1 < 20:
            return None
        roi = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([roi], [0, 1], None, [18, 16], [0, 180, 0, 256])
        return cv2.normalize(hist, hist).flatten()

    def _select_clicked(self, detections, width, height, point):
        px, py = point[0] * width, point[1] * height
        inside = [d for d in detections if d["bbox"][0] <= px <= d["bbox"][2]
                  and d["bbox"][1] <= py <= d["bbox"][3]]
        if inside:
            return max(inside, key=lambda d: d["confidence"])
        if not detections:
            return None
        nearest = min(detections, key=lambda d: math.hypot(
            ((d["bbox"][0] + d["bbox"][2]) / 2 - px) / width,
            ((d["bbox"][1] + d["bbox"][3]) / 2 - py) / height,
        ))
        cx = (nearest["bbox"][0] + nearest["bbox"][2]) / 2
        cy = (nearest["bbox"][1] + nearest["bbox"][3]) / 2
        return nearest if math.hypot((cx-px)/width, (cy-py)/height) <= .22 else None

    def _reidentify(self, frame, detections):
        if self.target_hist is None:
            return None
        import cv2
        scored = []
        for item in detections:
            hist = self._histogram(frame, item["bbox"])
            if hist is None:
                continue
            score = float(cv2.compareHist(self.target_hist, hist, cv2.HISTCMP_CORREL))
            scored.append((score, item, hist))
        if not scored:
            return None
        score, item, hist = max(scored, key=lambda value: value[0])
        if score < .72:
            self._reid_candidate = None
            self._reid_count = 0
            return None
        if self._reid_candidate == item["id"]:
            self._reid_count += 1
        else:
            self._reid_candidate = item["id"]
            self._reid_count = 1
        if self._reid_count < 2:
            return None
        self.target_hist = hist
        self._reid_candidate = None
        self._reid_count = 0
        return item

    def _update_target(self, frame, detections, now, posture):
        height, width = frame.shape[:2]
        # No identity lock is needed in this mode.  The largest visible person
        # is used only for the on-screen box; the presence of any person is the
        # trigger for forward motion.
        selected = max(
            detections,
            key=lambda item: (
                max(0.0, item["bbox"][2] - item["bbox"][0])
                * max(0.0, item["bbox"][3] - item["bbox"][1]),
                item.get("confidence", 0.0),
            ),
            default=None,
        )
        with self.lock:
            self.frame_size = (width, height)
            self.detections = detections
            self.model_ready = True
            self.pending_lock = None
            if selected is None:
                self.target_visible = False
                self.track_id = None
                self.target_bbox = None
                self.at_owner = False
                self.arrival_announced = False
                self.decision = "stop"
                self.reason = ("畫面未偵測到人，已停車" if self.enabled
                               else "人物前進模式尚未啟用")
                return
            self.target_visible = True
            self.track_id = selected["id"]
            self.target_bbox = selected["bbox"]
            self.last_seen = now
            self.last_error = ""
            if self.at_owner:
                self.decision = "stop"
                self.reason = "已到主人身邊，停車陪伴"
            elif posture in self.unsafe_postures:
                self.decision = "stop"
                self.reason = "姿態不適合跟隨，已停車觀察"
            else:
                self.decision = "forward"
                self.reason = ("已偵測到人，持續向前" if self.enabled
                               else "已偵測到人，按「開始跟隨」即可前進")

    def _safe_decision(self):
        with self.lock:
            if not self.enabled:
                return "stop", "跟隨尚未啟用"
            if not self.target_visible or self.last_seen is None or time.monotonic()-self.last_seen > .8:
                return "stop", "失去照護對象"
            return self.decision, self.reason

    def _preflight(self, command):
        health = self.gateway.request("GET", "/health")
        telemetry = self.gateway.request("GET", "/telemetry")
        if not health.get("ok") or not health.get("serial_open") or not health.get("telemetry_fresh"):
            raise FollowError("Ranger 控制板或遷測尚未就緒")
        if not str(health.get("board", "")).startswith("MBOT_RANGER_AURIGA"):
            raise FollowError("Ranger 控制板識別不符")
        age = float(telemetry.get("age_sec", 99))
        front = float(telemetry.get("front_cm", -1))
        with self.lock:
            self.front_cm = front if math.isfinite(front) else None
        if age > .8 or not 1 <= front < 399.5:
            raise FollowError("前向超音波距離無效，已禁止自主移動")
        if front < self.arrival_stop_cm:
            raise ArrivalReached(front)
        return telemetry

    def _mark_arrival(self, front_cm):
        callback = None
        with self.lock:
            first_arrival = not self.arrival_announced
            self.at_owner = True
            self.arrival_announced = True
            self.front_cm = front_cm
            self.decision = "stop"
            self.reason = f"已到主人身邊（{front_cm:.1f} cm），停車陪伴"
            self.last_error = ""
            if first_arrival:
                callback = self.on_arrival
        if callback is not None:
            try:
                callback()
            except Exception:
                # A speaker problem must never defeat motor safety.
                pass

    def _poll_arrival_release(self):
        """Resume following after the person has walked away from the robot."""
        now = time.monotonic()
        with self.lock:
            if not self.at_owner or now < self._next_arrival_poll:
                return
            self._next_arrival_poll = now + .5
        try:
            telemetry = self.gateway.request("GET", "/telemetry")
            front = float(telemetry.get("front_cm", -1))
        except Exception:
            return
        with self.lock:
            if math.isfinite(front) and front >= self.arrival_release_cm:
                self.at_owner = False
                self.arrival_announced = False
                if self.enabled and self.target_visible:
                    self.decision = "forward"
                    self.reason = "主人已往前移動，繼續跟隨"

    def _send(self, command):
        if command != "stop":
            self._preflight(command)
        with self.lock:
            if command != "stop":
                self.moving_until = time.monotonic() + 1.8
        response = self.gateway.request("POST", "/control", {"command": command})
        if not response.get("ok"):
            raise FollowError(f"Ranger 拒絕 {command} 指令")
        with self.lock:
            self.last_command = command
            self.command_history.append({"at": time.time(), "command": command})

    def _motion_loop(self):
        last_sent = None
        while not self.stop_event.wait(.04):
            self._poll_arrival_release()
            command, reason = self._safe_decision()
            try:
                if command == "stop":
                    if last_sent != "stop":
                        self._send("stop")
                        last_sent = "stop"
                    continue
                self._send(command)
                last_sent = command
                with self.lock:
                    self.last_error = ""
            except ArrivalReached as exc:
                try:
                    self._send("stop")
                except Exception:
                    pass
                self._mark_arrival(exc.front_cm)
                last_sent = "stop"
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                    self.reason = str(exc)
                    self.decision = "stop"
                try:
                    self._send("stop")
                except Exception:
                    pass
                last_sent = "stop"
                self.stop_event.wait(.6)

    def draw(self, frame):
        import cv2
        with self.lock:
            bbox = self.target_bbox if self.target_visible else None
            enabled = self.enabled
            reason = self.reason
            track_id = self.track_id
        if bbox is not None:
            x1, y1, x2, y2 = map(int, bbox)
            color = (0, 220, 80) if enabled else (0, 190, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
            cv2.putText(frame, f"PERSON DETECTED #{track_id}", (x1, max(22, y1-8)),
                        cv2.FONT_HERSHEY_SIMPLEX, .58, color, 2)
        cv2.putText(frame, ("FOLLOW ON" if enabled else "FOLLOW READY") + ": " + reason[:38],
                    (12, 114), cv2.FONT_HERSHEY_SIMPLEX, .48, (0, 220, 80), 2)

    def snapshot(self):
        with self.lock:
            width, height = self.frame_size
            bbox = self.target_bbox
            normalized = None if bbox is None else {
                "x1": round(bbox[0]/width, 4), "y1": round(bbox[1]/height, 4),
                "x2": round(bbox[2]/width, 4), "y2": round(bbox[3]/height, 4),
            }
            return {
                "available": self.model_ready, "enabled": self.enabled,
                "locked": False, "target_visible": self.target_visible,
                "track_id": self.track_id, "bbox": normalized,
                "decision": self.decision, "last_command": self.last_command,
                "reason": self.reason, "error": self.last_error,
                "front_cm": self.front_cm, "people_visible": len(self.detections),
                "desired_height_ratio": self.desired_height_ratio,
                "at_owner": self.at_owner,
                "mode": "any_person_forward",
            }
