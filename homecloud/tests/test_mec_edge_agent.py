import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mec_edge_agent import ConnectivityState,outage_active
from types import SimpleNamespace


class ConnectivityStateTests(unittest.TestCase):
    def test_circuit_breaker_and_recovery(self):
        state=ConnectivityState(failure_threshold=2,base_backoff=1,max_backoff=4)
        state.failure(now=10)
        self.assertEqual(state.snapshot()['state'],'degraded')
        state.failure(now=10)
        self.assertFalse(state.can_attempt(now=10.5))
        self.assertTrue(state.can_attempt(now=11.1))
        state.success()
        self.assertEqual(state.snapshot()['state'],'online')

    def test_force_probe_skips_backoff_after_network_returns(self):
        state=ConnectivityState(failure_threshold=1,base_backoff=30)
        state.failure(now=10)
        self.assertFalse(state.can_attempt(now=11))
        state.force_probe()
        self.assertTrue(state.can_attempt(now=11))

    def test_second_based_outage_takes_precedence(self):
        args=SimpleNamespace(outage_start_seconds=10,outage_seconds=60,
                             outage_start_frame=3,outage_frames=4)
        self.assertFalse(outage_active(args,3,9.9))
        self.assertTrue(outage_active(args,100,10))
        self.assertTrue(outage_active(args,100,69.9))
        self.assertFalse(outage_active(args,3,70))

    def test_frame_based_outage_remains_available(self):
        args=SimpleNamespace(outage_start_seconds=-1,outage_seconds=0,
                             outage_start_frame=3,outage_frames=4)
        self.assertTrue(outage_active(args,3,0))
        self.assertFalse(outage_active(args,7,0))


if __name__=='__main__':unittest.main()
