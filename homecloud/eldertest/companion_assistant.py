"""Small Gemini companion wrapper; the API key never leaves the server."""
from __future__ import annotations

import base64
import threading
import json
import re
import urllib.error
import urllib.request


SYSTEM_PROMPT = """你是居家照護機器狗的長者陪伴助手。
請用繁體中文，語氣溫暖、句子簡短，通常在三句以內。
若涉及急性危險、意識不清、呼吸困難、嚴重出血或劇烈疼痛，請先建議立即求助家人或撥打 119。
不要聲稱已做出醫療診斷，也不要捏造你看見或量到的事情。"""


class CompanionAssistant:
    def __init__(self, api_key: str, model_name: str = "gemini-flash-lite-latest", responder=None,
                 audio_responder=None):
        self.api_key = api_key.strip()
        self.model_name = model_name
        self._responder = responder
        self._audio_responder = audio_responder
        self._lock = threading.Lock()
        self._history = []
        self.error = ""
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model_name):
            self.error = "Gemini 模型名稱格式不正確"
        self._opener = urllib.request.build_opener()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and not self.error)

    def status(self):
        return {
            "configured": self.configured,
            "model": self.model_name,
            "error": self.error if self.api_key and not self.configured else "",
        }

    def update_api_key(self, api_key: str):
        """Replace the key in memory without ever returning or logging it."""
        value = str(api_key).strip()
        if len(value) < 20 or len(value) > 200 or any(ch.isspace() for ch in value):
            raise ValueError("API Key 格式不合理")
        with self._lock:
            self.api_key = value
            self._history.clear()
            self.error = ("" if re.fullmatch(r"[A-Za-z0-9._-]+", self.model_name)
                          else "Gemini 模型名稱格式不正確")
        return self.status()

    def ask(self, text: str) -> str:
        text = text.strip()
        if not text:
            raise ValueError("請先輸入或說出內容")
        if len(text) > 500:
            raise ValueError("內容不可超過 500 個字")
        if not self.configured:
            raise RuntimeError("Gemini 尚未啟用，請先在 .env 設定 GEMINI_API_KEY")
        with self._lock:
            prompt = f"{SYSTEM_PROMPT}\n\n{self._history_text()}使用者：{text}"
            if self._responder is not None:
                reply = self._responder(prompt)
            else:
                reply = self._generate_rest(prompt)
        reply = (reply or "目前無法回答，請稍後再試。").strip()
        reply = reply[:800]
        with self._lock:
            self._remember(text, reply)
        return reply

    def ask_audio(self, wav_bytes: bytes):
        if not self.configured:
            raise RuntimeError("Gemini 尚未啟用，請先在 .env 設定 GEMINI_API_KEY")
        if not isinstance(wav_bytes, (bytes, bytearray)) or len(wav_bytes) < 100:
            raise ValueError("錄音內容為空")
        if len(wav_bytes) > 2_000_000:
            raise ValueError("單次錄音過長")
        with self._lock:
            if self._audio_responder is not None:
                result = self._audio_responder(bytes(wav_bytes), self._history_text())
            else:
                result = self._generate_audio_rest(bytes(wav_bytes), self._history_text())
        transcript = " ".join(str(result.get("transcript", "")).split())[:500]
        reply = " ".join(str(result.get("reply", "")).split())[:800]
        if not transcript:
            raise RuntimeError("沒有辨識到清楚的語音，請靠近麥克風再說一次")
        if not reply:
            reply = "我有聽到，但現在想不到怎麼回答。可以再說一次嗎？"
        with self._lock:
            self._remember(transcript, reply)
        return {"transcript": transcript, "reply": reply}

    def clear_history(self):
        with self._lock:
            self._history.clear()

    def _remember(self, user_text, reply):
        self._history.append((str(user_text), str(reply)))
        self._history = self._history[-8:]

    def _history_text(self):
        if not self._history:
            return ""
        lines = ["最近對話："]
        for user_text, reply in self._history[-6:]:
            lines.extend((f"長者：{user_text}", f"守護犬：{reply}"))
        return "\n".join(lines) + "\n\n"

    def _generate_rest(self, prompt: str) -> str:
        data = self._post_generate({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800},
        })
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        return "".join(str(part.get("text", "")) for part in parts)

    def _generate_audio_rest(self, wav_bytes: bytes, history: str):
        prompt = f"""{SYSTEM_PROMPT}

{history}請辨識這段台灣華語錄音，並直接回答長者。只輸出指定 JSON。
transcript 是逐字內容；reply 是三句內、適合直接用喇叭播放的回答。"""
        data = self._post_generate({
            "contents": [{"role": "user", "parts": [
                {"text": prompt},
                {"inlineData": {
                    "mimeType": "audio/wav",
                    "data": base64.b64encode(wav_bytes).decode("ascii"),
                }},
            ]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1000,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "transcript": {"type": "STRING"},
                        "reply": {"type": "STRING"},
                    },
                    "required": ["transcript", "reply"],
                },
            },
        })
        parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        raw = "".join(str(part.get("text", "")) for part in parts).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gemini 語音回覆格式錯誤") from exc

    def _post_generate(self, payload_object):
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model_name}:generateContent")
        payload = json.dumps(payload_object, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        })
        try:
            with self._opener.open(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                detail = ""
            raise RuntimeError(f"Gemini API 拒絕請求：{detail or exc.code}") from exc
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Gemini API 連線失敗：{exc}") from exc
        return data
