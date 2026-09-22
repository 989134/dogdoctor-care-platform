import json
import tempfile
import unittest
from pathlib import Path

from navigation.ranger_patrol_adapter import GatewayError, RangerPatrolAdapter


class FakeTransport:
    def __init__(self):
        self.pulses = 0
        self.commands = []
        self.done_reason = "encoder_target"

    def request(self, method, path, payload=None):
        if path == "/health":
            return {"ok": True, "serial_open": True,
                    "board": "MBOT_RANGER_AURIGA_TELEMETRY_V1", "telemetry_fresh": True}
        if path == "/telemetry":
            self.pulses += 10
            return {"ok": True, "age_sec": .05, "t_ms": self.pulses * 10,
                    "left_pulse": self.pulses, "right_pulse": self.pulses,
                    "front_cm": 100.0}
        if path == "/control":
            self.commands.append(payload["command"])
            return {"ok": True, "done": payload["command"] + ",reason=" + self.done_reason}
        raise AssertionError(path)


class RangerPatrolAdapterTests(unittest.TestCase):
    def calibration_file(self, root):
        path = root / "calibration.json"
        path.write_text(json.dumps({
            "left_m_per_pulse": .001, "right_m_per_pulse": .001,
            "left_sign": 1, "right_sign": 1, "track_width_m": .18,
            "yaw_sign": -1, "left_turn_yaw_scale": .3,
            "right_turn_yaw_scale": .1,
        }), encoding="utf-8")
        return path

    def test_measured_snapshot_and_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeTransport()
            adapter = RangerPatrolAdapter.from_calibration_file(
                self.calibration_file(Path(temp)), transport=transport
            )
            self.assertEqual(adapter.odometry.yaw_sign, -1)
            self.assertAlmostEqual(adapter.odometry.left_turn_scale, .3)
            self.assertAlmostEqual(adapter.odometry.right_turn_scale, .1)
            self.assertTrue(adapter.preflight(now=1.0)["ok"])
            first = adapter.snapshot(now=1.1)
            second = adapter.snapshot(now=1.2)
            self.assertGreater(second.pose.x, first.pose.x)
            self.assertAlmostEqual(second.front_m, 1.0)

    def test_motion_is_locked_but_stop_is_always_available(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeTransport()
            adapter = RangerPatrolAdapter.from_calibration_file(
                self.calibration_file(Path(temp)), transport=transport
            )
            with self.assertRaises(GatewayError):
                adapter.apply("forward", now=1.0)
            adapter.apply("stop", now=1.0)
            self.assertEqual(transport.commands, ["stop"])

    def test_enabled_motion_requires_and_passes_preflight(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeTransport()
            adapter = RangerPatrolAdapter.from_calibration_file(
                self.calibration_file(Path(temp)), transport=transport, motion_enabled=True
            )
            response = adapter.apply("forward", now=1.0)
            self.assertTrue(response["ok"])
            self.assertGreater(response["pulse_delta"]["left"], 0)
            self.assertEqual(response["pulse_delta"]["left"], response["pulse_delta"]["right"])
            self.assertTrue(response["settled"])
            self.assertIn("telemetry_before", response)
            self.assertIn("telemetry_after", response)
            self.assertEqual(transport.commands, ["forward"])

    def test_motion_timeout_is_not_treated_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeTransport()
            transport.done_reason = "motion_timeout"
            adapter = RangerPatrolAdapter.from_calibration_file(
                self.calibration_file(Path(temp)), transport=transport, motion_enabled=True
            )
            with self.assertRaisesRegex(GatewayError, "motion_timeout"):
                adapter.apply("forward", now=1.0)
            self.assertEqual(transport.commands, ["forward"])

    def test_missing_track_width_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text(json.dumps({"left_m_per_pulse": .001}), encoding="utf-8")
            with self.assertRaises(ValueError):
                RangerPatrolAdapter.from_calibration_file(path)


if __name__ == "__main__":
    unittest.main()
