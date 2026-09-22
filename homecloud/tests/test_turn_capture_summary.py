import unittest

from navigation.capture_turn_360 import build_summary


def trial(direction, left, right, width=.2, valid=True):
    return {
        "direction": direction, "left_delta_pulse": left,
        "right_delta_pulse": right, "effective_track_width_m": width,
        "angle_error_deg": 0.0, "valid": valid,
    }


class TurnCaptureSummaryTests(unittest.TestCase):
    def test_zero_pulse_and_incomplete_direction_are_not_summarized(self):
        trials = [trial("left", -10, 10) for _ in range(3)]
        trials += [trial("right", 10, -10) for _ in range(2)]
        trials += [trial(None, 0, 0, width=None, valid=False)]
        summary = build_summary(trials, 3)
        self.assertFalse(summary["valid"])
        self.assertEqual(summary["actual_left_valid_trials"], 3)
        self.assertEqual(summary["actual_right_valid_trials"], 2)

    def test_complete_valid_trials_produce_effective_width(self):
        trials = [trial("left", -10, 10, .21) for _ in range(3)]
        trials += [trial("right", 10, -10, .23) for _ in range(3)]
        summary = build_summary(trials, 3)
        self.assertTrue(summary["valid"])
        self.assertAlmostEqual(summary["overall_mean_effective_track_width_m"], .22)


if __name__ == "__main__":
    unittest.main()
