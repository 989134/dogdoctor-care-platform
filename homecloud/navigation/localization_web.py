"""Read-only live localization service and dashboard for the Ranger."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template_string, request

from navigation.localization_runtime import LocalizationEngine
from navigation.local_network import direct_urlopen


def get_json(url: str, timeout=1.5) -> dict:
    try:
        with direct_urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"無法讀取 {url}：{exc}") from exc


class LiveLocalizationRuntime:
    def __init__(self, source, gateway_url: str, calibration: Path, vision_step=5, telemetry_hz=5.0):
        self.source = int(source) if isinstance(source, str) and source.isdigit() else source
        self.gateway_url = gateway_url.rstrip("/")
        self.calibration = Path(calibration)
        self.vision_step = max(1, int(vision_step))
        self.telemetry_hz = max(1.0, float(telemetry_hz))
        self.engine = LocalizationEngine(self.calibration)
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.camera_connected = False
        self.telemetry_connected = False
        self.error = ""
        self.latest_jpeg = None
        self.started_at = time.monotonic()
        self.threads = []

    def start(self):
        if self.threads:
            return
        self.threads = [
            threading.Thread(target=self._telemetry_loop, daemon=True, name="LocalizationTelemetry"),
            threading.Thread(target=self._camera_loop, daemon=True, name="LocalizationCamera"),
        ]
        for thread in self.threads:
            thread.start()

    def _telemetry_loop(self):
        interval = 1.0 / self.telemetry_hz
        while not self.closed.is_set():
            started = time.monotonic()
            try:
                packet = get_json(self.gateway_url + "/telemetry")
                received = time.monotonic()
                packet_age = max(0.0, float(packet.get("age_sec", 0.0)))
                with self.lock:
                    self.engine.update_telemetry(
                        packet, received - self.started_at - packet_age,
                    )
                    self.telemetry_connected = True
                    self.error = ""
            except (RuntimeError, ValueError) as exc:
                with self.lock:
                    self.telemetry_connected = False
                    self.error = str(exc)
            self.closed.wait(max(0.0, interval - (time.monotonic() - started)))

    def _camera_loop(self):
        frame_index = 0
        while not self.closed.is_set():
            if isinstance(self.source, str) and self.source.lower().startswith(("http://", "https://")):
                try:
                    for frame in self._mjpeg_frames():
                        if self.closed.is_set():
                            break
                        with self.lock:
                            self.camera_connected = True
                            self.error = ""
                        frame_index = self._process_camera_frame(frame, frame_index)
                except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
                    with self.lock:
                        self.camera_connected = False
                        self.error = f"定位攝影機串流中斷，正在重試：{exc}"
                finally:
                    with self.lock:
                        self.camera_connected = False
                self.closed.wait(1.0)
                continue
            capture = cv2.VideoCapture(self.source)
            if not capture.isOpened():
                with self.lock:
                    self.camera_connected = False
                    self.error = "無法開啟定位攝影機，正在重試"
                self.closed.wait(2.0)
                continue
            with self.lock:
                self.camera_connected = True
            try:
                while not self.closed.is_set():
                    ok, frame = capture.read()
                    if not ok:
                        break
                    frame_index = self._process_camera_frame(frame, frame_index)
            finally:
                capture.release()
                with self.lock:
                    self.camera_connected = False
            self.closed.wait(1.0)

    def _mjpeg_frames(self):
        request = urllib.request.Request(str(self.source), method="GET")
        with direct_urlopen(request, timeout=5.0) as response:
            content_type = response.headers.get("Content-Type", "")
            if "multipart" not in content_type.lower() and "jpeg" not in content_type.lower():
                raise ValueError(f"攝影機回傳格式不符：{content_type or 'unknown'}")
            buffer = bytearray()
            while not self.closed.is_set():
                chunk = response.read(8192)
                if not chunk:
                    raise OSError("攝影機串流已關閉")
                buffer.extend(chunk)
                while True:
                    start = buffer.find(b"\xff\xd8")
                    end = buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start < 0 or end < 0:
                        if len(buffer) > 8_000_000:
                            del buffer[:-2]
                        break
                    jpeg = bytes(buffer[start:end + 2])
                    del buffer[:end + 2]
                    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        yield frame

    def _process_camera_frame(self, frame, frame_index):
        host_time = time.monotonic() - self.started_at
        with self.lock:
            if frame_index % self.vision_step == 0:
                self.engine.process_frame(frame, host_time, frame_index)
            display = self._annotate(frame)
            encoded, jpeg = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 82])
            if encoded:
                self.latest_jpeg = jpeg.tobytes()
                self.ready.set()
        return frame_index + 1

    def _annotate(self, frame):
        result = frame.copy()
        status = self.engine.status(time.monotonic() - self.started_at)
        pose = status["pose"]
        color = (60, 205, 75) if status["localization_available"] else (40, 55, 225)
        cv2.rectangle(result, (0, 0), (result.shape[1], 74), (18, 36, 42), -1)
        cv2.putText(result, f"pose x={pose['x']:.2f} y={pose['y']:.2f} yaw={pose['yaw']:.2f}",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, .55, (240, 245, 245), 1, cv2.LINE_AA)
        cv2.putText(result, f"{status['vision']['state']} confidence={status['localization_confidence']:.2f}",
                    (10, 53), cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2, cv2.LINE_AA)
        return result

    def status(self, include_map=True):
        with self.lock:
            status = self.engine.status(time.monotonic() - self.started_at, include_map=include_map)
            status.update({
                "camera_connected": self.camera_connected,
                "telemetry_connected": self.telemetry_connected,
                "error": self.error,
                "source": str(self.source),
                "gateway_url": self.gateway_url,
            })
            return status

    def jpeg(self):
        with self.lock:
            return self.latest_jpeg

    def reset_origin(self):
        with self.lock:
            self.engine = LocalizationEngine(self.calibration)
            self.started_at = time.monotonic()
            self.error = ""

    def close(self):
        self.closed.set()
        for thread in self.threads:
            thread.join(timeout=2.0)


PAGE = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ranger 即時定位與建圖</title><style>
:root{--ink:#17343d;--teal:#087f8c;--red:#bd3f4c;--bg:#edf4f5}*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:system-ui,"Microsoft JhengHei",sans-serif;color:var(--ink)}header{background:#173b47;color:white;padding:16px 24px}header h1{margin:0;font-size:24px}.banner{background:#e6f5ef;color:#175d45;padding:10px 20px;text-align:center;font-weight:700}.layout{display:grid;grid-template-columns:minmax(480px,1.2fr) minmax(420px,1fr);gap:16px;max-width:1400px;margin:18px auto;padding:0 16px}.card{background:white;border-radius:13px;padding:15px;box-shadow:0 3px 12px #173b4714}img{width:100%;background:#17262b;border-radius:9px}canvas{width:100%;aspect-ratio:1/1;background:#f7fafb;border-radius:9px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.metric{background:#f0f6f7;border-radius:8px;padding:10px}.metric b{display:block;font-size:20px;margin-top:3px}.bad{color:var(--red)}.good{color:#16885a}.note{font-size:13px;color:#587078}@media(max-width:920px){.layout{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}}
</style></head><body><header><h1>Ranger 即時定位與超音波建圖</h1></header><div class="banner">被動觀察模式：此服務不會傳送任何馬達指令</div><main class="layout"><section><div class="card"><img src="/localization/video_feed"></div><div class="card"><div class="metrics"><div class="metric">定位狀態<b id="available">—</b></div><div class="metric">可信度<b id="confidence">—</b></div><div class="metric">視覺狀態<b id="vision">—</b></div><div class="metric">X / Y<b id="xy">—</b></div><div class="metric">角度<b id="yaw">—</b></div><div class="metric">前方距離<b id="front">—</b></div></div><p id="error" class="bad"></p></div></section><aside><div class="card"><canvas id="map" width="700" height="700"></canvas><p class="note">淺藍：超音波確認可通行；深色：量測到障礙；橘色：里程計軌跡；紅色：目前位置。</p></div><div class="card"><p>影像：<b id="camera">—</b>　遙測：<b id="telemetry">—</b></p><p>閉環修正：<b id="loops">0</b> 次　定位不會控制車輛。</p><p>編碼器：左 <b id="leftPulse">—</b>　右 <b id="rightPulse">—</b></p><button onclick="resetOrigin()">重設定位原點</button></div></aside></main>
<script>
function draw(s){const c=document.querySelector('#map'),g=c.getContext('2d'),m=s.map,W=c.width,H=c.height,x0=m.origin[0],y0=m.origin[1],span=m.size*m.resolution,px=x=>(x-x0)/span*W,py=y=>H-(y-y0)/span*H;g.fillStyle='#f8fbfb';g.fillRect(0,0,W,H);g.fillStyle='#dceff1';(m.free||[]).forEach(p=>g.fillRect(px(p[0])-1,py(p[1])-1,2,2));g.fillStyle='#26383e';(m.occupied||[]).forEach(p=>g.fillRect(px(p[0])-3,py(p[1])-3,6,6));const tr=s.trace||[];g.strokeStyle='#df7e2d';g.lineWidth=3;g.beginPath();let last=null;tr.forEach((p,i)=>{if(!last||Math.hypot(p[0]-last[0],p[1]-last[1])>.5)g.moveTo(px(p[0]),py(p[1]));else g.lineTo(px(p[0]),py(p[1]));last=p});g.stroke();const p=s.pose;g.fillStyle='#c83f4e';g.beginPath();g.arc(px(p.x),py(p.y),8,0,Math.PI*2);g.fill()}
async function resetOrigin(){await fetch('/api/localization/reset',{method:'POST'});await refresh()}async function refresh(){try{const s=await fetch('/api/localization/status').then(r=>r.json());const ok=s.localization_available;document.querySelector('#available').textContent=ok?'正常':'暫停';document.querySelector('#available').className=ok?'good':'bad';document.querySelector('#confidence').textContent=s.localization_confidence.toFixed(2);document.querySelector('#vision').textContent=s.vision.state;document.querySelector('#xy').textContent=`${s.pose.x.toFixed(2)}, ${s.pose.y.toFixed(2)} m`;document.querySelector('#yaw').textContent=`${(s.pose.yaw*180/Math.PI).toFixed(1)}°`;document.querySelector('#front').textContent=s.front_m==null?'—':`${s.front_m.toFixed(2)} m`;document.querySelector('#camera').textContent=s.camera_connected?'正常':'中斷';document.querySelector('#telemetry').textContent=s.telemetry_connected?'正常':'中斷';document.querySelector('#loops').textContent=s.loop_closures.length;document.querySelector('#leftPulse').textContent=s.encoder.left_pulse??'—';document.querySelector('#rightPulse').textContent=s.encoder.right_pulse??'—';document.querySelector('#error').textContent=s.error||'';draw(s)}catch(e){document.querySelector('#error').textContent='定位服務連線失敗'}}setInterval(refresh,500);refresh();
</script></body></html>
"""


def create_app(runtime: LiveLocalizationRuntime) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    @app.get("/localization")
    def page():
        return render_template_string(PAGE)

    @app.get("/api/localization/status")
    def status():
        include_map = request.args.get("include_map", "1").lower() not in {"0", "false", "no"}
        return jsonify(runtime.status(include_map=include_map))

    @app.post("/api/localization/reset")
    def reset():
        runtime.reset_origin()
        return jsonify(ok=True, action="reset_origin", motion_commands_sent=0)

    @app.get("/localization/video_feed")
    def video_feed():
        def frames():
            while not runtime.closed.is_set():
                jpeg = runtime.jpeg()
                if jpeg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                runtime.closed.wait(0.05)
        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app
