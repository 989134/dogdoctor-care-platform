import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from navigation.localization_runtime import LocalizationEngine
from navigation.localization_web import LiveLocalizationRuntime, create_app


def calibration_file(root: Path) -> Path:
    path = root / "calibration.json"
    path.write_text(json.dumps({
        "left_m_per_pulse": 0.001,
        "right_m_per_pulse": 0.001,
        "left_sign": -1,
        "right_sign": 1,
        "track_width_m": 0.15,
    }), encoding="utf-8")
    return path


def packet(t_ms, left, right, front=100.0):
    return {
        "ok": True, "t_ms": t_ms, "left_pulse": left, "right_pulse": right,
        "front_cm": front, "ultrasonic_valid": True,
    }


class LocalizationRuntimeTests(unittest.TestCase):
    def test_duplicate_controller_timestamp_is_safe_and_forward_is_metric(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = LocalizationEngine(calibration_file(Path(temporary)))
            engine.update_telemetry(packet(1000, 0, 0), 0.0)
            engine.update_telemetry(packet(1000, 0, 0), 0.1)
            pose = engine.update_telemetry(packet(1250, -100, 100), 0.25)
            self.assertAlmostEqual(pose.x, 0.1, places=5)
            self.assertAlmostEqual(pose.y, 0.0, places=5)
            self.assertEqual(engine.telemetry_samples, 3)
            self.assertEqual(
                engine.status(0.25)["encoder"],
                {"left_pulse": -100, "right_pulse": 100},
            )

    def test_ultrasonic_hit_updates_occupancy_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = LocalizationEngine(calibration_file(Path(temporary)))
            engine.update_telemetry(packet(1000, 0, 0, front=20.0), 0.0)
            status = engine.status(0.0, include_map=True)
            self.assertGreaterEqual(len(status["map"]["occupied"]), 1)
            self.assertEqual(status["motion_commands_sent"], 0)

    def test_recent_near_obstacle_survives_intermittent_max_range(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = LocalizationEngine(calibration_file(Path(temporary)))
            engine.update_telemetry(packet(1000, 0, 0, front=16.0), 0.0)
            engine.update_telemetry(packet(1250, 0, 0, front=400.0), 0.25)
            status = engine.status(0.25)
            self.assertAlmostEqual(status["front_m"], .16)
            self.assertIsNone(status["front_raw_m"])
            engine.update_telemetry(packet(2501, 0, 0, front=400.0), 1.501)
            self.assertIsNone(engine.status(1.501)["front_m"])

    def test_stale_telemetry_pauses_localization(self):
        with tempfile.TemporaryDirectory() as temporary:
            engine = LocalizationEngine(calibration_file(Path(temporary)), (64, 48))
            engine.update_telemetry(packet(1000, 0, 0), 0.0)
            stale_at = engine.config.telemetry_stale_s + 0.1
            engine.process_frame(np.zeros((48, 64, 3), np.uint8), stale_at, 0)
            self.assertFalse(engine.status(stale_at)["localization_available"])

    def test_dashboard_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = LiveLocalizationRuntime(
                "0", "http://127.0.0.1:9", calibration_file(Path(temporary))
            )
            client = create_app(runtime).test_client()
            response = client.get("/api/localization/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["motion_commands_sent"], 0)
            reset = client.post("/api/localization/reset")
            self.assertEqual(reset.status_code, 200)
            self.assertEqual(reset.get_json()["motion_commands_sent"], 0)


if __name__ == "__main__":
    unittest.main()
