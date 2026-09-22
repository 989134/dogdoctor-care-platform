import sys
import threading
import tempfile
import unittest
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eldertest"))

from care_web import create_blueprint
from companion_assistant import CompanionAssistant


class FakeService:
    def __init__(self):
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.jpeg = None

    def snapshot(self):
        return {"ok": True}


class FakeAudioClient:
    def __init__(self):
        self.spoken = []
        self.stopped = False

    def health(self):
        return {"ok": True, "connected": True, "speaker": {"available": True},
                "microphone": {"available": True}}

    def speak(self, text, priority="normal"):
        self.spoken.append((text, priority))
        return {"ok": True, "queued": True}

    def stop(self):
        self.stopped = True
        return {"ok": True}

    def microphone_test(self, duration=2.0):
        return {"ok": True, "rms": 0.01, "peak": 0.1, "heard": True}

    def capture_audio(self, duration=6.0):
        return {"wav": b"RIFF" + b"0" * 200, "rms": 0.02, "peak": 0.2,
                "heard": True, "device": "3", "device_name": "AB13X USB Audio"}


class CareAudioRouteTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.tempdir.name) / '.env'
        self.audio = FakeAudioClient()
        self.assistant = CompanionAssistant("test-key-value-long-enough",
                                            responder=lambda prompt: "您好，記得慢慢喝水。",
                                            audio_responder=lambda wav, history: {
                                                "transcript": "今天天氣如何",
                                                "reply": "今天天氣很舒服，出門記得帶水喔。",
                                            })
        app = Flask(__name__)
        app.register_blueprint(create_blueprint(FakeService(), assistant=self.assistant,
                                                audio_client=self.audio,
                                                env_path=self.env_path))
        self.client = app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_status_does_not_expose_api_key(self):
        response = self.client.get("/care/audio/status")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["assistant"]["configured"])
        self.assertNotIn("key", str(body).lower())
        self.assertNotIn("test-key", response.get_data(as_text=True))

    def test_custom_speech_and_emergency_announcement(self):
        response = self.client.post("/care/audio/speak", json={"text": "晚餐準備好了"})
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/care/audio/announce", json={"kind": "emergency"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.audio.spoken[0], ("晚餐準備好了", "normal"))
        self.assertEqual(self.audio.spoken[1][1], "emergency")

    def test_companion_reply_is_forwarded_to_robot(self):
        response = self.client.post("/care/assistant",
                                    json={"text": "提醒我喝水", "speak_on_robot": True})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["reply"], "您好，記得慢慢喝水。")
        self.assertTrue(body["speaker"]["sent"])
        self.assertEqual(self.audio.spoken[-1][0], body["reply"])

    def test_rejects_empty_or_too_long_speech(self):
        self.assertEqual(self.client.post("/care/audio/speak", json={"text": ""}).status_code, 400)
        self.assertEqual(self.client.post("/care/audio/speak",
                                          json={"text": "長" * 301}).status_code, 400)

    def test_robot_microphone_voice_turn_is_transcribed_answered_and_spoken(self):
        response = self.client.post("/care/assistant/voice-turn", json={"duration": 6})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["transcript"], "今天天氣如何")
        self.assertIn("天氣", body["reply"])
        self.assertEqual(self.audio.spoken[-1][0], body["reply"])

    def test_reset_companion_history(self):
        self.assertEqual(self.client.post("/care/assistant/reset", json={}).status_code, 200)

    def test_gemini_key_can_only_be_updated_locally_and_is_never_returned(self):
        key='new-gemini-key-value-long-enough'
        denied=self.client.post('/care/assistant/settings',json={'api_key':key})
        self.assertEqual(denied.status_code,403)
        response=self.client.post('/care/assistant/settings',json={'api_key':key},
                                  headers={'X-Care-Settings':'1'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn(key,response.get_data(as_text=True))
        self.assertEqual(self.assistant.api_key,key)
        self.assertIn('GEMINI_API_KEY='+key,
                      self.env_path.read_text(encoding='utf-8'))


if __name__ == "__main__":
    unittest.main()
