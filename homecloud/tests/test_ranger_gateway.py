import queue
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ranger_gateway import create_app


class FakeSerial:
    def __init__(self, reply=True):
        self.is_open = True
        self.reply = reply
        self.lines = queue.Queue()
        self.writes = []

    def readline(self):
        try:
            return self.lines.get(timeout=.02)
        except queue.Empty:
            return b''

    def write(self, data):
        self.writes.append(data)
        command = data.decode('ascii').strip()
        if self.reply:
            self.lines.put(('DONE:' + command + ',reason=test\n').encode('ascii'))
        return len(data)

    def flush(self):
        pass


class RangerGatewayTests(unittest.TestCase):
    def test_control_waits_for_matching_done(self):
        serial = FakeSerial()
        client = create_app(serial, done_timeout=.2).test_client()
        response = client.post('/control', json={'command': 'forward'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['done'], 'forward,reason=test')

    def test_timeout_sends_stop(self):
        serial = FakeSerial(reply=False)
        client = create_app(serial, done_timeout=.04).test_client()
        response = client.post('/control', json={'command': 'left'})
        self.assertEqual(response.status_code, 504)
        self.assertEqual(serial.writes, [b'left\n', b'stop\n'])

    def test_stop_can_preempt_busy_request(self):
        serial = FakeSerial(reply=False)
        client1 = create_app(serial, done_timeout=.2).test_client()
        # Use the same Flask app/client state for both calls.
        app = client1.application
        result = {}
        def move():
            with app.test_client() as client:
                result['move'] = client.post('/control', json={'command': 'forward'})
        worker = threading.Thread(target=move)
        worker.start()
        time.sleep(.03)
        serial.reply = True
        with app.test_client() as client:
            stopped = client.post('/control', json={'command': 'stop'})
        worker.join()
        self.assertEqual(stopped.status_code, 200)
        self.assertIn(b'stop\n', serial.writes)

    def test_telemetry_freshness_uses_tel_not_done(self):
        serial = FakeSerial(reply=False)
        client = create_app(serial).test_client()
        serial.lines.put(b'DONE:stop,reason=test\n')
        time.sleep(.04)
        self.assertFalse(client.get('/health').get_json()['telemetry_fresh'])
        serial.lines.put(
            b'TEL:t_ms=1,left_pulse=0,right_pulse=0,left_deg=0,right_deg=0,'
            b'left_rpm=0,right_rpm=0,front_cm=-1,moving=0\n'
        )
        time.sleep(.04)
        self.assertTrue(client.get('/health').get_json()['telemetry_fresh'])


if __name__ == '__main__':
    unittest.main()
