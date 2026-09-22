"""Local MEC emulator: JPEG in, care/environment results and timing out."""
import argparse
import copy
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'eldertest'))

from care_engine import CareEngine, Settings
from environment_engine import EnvironmentEngine

DEVICE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class MediaPipeDetector:
    def __init__(self):
        import mediapipe as mp
        if not hasattr(mp, 'solutions'):
            raise RuntimeError('MediaPipe pose is unavailable')
        self.mp = mp
        self.lock = threading.Lock()
        self.pose = mp.solutions.pose.Pose(
            model_complexity=1, smooth_landmarks=True, enable_segmentation=True,
            min_detection_confidence=.6, min_tracking_confidence=.6,
        )

    def process(self, frame):
        import cv2
        with self.lock:
            result = self.pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        points = result.pose_landmarks.landmark if result.pose_landmarks else None
        mask = ((result.segmentation_mask > .25).astype('uint8')
                if result.segmentation_mask is not None else None)
        return points, mask


class DeviceRuntime:
    def __init__(self, settings, detector_factory):
        self.care = CareEngine(settings)
        self.environment = EnvironmentEngine()
        self.detector = detector_factory()
        self.lock = threading.RLock()
        self.last_sequence = -1
        self.last_response = None


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.requests = 0
        self.errors = 0
        self.processing_ms = deque(maxlen=1000)
        self.upload_bytes = 0
        self.duplicates = 0
        self.out_of_order = 0

    def record(self, elapsed_ms, error=False,upload_bytes=0,duplicate=False,out_of_order=False):
        with self.lock:
            self.requests += 1
            self.errors += int(error)
            self.processing_ms.append(float(elapsed_ms))
            self.upload_bytes += int(upload_bytes)
            self.duplicates += int(duplicate)
            self.out_of_order += int(out_of_order)

    @staticmethod
    def percentile(values, fraction):
        if not values:
            return None
        values = sorted(values)
        return round(values[min(len(values)-1, int((len(values)-1)*fraction))], 2)

    def snapshot(self):
        with self.lock:
            values = list(self.processing_ms)
            return {
                'requests_total': self.requests,
                'errors_total': self.errors,
                'upload_bytes_total': self.upload_bytes,
                'duplicate_frames_total': self.duplicates,
                'out_of_order_frames_total': self.out_of_order,
                'uptime_seconds': round(time.time()-self.started, 1),
                'processing_ms': {
                    'p50': self.percentile(values, .50),
                    'p95': self.percentile(values, .95),
                    'p99': self.percentile(values, .99),
                    'samples': len(values),
                },
            }


class MecEventStore:
    def __init__(self,path=':memory:'):
        self.lock=threading.RLock()
        if path != ':memory:':
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path=str(path)
        self.connection=sqlite3.connect(path,check_same_thread=False)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute(
            'CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,device_id TEXT NOT NULL,'
            'received_at REAL NOT NULL,payload TEXT NOT NULL)')
        self.connection.commit()

    def add(self,event):
        with self.lock:
            cursor=self.connection.execute(
                'INSERT OR IGNORE INTO events(event_id,device_id,received_at,payload) VALUES(?,?,?,?)',
                (event['event_id'],event['device_id'],time.time(),
                 json.dumps(event,ensure_ascii=False)))
            self.connection.commit()
        return cursor.rowcount==1

    def list(self,device_id=None,limit=100):
        limit=max(1,min(int(limit),500))
        with self.lock:
            if device_id:
                rows=self.connection.execute(
                    'SELECT payload,received_at FROM events WHERE device_id=? '
                    'ORDER BY received_at DESC LIMIT ?',(device_id,limit)).fetchall()
            else:
                rows=self.connection.execute(
                    'SELECT payload,received_at FROM events ORDER BY received_at DESC LIMIT ?',
                    (limit,)).fetchall()
        return [{**json.loads(row['payload']),'received_at':row['received_at']} for row in rows]

    def close(self):
        with self.lock:self.connection.close()


def decode_image(request):
    import cv2
    import numpy as np
    if 'image' in request.files:
        raw = request.files['image'].read()
    elif request.mimetype == 'image/jpeg':
        raw = request.get_data(cache=False)
    else:
        raise ValueError('send multipart field image or Content-Type image/jpeg')
    if not raw:
        raise ValueError('empty image')
    frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('invalid JPEG image')
    return frame


def create_app(detector=None, settings=None,api_token=None,event_store_path=':memory:'):
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
    detector_factory = (lambda: detector) if detector is not None else MediaPipeDetector
    settings = settings or Settings()
    runtimes = {}
    runtimes_lock = threading.Lock()
    metrics = Metrics()
    event_store=MecEventStore(event_store_path)
    app.extensions['mec_event_store']=event_store
    api_token=os.getenv('MEC_API_TOKEN','') if api_token is None else api_token

    @app.before_request
    def authenticate():
        if not api_token or request.path == '/health':
            return None
        supplied=request.headers.get('Authorization','')
        if supplied.startswith('Bearer '):supplied=supplied[7:]
        else:supplied=request.headers.get('X-API-Key','')
        if not hmac.compare_digest(str(supplied),str(api_token)):
            return jsonify(ok=False,error='unauthorized'),401

    def runtime(device_id):
        with runtimes_lock:
            if device_id not in runtimes:
                runtimes[device_id] = DeviceRuntime(
                    copy.deepcopy(settings), detector_factory
                )
            return runtimes[device_id]

    def identity():
        device_id = request.headers.get('X-Device-ID', '').strip()
        if not DEVICE_ID.fullmatch(device_id):
            raise ValueError('invalid or missing X-Device-ID')
        return device_id

    @app.get('/health')
    def health():
        return jsonify(ok=True, service='homecare-mec', model='mediapipe-pose',
                       model_version='care-mediapipe-1.1.0',devices=len(runtimes),
                       auth_required=bool(api_token),time=time.time())

    @app.get('/metrics')
    def metric_endpoint():
        return jsonify(metrics.snapshot())

    @app.post('/v1/infer')
    def infer():
        began = time.monotonic()
        try:
            device_id = identity()
            sequence_raw = request.headers.get('X-Frame-Seq', '')
            if not sequence_raw.isdigit():
                raise ValueError('invalid or missing X-Frame-Seq')
            sequence = int(sequence_raw)
            upload_bytes=int(request.content_length or 0)
            moving = request.headers.get('X-Camera-Moving', '0') == '1'
            received_wall=time.time()
            device = runtime(device_id)
            with device.lock:
                if sequence == device.last_sequence and device.last_response is not None:
                    payload=copy.deepcopy(device.last_response);payload['duplicate']=True
                    elapsed=(time.monotonic()-began)*1000
                    payload['server_processing_ms']=round(elapsed,2)
                    metrics.record(elapsed,upload_bytes=upload_bytes,duplicate=True)
                    return jsonify(payload)
                if sequence < device.last_sequence:
                    elapsed=(time.monotonic()-began)*1000
                    metrics.record(elapsed,error=True,upload_bytes=upload_bytes,out_of_order=True)
                    return jsonify(ok=False,error='out-of-order frame_seq',
                                   last_frame_seq=device.last_sequence),409
                frame = decode_image(request)
                now = time.monotonic()
                points, person_mask = device.detector.process(frame)
                care = device.care.update(points, frame.shape, now, moving)
                environment = device.environment.update(
                    frame, now, moving, ignore_mask=person_mask
                )
                active = care['active_events'] + environment['active_events']
                elapsed = (time.monotonic()-began)*1000
                metrics.record(elapsed,upload_bytes=upload_bytes)
                events = [{**event,
                           'event_id': f"{device_id}-{event['kind']}-{event['id']}"}
                          for event in active]
                payload=dict(
                    ok=True, device_id=device_id, frame_seq=sequence,duplicate=False,
                    captured_at=request.headers.get('X-Captured-At'),
                    received_at=received_wall,completed_at=time.time(),
                    model_version='care-mediapipe-1.1.0',
                    server_processing_ms=round(elapsed, 2),
                    result={
                        'posture': care['posture'],
                        'observation_mode': care['observation_mode'],
                        'angle': care['angle'],
                        'activity': care['activity'],
                        'environment': {
                            'status': environment['status'],
                            'path_status': environment['path_status'],
                            'water_status': environment['water_status'],
                            'gas_status': environment['gas_status'],
                        },
                        'events': events,
                    },
                )
                device.last_sequence=sequence
                device.last_response=copy.deepcopy(payload)
            return jsonify(payload)
        except ValueError as exc:
            metrics.record((time.monotonic()-began)*1000, error=True)
            return jsonify(ok=False, error=str(exc)), 400
        except Exception:
            metrics.record((time.monotonic()-began)*1000, error=True)
            app.logger.exception('MEC inference failed')
            return jsonify(ok=False, error='inference failed'), 500

    @app.post('/v1/environment-baseline')
    def environment_baseline():
        try:
            device_id = identity()
            frame = decode_image(request)
            device = runtime(device_id)
            with device.lock:
                device.environment.set_baseline(frame)
            return jsonify(ok=True, device_id=device_id, baseline='updated')
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @app.post('/v1/events')
    def ingest_event():
        event=request.get_json(silent=True)
        try:
            if not isinstance(event,dict):raise ValueError('JSON event is required')
            event_id=str(event.get('event_id',''))
            device_id=str(event.get('device_id',''))
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',event_id):
                raise ValueError('invalid event_id')
            if not DEVICE_ID.fullmatch(device_id):raise ValueError('invalid device_id')
            if not isinstance(event.get('type'),str) or not event['type']:
                raise ValueError('event type is required')
            inserted=event_store.add(event)
            return jsonify(ok=True,event_id=event_id,duplicate=not inserted),200
        except ValueError as exc:
            return jsonify(ok=False,error=str(exc)),400

    @app.get('/v1/events')
    def list_events():
        try:limit=int(request.args.get('limit','100'))
        except ValueError:return jsonify(ok=False,error='limit must be an integer'),400
        device_id=request.args.get('device_id') or None
        if device_id and not DEVICE_ID.fullmatch(device_id):
            return jsonify(ok=False,error='invalid device_id'),400
        return jsonify(ok=True,events=event_store.list(device_id,limit))

    return app


def main():
    parser = argparse.ArgumentParser(description='Local MEC inference emulator')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7000)
    parser.add_argument('--settings', type=Path, default=ROOT/'care_settings.json')
    parser.add_argument('--event-store',type=Path,default=ROOT/'mec_output'/'mec_events.sqlite3')
    args = parser.parse_args()
    settings = Settings(**json.loads(args.settings.read_text(encoding='utf-8')))
    create_app(settings=settings,event_store_path=args.event_store).run(
        host=args.host, port=args.port, debug=False, use_reloader=False,
        threaded=True, load_dotenv=False
    )


if __name__ == '__main__':
    main()
