import unittest
import math

from flask import Flask

from navigation.core import Pose
from navigation.mission import PatrolMission, SensorSnapshot, Waypoint, arrange_waypoints
from navigation.patrol_simulator import DEFAULT_WAYPOINTS, PatrolSimulator, create_demo_grid
from navigation.patrol_web import PatrolRuntime, create_patrol_blueprint


class PatrolMissionTests(unittest.TestCase):
    def test_shortest_route_respects_priority_then_distance(self):
        points = [
            Waypoint("far", "遠", 1.5, 0, 1, 1),
            Waypoint("urgent", "優先", 1.8, 1.8, 1, 2),
            Waypoint("near", "近", .2, 0, 1, 1),
        ]
        route = arrange_waypoints(points, Pose(), "shortest")
        self.assertEqual([point.id for point in route], ["urgent", "far", "near"])

    def test_localization_loss_is_latched_and_stops(self):
        grid = create_demo_grid()
        mission = PatrolMission(grid)
        start = Pose(-1.35, -1.25, 0)
        mission.start(DEFAULT_WAYPOINTS[:1], start)
        sample = SensorSnapshot(1.0, start, -1.0, .1, 1.0, 0.0)
        self.assertEqual(mission.tick(sample), "stop")
        self.assertEqual(mission.state, "localization_lost")
        good = SensorSnapshot(1.1, start, 1.1, .95, 1.0, 0.0)
        self.assertEqual(mission.tick(good), "stop")
        mission.resume(start, localization_ok=True, now=1.2)
        self.assertEqual(mission.state, "navigating")

    def test_simulated_patrol_reaches_and_observes_all_points(self):
        simulator = PatrolSimulator.create()
        mission = PatrolMission(simulator.grid)
        mission.start(DEFAULT_WAYPOINTS, simulator.pose, route_mode="shortest")
        command = mission.tick(simulator.snapshot())
        for _ in range(5000):
            simulator.step(command)
            command = mission.tick(simulator.snapshot())
            if mission.state in {"completed", "blocked", "localization_lost"}:
                break
        self.assertEqual(mission.state, "completed", mission.reason)
        self.assertEqual(len(mission.completed_observations), len(DEFAULT_WAYPOINTS))
        self.assertEqual(command, "stop")

    def test_dynamic_obstacle_triggers_replan_or_safe_stop(self):
        simulator = PatrolSimulator.create()
        mission = PatrolMission(simulator.grid)
        mission.start(DEFAULT_WAYPOINTS[:1], simulator.pose)
        command = mission.tick(simulator.snapshot())
        for _ in range(10):
            simulator.step(command)
            command = mission.tick(simulator.snapshot())
        before = mission.replans
        simulator.inject_obstacle(distance=.55)
        simulator.step(command)
        command = mission.tick(simulator.snapshot())
        self.assertGreater(mission.replans, before)
        self.assertIn(command, {"left", "right", "stop"})

    def test_waypoint_can_require_observation_heading(self):
        grid = create_demo_grid()
        mission = PatrolMission(grid)
        point = [{"id": "turn", "name": "右轉", "x": -1.35, "y": -1.25,
                  "yaw_deg": -90, "observe_seconds": 1}]
        pose = Pose(-1.35, -1.25, 0)
        mission.start(point, pose)
        command = mission.tick(SensorSnapshot(1, pose, 1, .9, 1, 0))
        self.assertEqual(command, "right")
        self.assertEqual(mission.state, "aligning")
        turned = Pose(pose.x, pose.y, -math.pi / 2)
        command = mission.tick(SensorSnapshot(2, turned, 2, .9, 1, 0))
        self.assertEqual(command, "stop")
        self.assertEqual(mission.state, "observing")

    def test_later_unknown_waypoint_is_safe_until_new_map_exists(self):
        grid = create_demo_grid()
        mission = PatrolMission(grid)
        start = Pose(-1.35, -1.25, 0)
        # The later point is inside the map but deliberately outside the demo's
        # known free room. Starting is allowed; planning it later will stop.
        mission.start([
            {"id": "known", "name": "已知點", "x": -1.2, "y": -1.25,
             "observe_seconds": 1},
            {"id": "future", "name": "待掃描點", "x": 1.5, "y": 1.5,
             "observe_seconds": 1},
        ], start)
        self.assertEqual(len(mission.route), 2)

    def test_clear_diagonal_steering_aims_at_goal_not_grid_zigzag(self):
        grid = create_demo_grid()
        mission = PatrolMission(grid)
        pose = Pose(-1.35, -1.25, -math.pi / 2)
        mission.start([{
            "id": "goal", "name": "斜前方", "x": -1.29, "y": -1.55,
            "observe_seconds": 1,
        }], pose)
        command = mission.tick(SensorSnapshot(1, pose, 1, .9, 1, 0))
        self.assertEqual(command, "forward")

    def test_dashboard_api_never_sends_hardware_commands(self):
        runtime = PatrolRuntime(start_thread=False)
        app = Flask(__name__)
        app.register_blueprint(create_patrol_blueprint(runtime))
        client = app.test_client()
        response = client.post("/api/patrol/start", json={
            "waypoints": DEFAULT_WAYPOINTS[:1], "route_mode": "manual"
        })
        self.assertEqual(response.status_code, 200)
        status = client.get("/api/patrol/status").get_json()
        self.assertEqual(status["mode"], "SIMULATION")
        self.assertEqual(status["hardware_commands_sent"], 0)
        client.post("/api/patrol/lose-localization")
        runtime.advance(1)
        self.assertEqual(client.get("/api/patrol/status").get_json()["command"], "stop")
        runtime.close()


if __name__ == "__main__":
    unittest.main()
