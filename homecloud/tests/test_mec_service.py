import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mec_service import create_app


class EmptyDetector:
    def process(self, frame):
        return None, None


def jpeg(value=100):
    frame = np.full((120, 160, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode('.jpg', frame)
    assert ok
    return encoded.tobytes()


class MecServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app(detector=EmptyDetector()).test_client()
        self.headers = {'X-Device-ID': 'ranger-001', 'X-Frame-Seq': '1',
                        'Content-Type': 'image/jpeg'}

    def test_health_and_inference_contract(self):
        self.assertTrue(self.client.get('/health').json['ok'])
        response = self.client.post('/v1/infer', data=jpeg(), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(data['device_id'], 'ranger-001')
        self.assertEqual(data['frame_seq'], 1)
        self.assertIn('server_processing_ms', data)
        self.assertEqual(data['result']['posture'], 'no_person')
        self.assertEqual(data['result']['environment']['status'], 'awaiting_baseline')
        duplicate=self.client.post('/v1/infer',data=jpeg(),headers=self.headers)
        self.assertEqual(duplicate.status_code,200)
        self.assertTrue(duplicate.json['duplicate'])
        old={**self.headers,'X-Frame-Seq':'0'}
        self.assertEqual(self.client.post('/v1/infer',data=jpeg(),headers=old).status_code,409)

    def test_baseline_then_environment_status(self):
        response = self.client.post('/v1/environment-baseline', data=jpeg(),
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/v1/infer', data=jpeg(), headers=self.headers)
        self.assertEqual(response.json['result']['environment']['path_status'], 'clear')

    def test_validation_and_metrics(self):
        self.assertEqual(self.client.post('/v1/infer', data=b'bad',
                         headers=self.headers).status_code, 400)
        headers={**self.headers, 'X-Device-ID': '../bad'}
        self.assertEqual(self.client.post('/v1/infer', data=jpeg(),
                         headers=headers).status_code, 400)
        metrics=self.client.get('/metrics').json
        self.assertEqual(metrics['requests_total'], 2)
        self.assertEqual(metrics['errors_total'], 2)

    def test_optional_bearer_authentication(self):
        client=create_app(detector=EmptyDetector(),api_token='secret').test_client()
        self.assertEqual(client.get('/health').status_code,200)
        self.assertEqual(client.post('/v1/infer',data=jpeg(),headers=self.headers).status_code,401)
        authorized={**self.headers,'Authorization':'Bearer secret'}
        self.assertEqual(client.post('/v1/infer',data=jpeg(),headers=authorized).status_code,200)
        self.assertEqual(client.get('/metrics').status_code,401)

    def test_event_ingest_is_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.sqlite3'
            client=create_app(detector=EmptyDetector(),event_store_path=path).test_client()
            event={'schema_version':'1.0','event_id':'ranger-001:fall:1',
                   'device_id':'ranger-001','type':'suspected_fall','severity':'high'}
            first=client.post('/v1/events',json=event)
            second=client.post('/v1/events',json=event)
            self.assertFalse(first.json['duplicate'])
            self.assertTrue(second.json['duplicate'])
            listed=client.get('/v1/events?device_id=ranger-001').json['events']
            self.assertEqual(len(listed),1)
            self.assertEqual(listed[0]['event_id'],event['event_id'])
            client.application.extensions['mec_event_store'].close()


if __name__ == '__main__':
    unittest.main()
