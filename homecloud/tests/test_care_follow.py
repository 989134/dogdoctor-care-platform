import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eldertest'))
from care_follow import ArrivalReached, FollowError, FollowRuntime


class FakeGateway:
    def __init__(self,front_cm=100):
        self.front_cm=front_cm
        self.commands=[]

    def request(self,method,path,payload=None):
        if path=='/health':
            return {'ok':True,'serial_open':True,'telemetry_fresh':True,
                    'board':'MBOT_RANGER_AURIGA_TEST'}
        if path=='/telemetry':
            return {'ok':True,'age_sec':.1,'front_cm':self.front_cm}
        if path=='/control':
            self.commands.append(payload['command'])
            return {'ok':True,'accepted':payload['command']}
        raise AssertionError(path)


class FollowRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.gateway=FakeGateway()
        self.runtime=FollowRuntime(Path('unused.pt'),'http://robot:5000/control',
                                   detector=object(),gateway=self.gateway)
        self.frame=np.zeros((480,640,3),dtype=np.uint8)
        self.frame[80:440,220:420]=(40,120,220)

    def tearDown(self):
        self.runtime.close()

    def test_any_visible_person_moves_forward_without_lock(self):
        self.runtime._update_target(self.frame,[
            {'id':7,'bbox':(220,80,420,440),'confidence':.9},
            {'id':8,'bbox':(10,40,80,180),'confidence':.95},
        ],time.monotonic(),'standing')
        state=self.runtime.snapshot()
        self.assertFalse(state['locked'])
        self.assertTrue(state['target_visible'])
        self.assertEqual(state['track_id'],7)
        self.runtime.enable()
        self.runtime._update_target(self.frame,[{'id':9,'bbox':(5,190,95,390),'confidence':.9}],time.monotonic(),'standing')
        self.assertEqual(self.runtime.snapshot()['decision'],'forward')

    def test_target_loss_fall_and_bad_ultrasonic_stop_motion(self):
        box={'id':3,'bbox':(220,80,420,440),'confidence':.9}
        self.runtime.enable()
        self.runtime._update_target(self.frame,[box],time.monotonic(),'standing')
        self.runtime._update_target(self.frame,[box],time.monotonic(),'suspected_fall')
        self.assertEqual(self.runtime.snapshot()['decision'],'stop')
        self.runtime._update_target(self.frame,[],time.monotonic(),'standing')
        self.assertFalse(self.runtime.snapshot()['target_visible'])
        self.gateway.front_cm=400
        with self.assertRaises(FollowError):
            self.runtime._preflight('forward')

    def test_lightweight_box_tracker_keeps_id_without_optional_lap(self):
        first=self.runtime._assign_detection_ids([
            {'bbox':(200,60,420,440),'confidence':.9},
        ])
        self.runtime.detections=first
        second=self.runtime._assign_detection_ids([
            {'bbox':(212,65,432,445),'confidence':.92},
        ])
        self.assertEqual(first[0]['id'],second[0]['id'])

    def test_arrival_stops_and_greets_once_until_person_leaves(self):
        greetings=[]
        self.runtime.on_arrival=lambda:greetings.append('hello')
        self.gateway.front_cm=30
        with self.assertRaises(ArrivalReached):
            self.runtime._preflight('forward')
        self.runtime._mark_arrival(30)
        self.runtime._mark_arrival(30)
        self.assertTrue(self.runtime.snapshot()['at_owner'])
        self.assertEqual(greetings,['hello'])
        self.runtime._update_target(self.frame,[],time.monotonic(),'standing')
        self.runtime._mark_arrival(30)
        self.assertEqual(greetings,['hello','hello'])


if __name__=='__main__':
    unittest.main()
