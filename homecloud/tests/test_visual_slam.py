import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from navigation.visual_slam import Calibration, MonocularTracker, _spread, analyze_video


class VisualSlamBTests(unittest.TestCase):
    def test_feature_spread_rejects_concentrated_geometry(self):
        concentrated = np.array([[10, 10], [12, 12], [14, 14]], dtype=np.float32)
        distributed = np.array([[5, 5], [315, 5], [5, 235], [315, 235]], dtype=np.float32)
        self.assertLess(_spread(concentrated, 320, 240), 0.12)
        self.assertGreater(_spread(distributed, 320, 240), 0.9)

    def test_fisheye_calibration_scales_to_video(self):
        calibration = Calibration(
            np.array([[300., 0., 320.], [0., 300., 240.], [0., 0., 1.]]),
            np.zeros((4, 1)), "test",
        )
        new_K, map1, map2 = calibration.rectifier((320, 240))
        self.assertEqual(map1.shape[:2], (240, 320))
        self.assertEqual(map2.shape[:2], (240, 320))
        self.assertEqual(new_K.shape, (3, 3))

    def test_stationary_camera_does_not_create_motion(self):
        rng = np.random.default_rng(7)
        image = rng.integers(0, 256, (240, 320), dtype=np.uint8)
        K = np.array([[260., 0., 160.], [0., 260., 120.], [0., 0., 1.]])
        tracker = MonocularTracker(K, (320, 240))
        tracker.process(image, 0, 0.0)
        row = tracker.process(image, 1, 0.1)
        self.assertEqual(row["state"], "stationary")
        self.assertEqual(tracker.accepted_steps, 0)

    def test_end_to_end_marks_static_video_as_non_metric(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video = root / "static.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
            rng = np.random.default_rng(9)
            frame = rng.integers(0, 256, (120, 160, 3), dtype=np.uint8)
            for _ in range(12):
                writer.write(frame)
            writer.release()
            report = analyze_video(video, root / "out", calibration_path=None, frame_step=1)
            self.assertFalse(report["full_slam_completed"])
            self.assertFalse(report["scale"]["metric"])
            self.assertEqual(report["quality"]["tracked_frames"], 0)
            self.assertTrue((root / "out" / "report.html").exists())

    def test_invalid_feature_exclusion_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze_video(Path("unused.mp4"), Path("unused"), exclude_regions=((.8, .1, .2, .9),))


if __name__ == "__main__":
    unittest.main()
