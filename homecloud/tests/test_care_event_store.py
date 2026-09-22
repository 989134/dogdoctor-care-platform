import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from flask import Flask

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eldertest'))
from care_engine import Settings
from care_service import CareService
from care_web import create_blueprint


class CareEventPersistenceTests(unittest.TestCase):
    def test_unresolved_review_survives_restart_and_status_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=root/'events.sqlite3';snapshots=root/'shots'
            first=CareService(Settings(fall_response_seconds=20),event_store_path=database,
                              snapshot_dir=snapshots)
            now=time.monotonic()
            first.engine._request_tracking_review(now)
            first._sync_events(now,force=True)
            uid=first.engine.events[-1]['event_uid']
            first.close()

            second=CareService(Settings(fall_response_seconds=20),event_store_path=database,
                               snapshot_dir=snapshots)
            restored=second.engine.active['tracking_review']
            self.assertEqual(restored['event_uid'],uid)
            self.assertEqual(restored['status'],'review')
            second.respond_to_fall('help')
            self.assertEqual(second.event_history()[0]['status'],'alert')
            second.close()

    def test_recorded_demo_can_keep_history_without_reviving_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=root/'events.sqlite3'
            first=CareService(Settings(),event_store_path=database,
                              snapshot_dir=root/'shots')
            now=time.monotonic();first.engine._request_tracking_review(now)
            first._sync_events(now,force=True);first.close()
            replay=CareService(Settings(),event_store_path=database,
                               snapshot_dir=root/'shots',restore_events=False)
            self.assertFalse(replay.engine.active)
            self.assertEqual(len(replay.event_history()),1)
            replay.close()

    def test_snapshot_is_written_and_resolved_by_uid(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);database=root/'events.sqlite3';snapshots=root/'shots'
            service=CareService(Settings(),event_store_path=database,snapshot_dir=snapshots)
            now=time.monotonic();service.engine._request_tracking_review(now)
            service._capture_event_snapshots(np.zeros((80,120,3),dtype=np.uint8),now)
            event=service.engine.events[-1]
            self.assertTrue(event['snapshot_available'])
            self.assertTrue(service.event_snapshot(event['event_uid']).is_file())
            self.assertTrue(service.event_history()[0]['snapshot_available'])
            service.close()

    def test_snapshot_becomes_three_frame_evidence_strip(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'居家照護';root.mkdir()
            service=CareService(Settings(),event_store_path=root/'events.sqlite3',
                                snapshot_dir=root/'shots')
            service._capture_event_snapshots(np.full((40,60,3),20,dtype=np.uint8),0)
            service.engine._request_tracking_review(1,risk_pose=(.7,.85))
            service._capture_event_snapshots(np.full((40,60,3),100,dtype=np.uint8),1)
            service._capture_event_snapshots(np.full((40,60,3),220,dtype=np.uint8),3.1)
            event=service.engine.events[-1]
            import cv2
            encoded=np.fromfile(service.event_snapshot(event['event_uid']),dtype=np.uint8)
            evidence=cv2.imdecode(encoded,cv2.IMREAD_COLOR)
            self.assertEqual(event['evidence_frames'],3)
            self.assertEqual(evidence.shape[:2],(40,180))
            service.close()

    def test_history_and_snapshot_are_available_from_web(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            service=CareService(Settings(),event_store_path=root/'events.sqlite3',
                                snapshot_dir=root/'shots')
            now=time.monotonic()
            service.engine._request_tracking_review(now,risk_pose=(.7,.85))
            service._capture_event_snapshots(np.zeros((40,60,3),dtype=np.uint8),now)
            uid=service.engine.events[-1]['event_uid']
            app=Flask(__name__);app.register_blueprint(create_blueprint(service))
            client=app.test_client()
            history=client.get('/care/events/history?limit=10')
            self.assertEqual(history.status_code,200)
            self.assertEqual(history.json['events'][0]['event_uid'],uid)
            snapshot=client.get(f'/care/events/{uid}/snapshot')
            self.assertEqual(snapshot.status_code,200)
            self.assertEqual(snapshot.mimetype,'image/jpeg')
            snapshot.close()
            self.assertEqual(client.get('/care/events/not-a-uid/snapshot').status_code,400)
            service.close()


if __name__=='__main__':
    unittest.main()
