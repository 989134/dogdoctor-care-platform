import unittest
import json
import tempfile
from pathlib import Path

from navigation.real_patrol import RealPatrolRuntime, grid_from_localization
from navigation.real_patrol_web import create_app


def localization_status(available=True, front=1.0):
    free = []
    for iy in range(4, 16):
        for ix in range(4, 16):
            free.append([-1.0 + (ix + .5) * .1, -1.0 + (iy + .5) * .1])
    return {
        "localization_available": available,
        "camera_connected": True,
        "telemetry_connected": True,
        "localization_confidence": .9 if available else .1,
        "telemetry_age_s": .05,
        "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
        "front_m": front,
        "robot_moving": False,
        "map": {"size": 20, "resolution": .1, "origin": [-1.0, -1.0],
                "free": free, "occupied": []},
        "trace": [[0.0, 0.0]],
    }


class FakeAdapter:
    def __init__(self):
        self.commands = []

    def preflight(self, now=None):
        return {"ok": True, "errors": [], "health": {}, "telemetry": {}}

    def apply(self, command, now=None):
        self.commands.append(command)
        return {"ok": True, "done": command + ",reason=encoder_target"}


class RealPatrolTests(unittest.TestCase):
    waypoint = [{"id": "p1", "name": "短距離", "x": .3, "y": 0,
                 "observe_seconds": 1}]

    def test_localization_map_converts_free_cells(self):
        grid = grid_from_localization(localization_status())
        self.assertLess(grid.cells[grid.cell(0, 0)[1]][grid.cell(0, 0)[0]], 0)

    def test_read_only_runtime_plans_without_hardware_output(self):
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=False, adapter=adapter,
            status_provider=localization_status, start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        self.assertEqual(runtime.advance_once(), "forward")
        self.assertEqual(adapter.commands, [])
        self.assertEqual(runtime.status()["mode"], "REAL_READ_ONLY")

    def test_armed_runtime_sends_one_bounded_step(self):
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=True, adapter=adapter,
            status_provider=localization_status, start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        self.assertEqual(runtime.advance_once(), "forward")
        self.assertEqual(adapter.commands, ["forward"])
        self.assertEqual(runtime.status()["hardware_commands_sent"], 1)
        self.assertEqual(runtime.advance_once(), "stop")
        self.assertEqual(adapter.commands, ["forward"])

    def test_summary_status_does_not_erase_cached_map(self):
        states = [localization_status(), localization_status()]
        states[1]["map"] = {"size": 20, "resolution": .1, "origin": [-1, -1]}
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=False, adapter=adapter,
            status_provider=lambda: states.pop(0), start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        expected_free = len(runtime.latest_map["free"])
        runtime.advance_once()
        self.assertEqual(len(runtime.latest_map["free"]), expected_free)

    def test_localization_loss_latches_and_stops(self):
        state = {"available": True}
        provider = lambda: localization_status(state["available"])
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=True, adapter=adapter,
            status_provider=provider, localization_loss_grace_s=0,
            start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        state["available"] = False
        self.assertEqual(runtime.advance_once(), "stop")
        self.assertEqual(runtime.status()["state"], "localization_lost")
        self.assertEqual(adapter.commands[-1], "stop")

    def test_transient_localization_loss_stops_without_latching(self):
        state = {"available": True}
        provider = lambda: localization_status(state["available"])
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=True, adapter=adapter,
            status_provider=provider, localization_loss_grace_s=2,
            start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        state["available"] = False
        self.assertEqual(runtime.advance_once(), "stop")
        self.assertEqual(runtime.status()["state"], "navigating")
        state["available"] = True
        self.assertEqual(runtime.advance_once(), "forward")

    def test_single_stale_packet_stops_without_latching(self):
        stale = localization_status()
        stale["telemetry_age_s"] = .8
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=True, adapter=adapter,
            status_provider=lambda: stale, localization_loss_grace_s=2,
            start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        self.assertEqual(runtime.advance_once(), "stop")
        self.assertEqual(runtime.status()["state"], "navigating")
        self.assertEqual(adapter.commands, ["stop"])

    def test_web_emergency_stop_is_available_in_read_only_mode(self):
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=False, adapter=FakeAdapter(),
            status_provider=localization_status, start_thread=False,
        )
        client = create_app(runtime).test_client()
        self.assertEqual(client.post("/api/real-patrol/emergency-stop").status_code, 200)
        self.assertEqual(client.get("/api/real-patrol/status").get_json()["hardware_commands_sent"], 0)

    def test_completed_hardware_step_is_kept_in_evidence(self):
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=True, adapter=adapter,
            status_provider=localization_status, start_thread=False,
        )
        runtime.start_mission(self.waypoint)
        runtime.advance_once()
        report = runtime.evidence()
        self.assertEqual(report["schema"], "homecloud.real_patrol.evidence.v1")
        self.assertEqual(report["hardware_history"][0]["command"], "forward")
        self.assertEqual(report["hardware_history"][0]["outcome"], "completed")
        self.assertNotIn("map", report["status"])
        self.assertGreater(report["status"]["map_summary"]["free_cells"], 0)

    def test_web_can_save_patrol_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RealPatrolRuntime(
                "http://unused", "http://unused", Path("unused"),
                motion_enabled=False, adapter=FakeAdapter(),
                status_provider=localization_status, start_thread=False,
                evidence_dir=Path(directory),
            )
            runtime.start_mission(self.waypoint)
            response = create_app(runtime).test_client().post(
                "/api/real-patrol/save-evidence"
            )
            self.assertEqual(response.status_code, 200)
            path = Path(response.get_json()["path"])
            self.assertTrue(path.is_file())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema"],
                             "homecloud.real_patrol.evidence.v1")

    def test_held_mission_sends_no_motion_until_released(self):
        adapter = FakeAdapter()
        runtime = RealPatrolRuntime(
            "http://unused", "http://unused", Path("unused"),
            motion_enabled=True, adapter=adapter,
            status_provider=localization_status, start_thread=False,
        )
        runtime.start_mission(self.waypoint, hold_before_motion=True)
        self.assertEqual(runtime.advance_once(), "stop")
        self.assertEqual(adapter.commands, [])
        self.assertTrue(runtime.status()["mission_held"])
        runtime.release_hold()
        self.assertEqual(runtime.advance_once(), "forward")
        self.assertEqual(adapter.commands, ["forward"])


if __name__ == "__main__":
    unittest.main()
