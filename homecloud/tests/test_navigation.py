import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from navigation.core import Grid,Odometry,Patrol,Pose,LocalScanMatcher,depth_advisory
from navigation.telemetry import parse_line,PulseCalibration
from navigation.calibrate_encoder import straight_calibration,track_width_calibration


class NavigationTests(unittest.TestCase):
    def test_measured_odometry(self):
        o=Odometry(.2);o.update(0,0,0);p=o.update(1,.1,.1)
        self.assertAlmostEqual(p.x,.1);self.assertAlmostEqual(p.y,0)
        p=o.update(2,0,.2);self.assertAlmostEqual(p.yaw,1)
        with self.assertRaises(ValueError):o.update(2,0,.2)
        with self.assertRaises(ValueError):o.update(3,30,30)

    def test_tracked_vehicle_turn_scales_and_physical_direction(self):
        o=Odometry(.2,yaw_sign=-1,left_turn_scale=.3,right_turn_scale=.1)
        o.update(0,0,0)
        # Left wheel forward and right wheel backward is a physical left turn
        # on the measured Ranger motor/encoder layout.
        p=o.update(1,.1,-.1)
        self.assertAlmostEqual(p.yaw,.3)
        # The opposite movement uses the separately measured right-turn scale.
        p=o.update(2,0,0)
        self.assertAlmostEqual(p.yaw,.2)

    def test_unknown_not_traversable(self):
        g=Grid();self.assertFalse(g.plan((0,0),(1,0)))

    def test_rays_and_wall(self):
        g=Grid();g.integrate(Pose(),[[0,1,True]])
        x,y=g.cell(1,0);self.assertGreater(g.cells[y][x],0)
        x,y=g.cell(.5,0);self.assertLess(g.cells[y][x],0)
        self.assertFalse(g.plan((0,0),(1.2,0)))

    def test_patrol_stops_and_observes(self):
        p=Patrol(Grid());p.start([[0,0,2]])
        self.assertEqual(p.step(Pose(),0,0,0,1,.1),'stop')
        self.assertIsNone(p.observe_until)
        p.step(Pose(),1,1,0,1,0);self.assertEqual(p.state,'observing')
        p.step(Pose(),3,3,0,1,0);self.assertEqual(p.state,'complete')

    def test_failure_latched_until_resume(self):
        p=Patrol(Grid());p.start([[1,0,2]])
        self.assertEqual(p.step(Pose(),2,0,0,1,0),'stop')
        self.assertEqual(p.state,'localization_lost')
        p.step(Pose(),3,3,0,1,0);self.assertEqual(p.state,'localization_lost')
        p.resume();p.step(Pose(),4,4,0,.1,0);self.assertEqual(p.state,'blocked')
        p.pause();self.assertEqual(p.step(Pose(),5,5,0,1,0),'stop')

    def test_no_scan_match_without_evidence(self):
        pose=Pose();p,ok=LocalScanMatcher().match(Grid(),pose,[[0,1,True]])
        self.assertFalse(ok);self.assertIs(p,pose)

    def test_depth_is_not_metric(self):
        r=depth_advisory([[1,2,3],[1,2,3]])
        self.assertFalse(r['usable_for_localization'])
        with self.assertRaises(ValueError):depth_advisory([[float('nan'),2,3]])

    def test_ranger_telemetry_parser(self):
        line='TEL:t_ms=100,left_pulse=12,right_pulse=-13,left_deg=4,right_deg=-5,left_rpm=1.2,right_rpm=-1.3,front_cm=-1.0,moving=1'
        kind,data=parse_line(line);self.assertEqual(kind,'telemetry')
        self.assertFalse(data['ultrasonic_valid'])
        self.assertEqual(parse_line('DONE:forward,reason=duration')[0],'done')
        with self.assertRaises(ValueError):parse_line('TEL:t_ms=1')

    def test_ultrasonic_max_cap_is_not_clear_path(self):
        line = ('TEL:t_ms=100,left_pulse=0,right_pulse=0,left_deg=0,right_deg=0,'
                'left_rpm=0,right_rpm=0,front_cm=400,moving=0')
        kind, data = parse_line(line)
        self.assertEqual(kind, 'telemetry')
        self.assertFalse(data['ultrasonic_valid'])

    def test_pulse_calibration_required(self):
        c=PulseCalibration(.001,.002,right_sign=-1)
        self.assertEqual(c.meters({'left_pulse':10,'right_pulse':10}),(.01,-.02))
        with self.assertRaises(ValueError):PulseCalibration(0,.1)

    def test_encoder_and_track_width_calibration(self):
        c = straight_calibration(1.0, 1000, -1050)
        self.assertEqual(c['left_sign'], 1)
        self.assertEqual(c['right_sign'], -1)
        # For a 360 degree turn, +/-628 pulses at 1 mm/pulse is about 0.2 m.
        equal = {'left_m_per_pulse': .001, 'right_m_per_pulse': .001,
                 'left_sign': 1, 'right_sign': 1}
        width = track_width_calibration(360, -628, 628, equal)
        self.assertAlmostEqual(width, .2, places=2)
        with self.assertRaises(ValueError):
            straight_calibration(1, 0, 100)


if __name__=='__main__':unittest.main()
