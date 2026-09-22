"""Raspberry Pi microphone/speaker gateway for the home-care robot."""
from __future__ import annotations

import argparse
import io
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass

from flask import Flask, jsonify, request


@dataclass
class SpeechJob:
    text: str
    priority: str = "normal"


class SpeakerService:
    def __init__(self, voice="zh-TW-HsiaoYuNeural", rate=155, engine_factory=None,
                 output_device="plughw:2,0", edge_rate="+5%", edge_pitch="+15Hz"):
        self.voice = voice
        self.rate = int(rate)
        self.output_device = output_device
        self.edge_rate = edge_rate
        self.edge_pitch = edge_pitch
        self.engine_factory = engine_factory
        self.queue = queue.Queue(maxsize=16)
        self.stop_event = threading.Event()
        self.current_process = None
        self.current_lock = threading.Lock()
        self.last_text = ""
        self.last_error = ""
        self.spoken_count = 0
        self.backend = self._detect_backend()
        self.thread = threading.Thread(target=self._run, name="RobotSpeaker", daemon=True)
        self.thread.start()

    def _detect_backend(self):
        if self.engine_factory is not None:
            return "injected"
        if shutil.which("edge-tts") and shutil.which("mpg123"):
            return "edge-tts"
        for command in ("espeak-ng", "espeak"):
            if shutil.which(command):
                return command
        try:
            import pyttsx3  # noqa: F401
            return "pyttsx3"
        except Exception:
            return "unavailable"

    @property
    def available(self):
        return self.backend != "unavailable"

    def enqueue(self, text, priority="normal"):
        text = " ".join(str(text).split())
        if not text:
            raise ValueError("語音內容不可為空")
        if len(text) > 300:
            raise ValueError("語音內容不可超過 300 個字")
        if priority == "emergency":
            self.stop(clear_queue=True)
        try:
            self.queue.put_nowait(SpeechJob(text, priority))
        except queue.Full as exc:
            raise RuntimeError("車上語音佇列已滿") from exc
        return self.queue.qsize()

    def stop(self, clear_queue=False):
        with self.current_lock:
            if self.current_process is not None and self.current_process.poll() is None:
                self.current_process.terminate()
        if clear_queue:
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break

    def close(self):
        self.stop_event.set()
        self.stop(clear_queue=True)
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass

    def status(self):
        return {
            "available": self.available,
            "backend": self.backend,
            "queue_depth": self.queue.qsize(),
            "speaking": bool(self.current_process and self.current_process.poll() is None),
            "spoken_count": self.spoken_count,
            "last_error": self.last_error,
            "voice": self.voice,
            "output_device": self.output_device,
        }

    def _speak(self, text):
        if self.backend == "edge-tts":
            with tempfile.TemporaryDirectory(prefix="robot-voice-") as temp_dir:
                audio_path = os.path.join(temp_dir, "speech.mp3")
                subprocess.run([
                    "edge-tts", "--voice", self.voice, f"--rate={self.edge_rate}",
                    f"--pitch={self.edge_pitch}", "--text", text,
                    "--write-media", audio_path,
                ], check=True, timeout=45, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE)
                command = ["mpg123", "-q"]
                if self.output_device:
                    command.extend(["-a", self.output_device])
                command.append(audio_path)
                with self.current_lock:
                    self.current_process = subprocess.Popen(command)
                self.current_process.wait(timeout=60)
                with self.current_lock:
                    self.current_process = None
            return
        if self.backend in ("espeak-ng", "espeak"):
            # eSpeak does not understand Edge neural voice names.
            command = [self.backend, "-v", "zh", "-s", str(self.rate), text]
            with self.current_lock:
                self.current_process = subprocess.Popen(command)
            self.current_process.wait(timeout=60)
            with self.current_lock:
                self.current_process = None
            return
        if self.backend == "injected":
            engine = self.engine_factory()
        else:
            import pyttsx3
            engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)
        try:
            engine.setProperty("voice", self.voice)
        except Exception:
            pass
        engine.say(text)
        engine.runAndWait()

    def _run(self):
        while not self.stop_event.is_set():
            job = self.queue.get()
            if job is None:
                break
            try:
                self.last_text = job.text
                self._speak(job.text)
                self.spoken_count += 1
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)


def _resolve_input_device(sd, requested=None):
    """Resolve an input device from an index or a case-insensitive name fragment."""
    requested = str(requested or os.getenv("ROBOT_AUDIO_INPUT_DEVICE", "")).strip()
    devices = sd.query_devices()
    inputs = [
        {"index": index, "name": item["name"], "channels": item["max_input_channels"],
         "default_sample_rate": int(item.get("default_samplerate") or 16000)}
        for index, item in enumerate(devices) if item["max_input_channels"] > 0
    ]
    if not requested:
        return int(sd.default.device[0]), inputs
    try:
        selected = int(requested)
    except ValueError:
        matches = [item for item in inputs if requested.casefold() in item["name"].casefold()]
        if not matches:
            raise ValueError(f"找不到麥克風裝置：{requested}")
        selected = matches[0]["index"]
    if not any(item["index"] == selected for item in inputs):
        raise ValueError(f"裝置 {selected} 不是可用的麥克風")
    return selected, inputs


def microphone_status():
    try:
        import sounddevice as sd
        selected, inputs = _resolve_input_device(sd)
        selected_name = next(
            (item["name"] for item in inputs if item["index"] == selected), ""
        )
        return {
            "available": bool(inputs),
            "default_input": int(sd.default.device[0]),
            "selected_input": selected,
            "selected_name": selected_name,
            "inputs": inputs,
        }
    except Exception as exc:
        return {
            "available": False,
            "default_input": None,
            "selected_input": None,
            "selected_name": "",
            "inputs": [],
            "error": str(exc),
        }


def capture_microphone_wav(duration, requested_device=None, sample_rate=None):
    import numpy as np
    import sounddevice as sd
    selected, inputs = _resolve_input_device(sd, requested_device)
    selected_name = next(
        (item["name"] for item in inputs if item["index"] == selected), ""
    )
    device_info = next(item for item in inputs if item["index"] == selected)
    requested_rate = sample_rate or os.getenv("ROBOT_AUDIO_SAMPLE_RATE", "")
    sample_rate = int(float(requested_rate or device_info["default_sample_rate"]))
    audio = sd.rec(
        int(duration * sample_rate), samplerate=sample_rate, channels=1,
        dtype="int16", device=selected,
    )
    sd.wait()
    raw = audio.tobytes()
    rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32) / 32768.0))))
    peak = float(np.max(np.abs(audio.astype(np.float32) / 32768.0)))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(raw)
    return {
        "wav": output.getvalue(), "rms": round(rms, 5), "peak": round(peak, 5),
        "heard": rms >= 0.003, "device": selected, "device_name": selected_name,
        "sample_rate": sample_rate,
    }


def create_app(speaker=None, token="", capture_func=None):
    app = Flask(__name__)
    speaker = speaker or SpeakerService(
        os.getenv("ROBOT_TTS_VOICE", "zh-TW-HsiaoYuNeural"),
        os.getenv("ROBOT_TTS_RATE", "155"),
        output_device=os.getenv("ROBOT_AUDIO_OUTPUT_DEVICE", "plughw:2,0"),
        edge_rate=os.getenv("ROBOT_TTS_EDGE_RATE", "+5%"),
        edge_pitch=os.getenv("ROBOT_TTS_EDGE_PITCH", "+15Hz"),
    )
    capture_func = capture_func or capture_microphone_wav
    microphone_lock = threading.Lock()
    token = token or os.getenv("ROBOT_AUDIO_TOKEN", "")

    def authorized():
        return not token or request.headers.get("X-Audio-Token", "") == token

    @app.before_request
    def require_token():
        if request.path != "/health" and not authorized():
            return jsonify(ok=False, error="音訊服務認證失敗"), 401

    @app.get("/health")
    def health():
        return jsonify(ok=True, service="robot-audio", speaker=speaker.status(), microphone=microphone_status())

    @app.post("/speak")
    def speak():
        data = request.get_json(silent=True) or {}
        try:
            depth = speaker.enqueue(data.get("text", ""), data.get("priority", "normal"))
            return jsonify(ok=True, queued=True, queue_depth=depth)
        except (ValueError, RuntimeError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post("/stop")
    def stop():
        speaker.stop(clear_queue=True)
        return jsonify(ok=True, message="已停止車上語音")

    @app.post("/microphone/test")
    def microphone_test():
        data = request.get_json(silent=True) or {}
        try:
            duration = max(0.5, min(5.0, float(data.get("duration", 2.0))))
            if not microphone_lock.acquire(blocking=False):
                return jsonify(ok=False, error="麥克風正在錄音中"), 409
            try:
                result = capture_func(duration)
            finally:
                microphone_lock.release()
            return jsonify(
                ok=True, duration=duration, rms=result["rms"], peak=result["peak"],
                heard=result["heard"], device=result["device"],
                device_name=result["device_name"], sample_rate=result.get("sample_rate"),
            )
        except Exception as exc:
            return jsonify(ok=False, error=f"麥克風測試失敗：{exc}"), 503

    @app.post("/microphone/capture")
    def microphone_capture():
        data = request.get_json(silent=True) or {}
        try:
            duration = max(2.0, min(10.0, float(data.get("duration", 6.0))))
            if not microphone_lock.acquire(blocking=False):
                return jsonify(ok=False, error="麥克風正在錄音中"), 409
            try:
                result = capture_func(duration)
            finally:
                microphone_lock.release()
            response = app.response_class(result["wav"], mimetype="audio/wav")
            response.headers["X-Audio-Rms"] = str(result["rms"])
            response.headers["X-Audio-Peak"] = str(result["peak"])
            response.headers["X-Audio-Heard"] = "1" if result["heard"] else "0"
            response.headers["X-Audio-Device"] = str(result["device"])
            response.headers["X-Audio-Device-Name"] = result["device_name"]
            response.headers["X-Audio-Sample-Rate"] = str(result.get("sample_rate", ""))
            return response
        except Exception as exc:
            return jsonify(ok=False, error=f"麥克風錄音失敗：{exc}"), 503

    app.speaker_service = speaker
    return app


def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi 車上麥克風與喇叭服務")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5002)
    args = parser.parse_args()
    create_app().run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
