"""Camera/video client for the local MEC emulator with timing and event outbox."""
import argparse
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from mec_service import MediaPipeDetector
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'eldertest'))
from care_engine import CareEngine, Settings
from environment_engine import EnvironmentEngine
from mec_event_sink import WebhookEventSink


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values)-1, int((len(values)-1)*fraction))], 2)


class EventOutbox:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path,check_same_thread=False)
        self.connection.execute(
            'CREATE TABLE IF NOT EXISTS events ('
            'event_id TEXT PRIMARY KEY, created_at REAL NOT NULL, payload TEXT NOT NULL, '
            'platform_delivered INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, '
            'next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT)'
        )
        columns={row[1] for row in self.connection.execute('PRAGMA table_info(events)')}
        for name,definition in [('attempts','INTEGER NOT NULL DEFAULT 0'),
                                ('next_attempt','REAL NOT NULL DEFAULT 0'),
                                ('last_error','TEXT')]:
            if name not in columns:
                self.connection.execute(f'ALTER TABLE events ADD COLUMN {name} {definition}')
        self.connection.commit()

    def add(self, event):
        event_id = event.get('event_id')
        if not event_id:
            raise ValueError('event_id is required')
        with self.lock:
            cursor = self.connection.execute(
                'INSERT OR IGNORE INTO events(event_id,created_at,payload) VALUES(?,?,?)',
                (event_id, time.time(), json.dumps(event, ensure_ascii=False)),
            )
            self.connection.commit()
        return cursor.rowcount == 1

    def count(self):
        with self.lock:
            return self.connection.execute(
                'SELECT COUNT(*) FROM events WHERE platform_delivered=0'
            ).fetchone()[0]

    def pending(self,limit=50,now=None):
        now=time.time() if now is None else now
        with self.lock:
            rows=self.connection.execute(
                'SELECT event_id,payload,attempts FROM events WHERE platform_delivered=0 '
                'AND next_attempt<=? ORDER BY created_at LIMIT ?',
                (now,max(1,min(int(limit),500)))).fetchall()
        return [(row[0],json.loads(row[1]),row[2]) for row in rows]

    def mark_delivered(self,event_id):
        with self.lock:
            self.connection.execute(
                'UPDATE events SET platform_delivered=1,last_error=NULL WHERE event_id=?',(event_id,))
            self.connection.commit()

    def mark_failed(self,event_id,error,now=None):
        now=time.time() if now is None else now
        with self.lock:
            row=self.connection.execute('SELECT attempts FROM events WHERE event_id=?',
                                        (event_id,)).fetchone()
            attempts=(row[0] if row else 0)+1
            delay=min(300,2**min(attempts,8))
            self.connection.execute(
                'UPDATE events SET attempts=?,next_attempt=?,last_error=? WHERE event_id=?',
                (attempts,now+delay,str(error)[:500],event_id))
            self.connection.commit()
        return delay

    def stats(self):
        with self.lock:
            row=self.connection.execute(
                'SELECT COUNT(*),SUM(CASE WHEN platform_delivered=0 THEN 1 ELSE 0 END),'
                'COALESCE(SUM(attempts),0) FROM events').fetchone()
        return {'total':row[0],'pending':row[1] or 0,'delivery_attempts':row[2]}

    def close(self):
        with self.lock:self.connection.close()


def normalize_event(event,device_id,mode):
    kind=event.get('kind') or event.get('type') or 'care_event'
    incident_id=event['event_id'];state=event.get('status','active')
    return {
        'schema_version':'1.0',
        # A review and its later high-risk escalation are separate delivery
        # revisions, while incident_id still ties them to the same incident.
        'event_id':f'{incident_id}:{state}',
        'incident_id':incident_id,
        'device_id':device_id,
        'occurred_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'type':kind,
        'severity':'high' if event.get('status')=='alert' or
                   event.get('risk_level')=='high' else 'warning',
        'mode':mode,
        'acknowledged':False,
        'details':{k:v for k,v in event.items() if k!='event_id'},
    }


def flush_outbox(outbox,sink,limit=20):
    if sink is None:return {'delivered':0,'failed':0}
    delivered=failed=0
    for event_id,event,_ in outbox.pending(limit):
        try:
            sink.upload_event(event)
            outbox.mark_delivered(event_id);delivered+=1
        except Exception as exc:
            outbox.mark_failed(event_id,exc);failed+=1
            break
    return {'delivered':delivered,'failed':failed}


def request_json(url, body=None, headers=None, timeout=8):
    request = urllib.request.Request(
        url, data=body, method='POST' if body is not None else 'GET', headers=headers or {}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class LocalFallback:
    """Shadow local inference so temporal state is ready before an outage."""
    def __init__(self, device_id):
        self.device_id = device_id
        self.detector = MediaPipeDetector()
        self.care = CareEngine(Settings())
        self.environment = EnvironmentEngine()
        self.baseline_set = False

    def process(self, frame, now, set_baseline=False):
        began = time.monotonic()
        if set_baseline and not self.baseline_set:
            self.environment.set_baseline(frame)
            self.baseline_set = True
        points, mask = self.detector.process(frame)
        care = self.care.update(points, frame.shape, now)
        environment = self.environment.update(frame, now, ignore_mask=mask)
        active = care['active_events'] + environment['active_events']
        events = [
            {**event,
             # Match the MEC event key so shadow inference and failover do not
             # notify the same incident twice when both engines agree.
             'event_id': f"{self.device_id}-{event['kind']}-{event['id']}",
             'mode': 'local_fallback'}
            for event in active
        ]
        return {
            'posture': care['posture'], 'events': events,
            'processing_ms': round((time.monotonic()-began)*1000, 2),
        }


def run(source, mec_url, device_id, frame_limit, fps, quality, output, database,
        set_baseline=False, outage_start=0, outage_frames=0, timeout=2.0,
        api_token='',event_sink_url='',event_sink_token=''):
    import cv2
    source_value = int(source) if str(source).isdigit() else str(source)
    try:
        health = request_json(mec_url.rstrip('/') + '/health', timeout=timeout)
        mec_initially_available = bool(health.get('ok'))
    except (urllib.error.URLError, TimeoutError, ValueError):
        health = {'ok': False, 'error': 'initial health check failed; local fallback active'}
        mec_initially_available = False
    capture = cv2.VideoCapture(source_value)
    if not capture.isOpened():
        raise RuntimeError(f'cannot open source: {source}')
    outbox = EventOutbox(database)
    sink=WebhookEventSink(event_sink_url,event_sink_token,timeout) if event_sink_url else None
    fallback = LocalFallback(device_id)
    rows = []
    interval = 1/max(fps, .1)
    is_recorded_file = isinstance(source_value, str) and Path(source_value).is_file()
    source_fps = capture.get(cv2.CAP_PROP_FPS) if is_recorded_file else 0
    source_frames_per_sample = max(1, round(source_fps/fps)) if source_fps > 0 else 1
    sequence = 0
    failures = 0
    remote_failures = 0
    remote_baseline_pending = bool(set_baseline)
    total_upload_bytes=0
    delivered_total=failed_delivery_total=0
    run_started=time.monotonic()
    try:
        while frame_limit <= 0 or sequence < frame_limit:
            loop_started = time.monotonic()
            ok, frame = capture.read()
            if not ok:
                break
            sequence += 1
            captured=time.time()
            encode_started=time.monotonic()
            encoded_ok, encoded = cv2.imencode(
                '.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
            )
            if not encoded_ok:
                failures += 1
                continue
            raw = encoded.tobytes()
            encode_ms=(time.monotonic()-encode_started)*1000
            total_upload_bytes+=len(raw)
            headers = {
                'Content-Type': 'image/jpeg',
                'X-Device-ID': device_id,
                'X-Frame-Seq': str(sequence),
                'X-Captured-At': str(captured),
            }
            if api_token:headers['Authorization']='Bearer '+api_token
            local = fallback.process(
                frame, time.monotonic(), set_baseline=(sequence == 1 and set_baseline)
            )
            local_ready_ms=(time.monotonic()-loop_started)*1000
            forced_outage = (outage_start > 0 and
                             outage_start <= sequence < outage_start+outage_frames)
            request_base = 'http://127.0.0.1:9' if forced_outage else mec_url.rstrip('/')
            began = time.monotonic()
            try:
                if remote_baseline_pending:
                    request_json(request_base + '/v1/environment-baseline',
                                 raw, headers, timeout=max(timeout, 10.0))
                    remote_baseline_pending = False
                result = request_json(request_base + '/v1/infer', raw, headers,
                                      timeout=timeout)
                rtt_ms = (time.monotonic()-began)*1000
                remote_ready_ms=(time.monotonic()-loop_started)*1000
                server_ms = float(result['server_processing_ms'])
                events = result['result'].get('events', [])
                stored = sum(outbox.add(normalize_event(event,device_id,'mec')) for event in events)
                delivery=flush_outbox(outbox,sink)
                delivered_total+=delivery['delivered'];failed_delivery_total+=delivery['failed']
                rows.append({
                    'frame_seq': sequence,
                    'rtt_ms': round(rtt_ms, 2),
                    'server_processing_ms': round(server_ms, 2),
                    'transport_overhead_ms': round(max(0, rtt_ms-server_ms), 2),
                    'response_bytes': len(json.dumps(result)),
                    'upload_bytes': len(raw),
                    'encode_ms':round(encode_ms,2),
                    'local_shadow_processing_ms':local['processing_ms'],
                    'end_to_end_ms':round(remote_ready_ms,2),
                    'posture': result['result']['posture'],
                    'events_received': len(events),
                    'new_events_stored': stored,
                    'events_delivered':delivery['delivered'],
                    'mode': 'mec',
                })
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                remote_failures += 1
                try:
                    stored = sum(outbox.add(normalize_event(event,device_id,'local_fallback'))
                                 for event in local['events'])
                    delivery=flush_outbox(outbox,sink)
                    delivered_total+=delivery['delivered'];failed_delivery_total+=delivery['failed']
                    rows.append({
                        'frame_seq': sequence, 'mode': 'local_fallback',
                        'mec_error': str(exc),
                        'fallback_processing_ms': local['processing_ms'],
                        'fallback_available_ms':round(local_ready_ms,2),
                        'encode_ms':round(encode_ms,2),
                        'posture': local['posture'],
                        'events_received': len(local['events']),
                        'new_events_stored': stored,
                        'events_delivered':delivery['delivered'],
                    })
                except Exception as fallback_error:
                    failures += 1
                    rows.append({'frame_seq': sequence, 'mode': 'failed',
                                 'mec_error': str(exc),
                                 'fallback_error': str(fallback_error)})
            delay = interval-(time.monotonic()-loop_started)
            if is_recorded_file:
                for _ in range(source_frames_per_sample-1):
                    if not capture.grab():
                        break
            if delay > 0:
                time.sleep(delay)
    finally:
        capture.release()
        delivery=flush_outbox(outbox,sink,limit=100)
        delivered_total+=delivery['delivered'];failed_delivery_total+=delivery['failed']
        queued = outbox.count()
        outbox_stats=outbox.stats()
        outbox.close()
        if sink:sink.close()

    remote_rows = [row for row in rows if row.get('mode') == 'mec']
    fallback_rows = [row for row in rows if row.get('mode') == 'local_fallback']
    successful = remote_rows + fallback_rows
    rtt = [row['rtt_ms'] for row in remote_rows]
    server = [row['server_processing_ms'] for row in remote_rows]
    e2e=[row['end_to_end_ms'] for row in remote_rows]
    fallback_ready=[row['fallback_available_ms'] for row in fallback_rows]
    duration=max(time.monotonic()-run_started,.001)
    report = {
        'source': str(source), 'mec_url': mec_url, 'device_id': device_id,
        'source_fps': round(source_fps, 3) if source_fps else None,
        'source_frames_per_sample': source_frames_per_sample,
        'mec_initially_available': mec_initially_available,
        'frames_attempted': sequence, 'frames_succeeded': len(successful),
        'frames_failed': failures, 'success_rate': round(len(successful)/max(sequence, 1), 4),
        'mec_frames': len(remote_rows), 'local_fallback_frames': len(fallback_rows),
        'mec_request_failures': remote_failures,
        'simulated_outage': {'start_frame': outage_start, 'frames': outage_frames},
        'rtt_ms': {'p50': percentile(rtt,.5), 'p95': percentile(rtt,.95),
                   'p99': percentile(rtt,.99)},
        'server_processing_ms': {'p50': percentile(server,.5), 'p95': percentile(server,.95),
                                 'p99': percentile(server,.99)},
        'end_to_end_ms': {'p50':percentile(e2e,.5),'p95':percentile(e2e,.95),
                          'p99':percentile(e2e,.99)},
        'fallback_available_ms': {'p50':percentile(fallback_ready,.5),
                                  'p95':percentile(fallback_ready,.95)},
        'duration_seconds':round(duration,2),
        'effective_fps':round(sequence/duration,2),
        'upload_bytes':total_upload_bytes,
        'average_upload_mbps':round(total_upload_bytes*8/duration/1_000_000,3),
        'outbox_pending_events': queued,
        'outbox':outbox_stats,
        'events_delivered_this_run':delivered_total,
        'event_delivery_failures':failed_delivery_total,
        'rows': rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description='Local MEC edge-client test')
    parser.add_argument('--source', default='0')
    parser.add_argument('--mec-url', default='http://127.0.0.1:7000')
    parser.add_argument('--device-id', default='ranger-001')
    parser.add_argument('--frames', type=int, default=50)
    parser.add_argument('--fps', type=float, default=5)
    parser.add_argument('--jpeg-quality', type=int, default=75)
    parser.add_argument('--set-baseline', action='store_true')
    parser.add_argument('--outage-start-frame', type=int, default=0,
                        help='simulate MEC outage starting at this frame; 0 disables')
    parser.add_argument('--outage-frames', type=int, default=0)
    parser.add_argument('--timeout', type=float, default=2.0)
    parser.add_argument('--api-token',default='',help='MEC Bearer token')
    parser.add_argument('--event-sink-url',default='',help='待傳事件接收端，例如 https://host/v1/events')
    parser.add_argument('--event-sink-token',default='',help='事件接收端 Bearer token')
    parser.add_argument('--output', type=Path, default=Path('mec_output/latency.json'))
    parser.add_argument('--database', type=Path, default=Path('mec_output/outbox.sqlite3'))
    args = parser.parse_args()
    if not 20 <= args.jpeg_quality <= 95:
        parser.error('jpeg-quality must be 20..95')
    report = run(args.source, args.mec_url, args.device_id, args.frames, args.fps,
                 args.jpeg_quality, args.output, args.database, args.set_baseline,
                 args.outage_start_frame, args.outage_frames, args.timeout,
                 args.api_token,args.event_sink_url,args.event_sink_token)
    print(json.dumps({k:v for k,v in report.items() if k != 'rows'}, ensure_ascii=False, indent=2))
    print(args.output.resolve())


if __name__ == '__main__':
    main()
