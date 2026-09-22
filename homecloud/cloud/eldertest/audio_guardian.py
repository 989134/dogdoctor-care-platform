import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

import config

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except Exception:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except Exception:
    Model = None
    KaldiRecognizer = None
    VOSK_AVAILABLE = False


@dataclass
class AudioEvent:
    kind: str
    text: str
    score: float = 0.0
    timestamp: float = 0.0


class AudioGuardian:
    """同時處理語音控制與異常聲音偵測，避免兩個服務搶同一支 USB 麥克風。"""

    def __init__(
        self,
        stop_event: threading.Event,
        status: dict,
        command_callback: Callable[[str], None],
        alert_callback: Callable[[str], None],
    ):
        self.stop_event = stop_event
        self.status = status
        self.command_callback = command_callback
        self.alert_callback = alert_callback
        self.sample_rate = int(config.AUDIO_SAMPLE_RATE)
        self.blocksize = int(config.AUDIO_BLOCK_SIZE)
        self.device = config.AUDIO_INPUT_DEVICE
        self.sound_threshold = float(config.SOUND_RMS_THRESHOLD)
        self.scream_threshold = float(config.SCREAM_RMS_THRESHOLD)
        self.cooldown_sec = float(config.SOUND_ALERT_COOLDOWN_SEC)
        self.last_alert_time = 0.0
        self.last_command_time = 0.0
        self.command_cooldown_sec = float(config.VOICE_COMMAND_COOLDOWN_SEC)
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self.stream = None
        self.thread = threading.Thread(target=self._run, name="AudioGuardian", daemon=True)
        self.vosk_model = None
        self.recognizer = None
        self.voice_enabled = bool(config.VOICE_CONTROL_ENABLED)
        self.sound_enabled = bool(config.SOUND_DETECTION_ENABLED)
        self._init_status()
        self._init_vosk()

    def _init_status(self):
        self.status.setdefault("audio", {})
        self.status["audio"].update(
            {
                "active": False,
                "mic_available": SOUNDDEVICE_AVAILABLE,
                "vosk_available": False,
                "voice_enabled": self.voice_enabled,
                "sound_enabled": self.sound_enabled,
                "last_voice_text": "",
                "last_command": "",
                "last_sound_event": "",
                "last_sound_score": 0.0,
                "last_update": "",
            }
        )

    def _init_vosk(self):
        if not self.voice_enabled:
            return
        if not VOSK_AVAILABLE:
            logger.warning("Vosk 未安裝，語音控制停用")
            return
        model_path = config.VOSK_MODEL_PATH
        try:
            if model_path and Model is not None:
                self.vosk_model = Model(model_path)
                self.recognizer = KaldiRecognizer(self.vosk_model, self.sample_rate)
                self.status["audio"]["vosk_available"] = True
                logger.info("Vosk 模型載入成功：%s", model_path)
        except Exception as exc:
            logger.error("Vosk 模型載入失敗：%s", exc)

    def start(self):
        if not SOUNDDEVICE_AVAILABLE:
            logger.warning("sounddevice 未安裝，音訊功能停用")
            return
        self.thread.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            logger.debug("麥克風狀態：%s", status)
        try:
            payload = bytes(indata)
            if self.audio_queue.full():
                self.audio_queue.get_nowait()
            self.audio_queue.put_nowait(payload)
        except Exception:
            pass

    def _map_voice_command(self, text: str) -> Optional[str]:
        t = text.replace(" ", "")
        mapping = {
            "前進": "forward",
            "往前": "forward",
            "後退": "backward",
            "往後": "backward",
            "左轉": "left",
            "向左": "left",
            "右轉": "right",
            "向右": "right",
            "停止": "stop",
            "停下": "stop",
            "停": "stop",
            "撿球": "pick",
            "開始自動": "MODE_AUTO",
            "自動模式": "MODE_AUTO",
            "切手動": "MODE_MANUAL",
            "手動模式": "MODE_MANUAL",
            "來這裡": "forward",
        }
        for phrase, command in mapping.items():
            if phrase in t:
                return command
        return None

    def _handle_voice_text(self, text: str):
        self.status["audio"]["last_voice_text"] = text
        self.status["audio"]["last_update"] = time.strftime("%H:%M:%S")

        if any(k in text for k in ["救命", "幫我", "help", "緊急"]):
            self._send_audio_alert("偵測到呼救語音，請立即確認長者狀況。", force=True)
            return

        command = self._map_voice_command(text)
        if not command:
            return
        now = time.time()
        if now - self.last_command_time < self.command_cooldown_sec:
            return
        self.last_command_time = now
        self.status["audio"]["last_command"] = command
        self.command_callback(command)
        logger.info("語音指令：%s -> %s", text, command)

    def _spectral_features(self, audio_int16: np.ndarray):
        audio = audio_int16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(audio))) + 1e-9)
        zcr = float(np.mean(np.abs(np.diff(np.sign(audio)))) / 2.0)
        mag = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(len(audio), d=1.0 / self.sample_rate)
        centroid = float((freqs * mag).sum() / (mag.sum() + 1e-9))
        peak = float(freqs[np.argmax(mag)]) if mag.size else 0.0
        return rms, zcr, centroid, peak

    def _classify_sound(self, rms: float, zcr: float, centroid: float, peak: float) -> Optional[AudioEvent]:
        now = time.time()
        if now - self.last_alert_time < self.cooldown_sec:
            return None

        if rms > self.scream_threshold and 700 < centroid < 2500 and zcr > 0.08:
            return AudioEvent(kind="scream", text="疑似呼救或尖叫聲", score=rms, timestamp=now)

        if rms > self.sound_threshold and centroid > 2500 and peak > 2800:
            return AudioEvent(kind="glass", text="疑似玻璃破裂或高頻異常聲", score=rms, timestamp=now)

        if rms > self.sound_threshold * 1.5:
            return AudioEvent(kind="impact", text="疑似突發撞擊或異常巨響", score=rms, timestamp=now)

        return None

    def _send_audio_alert(self, message: str, force: bool = False):
        now = time.time()
        if not force and now - self.last_alert_time < self.cooldown_sec:
            return
        self.last_alert_time = now
        self.alert_callback(message)

    def _run(self):
        self.status["audio"]["active"] = True
        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                device=self.device if self.device != "" else None,
                channels=1,
                dtype="int16",
                callback=self._audio_callback,
            ):
                logger.info("AudioGuardian 啟動成功")
                while not self.stop_event.is_set():
                    try:
                        payload = self.audio_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue

                    audio_np = np.frombuffer(payload, dtype=np.int16)
                    if audio_np.size == 0:
                        continue

                    rms, zcr, centroid, peak = self._spectral_features(audio_np)
                    if self.sound_enabled:
                        event = self._classify_sound(rms, zcr, centroid, peak)
                        if event:
                            self.status["audio"]["last_sound_event"] = event.text
                            self.status["audio"]["last_sound_score"] = round(event.score, 4)
                            self.status["audio"]["last_update"] = time.strftime("%H:%M:%S")
                            self._send_audio_alert(f"{event.text}，請立即查看家中狀況。")
                            logger.warning("異常聲音：%s rms=%.4f centroid=%.1f", event.kind, rms, centroid)

                    if self.voice_enabled and self.recognizer is not None:
                        accepted = self.recognizer.AcceptWaveform(payload)
                        if accepted:
                            result = json.loads(self.recognizer.Result())
                            text = (result.get("text") or "").strip()
                            if text:
                                self._handle_voice_text(text)
                        else:
                            partial = json.loads(self.recognizer.PartialResult()).get("partial", "").strip()
                            if partial:
                                self.status["audio"]["last_voice_text"] = partial
        except Exception as exc:
            logger.error("AudioGuardian 啟動失敗：%s", exc)
            self.status["audio"]["active"] = False
            self.status["audio"]["mic_available"] = False
            return
        finally:
            self.status["audio"]["active"] = False
            logger.info("AudioGuardian 已停止")