"""HTTP client used by the Windows care UI to control the robot speaker."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class RobotAudioError(RuntimeError):
    pass


class RobotAudioClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Audio-Token"] = self.token
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise RobotAudioError(detail) from exc
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RobotAudioError(f"車上音訊服務連線失敗：{exc}") from exc

    def _request_bytes(self, method: str, path: str, payload=None, timeout=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Audio-Token"] = self.token
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                return response.read(), dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise RobotAudioError(detail) from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise RobotAudioError(f"車上音訊服務連線失敗：{exc}") from exc

    def health(self):
        try:
            result = self._request("GET", "/health")
            result["connected"] = True
            return result
        except RobotAudioError as exc:
            return {"ok": False, "connected": False, "error": str(exc)}

    def speak(self, text: str, priority: str = "normal"):
        return self._request("POST", "/speak", {"text": text, "priority": priority})

    def stop(self):
        return self._request("POST", "/stop", {})

    def microphone_test(self, duration: float = 2.0):
        return self._request("POST", "/microphone/test", {"duration": duration})

    def capture_audio(self, duration: float = 6.0):
        wav, headers = self._request_bytes(
            "POST", "/microphone/capture", {"duration": duration},
            timeout=max(self.timeout, duration + 8),
        )
        return {
            "wav": wav,
            "rms": float(headers.get("X-Audio-Rms", 0)),
            "peak": float(headers.get("X-Audio-Peak", 0)),
            "heard": headers.get("X-Audio-Heard") == "1",
            "device": headers.get("X-Audio-Device", ""),
            "device_name": headers.get("X-Audio-Device-Name", ""),
            "sample_rate": headers.get("X-Audio-Sample-Rate", ""),
        }
