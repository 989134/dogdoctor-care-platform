import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot_audio_gateway import _resolve_input_device, create_app


class FakeSoundDevice:
    class Default:
        device = (1, 4)

    default = Default()

    @staticmethod
    def query_devices():
        return [
            {"name": "output only", "max_input_channels": 0, "default_samplerate": 48000},
            {"name": "icspring camera", "max_input_channels": 1, "default_samplerate": 16000},
            {"name": "AB13X USB Audio", "max_input_channels": 1, "default_samplerate": 48000},
        ]


class FakeSpeaker:
    def __init__(self):
        self.jobs = []
        self.stopped = False

    def status(self):
        return {"available": True, "backend": "fake", "queue_depth": 0}

    def enqueue(self, text, priority="normal"):
        if not str(text).strip():
            raise ValueError("語音內容不可為空")
        self.jobs.append((text, priority))
        return len(self.jobs)

    def stop(self, clear_queue=False):
        self.stopped = True


class RobotAudioGatewayTests(unittest.TestCase):
    def setUp(self):
        self.speaker = FakeSpeaker()
        self.capture_result = {
            "wav": b"RIFF" + b"1" * 128, "rms": 0.02, "peak": 0.2,
            "heard": True, "device": 2, "device_name": "AB13X USB Audio",
        }
        self.client = create_app(
            self.speaker, token="secret-token",
            capture_func=lambda duration: self.capture_result,
        ).test_client()

    def test_health_does_not_require_token(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["service"], "robot-audio")

    def test_speak_requires_token(self):
        self.assertEqual(self.client.post("/speak", json={"text": "測試"}).status_code, 401)
        response = self.client.post("/speak", json={"text": "測試", "priority": "normal"},
                                    headers={"X-Audio-Token": "secret-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.speaker.jobs, [("測試", "normal")])

    def test_stop_clears_speech(self):
        response = self.client.post("/stop", json={},
                                    headers={"X-Audio-Token": "secret-token"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.speaker.stopped)

    def test_input_device_can_be_selected_by_name(self):
        selected, inputs = _resolve_input_device(FakeSoundDevice(), "ab13x")
        self.assertEqual(selected, 2)
        self.assertEqual(len(inputs), 2)

    def test_default_input_device_is_used_without_override(self):
        selected, _ = _resolve_input_device(FakeSoundDevice(), "")
        self.assertEqual(selected, 1)

    def test_microphone_capture_returns_wav_without_saving_file(self):
        response = self.client.post(
            "/microphone/capture", json={"duration": 6},
            headers={"X-Audio-Token": "secret-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertEqual(response.data, self.capture_result["wav"])
        self.assertEqual(response.headers["X-Audio-Device-Name"], "AB13X USB Audio")


if __name__ == "__main__":
    unittest.main()
