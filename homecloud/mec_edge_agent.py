"""Non-blocking edge agent: local safety stays live while MEC requests stall."""
import argparse
import json
import os
import queue
import threading
import time
import urllib.error
from collections import deque
from pathlib import Path

from mec_edge_client import (EventOutbox,LocalFallback,normalize_event,percentile,
                             request_json,flush_outbox)
from mec_event_sink import (ChtIotEventSink,ChtPlatformSink,ChtTasNotifier,
                            WebhookEventSink)


class ConnectivityState:
    def __init__(self,failure_threshold=2,base_backoff=1,max_backoff=30):
        self.failure_threshold=failure_threshold;self.base_backoff=base_backoff
        self.max_backoff=max_backoff;self.failures=0;self.backoff=base_backoff
        self.next_probe=0.;self.state='starting';self.lock=threading.Lock()

    def can_attempt(self,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:return self.state!='offline' or now>=self.next_probe

    def success(self):
        with self.lock:
            self.state='online';self.failures=0;self.backoff=self.base_backoff
            self.next_probe=0

    def force_probe(self):
        """Allow one immediate retry after a known network transition."""
        with self.lock:self.next_probe=0

    def failure(self,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            self.failures+=1
            if self.failures>=self.failure_threshold:
                self.state='offline';self.next_probe=now+self.backoff
                self.backoff=min(self.max_backoff,self.backoff*2)
            else:self.state='degraded'

    def snapshot(self):
        with self.lock:
            return {'state':self.state,'consecutive_failures':self.failures,
                    'next_probe_seconds':round(max(0,self.next_probe-time.monotonic()),2)}


class RemoteWorker:
    def __init__(self,mec_url,device_id,token,outbox,timeout=2,set_baseline=False):
        self.mec_url=mec_url.rstrip('/');self.device_id=device_id;self.token=token
        self.outbox=outbox;self.timeout=timeout;self.connectivity=ConnectivityState()
        self.tasks=queue.Queue(maxsize=1);self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._run,name='MecRemote',daemon=True)
        self.lock=threading.Lock();self.rows=deque(maxlen=5000)
        self.sent=self.failed=self.dropped=self.skipped=0;self.last_result=None
        self.baseline_pending=bool(set_baseline);self.baseline_updated=False

    def start(self):self.thread.start()

    def submit(self,task,force_probe=False):
        if force_probe:self.connectivity.force_probe()
        if not self.connectivity.can_attempt():
            with self.lock:self.skipped+=1
            return False
        try:self.tasks.put_nowait(task);return True
        except queue.Full:
            try:self.tasks.get_nowait();self.tasks.task_done()
            except queue.Empty:pass
            with self.lock:self.dropped+=1
            try:self.tasks.put_nowait(task);return True
            except queue.Full:return False

    def close(self):
        deadline=time.monotonic()+self.timeout+2
        while self.tasks.unfinished_tasks and time.monotonic()<deadline:time.sleep(.05)
        self.stop_event.set();self.thread.join(timeout=self.timeout+2)

    def snapshot(self):
        with self.lock:
            latest_ok=next((row for row in reversed(self.rows) if row.get('ok')),None)
            return {**self.connectivity.snapshot(),'sent':self.sent,'failed':self.failed,
                    'dropped':self.dropped,'skipped_offline':self.skipped,
                    'queue_depth':self.tasks.qsize(),'last_result':self.last_result,
                    'last_rtt_ms':latest_ok.get('rtt_ms') if latest_ok else None,
                    'environment_baseline_updated':self.baseline_updated}

    def _run(self):
        while not self.stop_event.is_set() or not self.tasks.empty():
            try:task=self.tasks.get(timeout=.2)
            except queue.Empty:continue
            began=time.monotonic()
            headers={'Content-Type':'image/jpeg','X-Device-ID':self.device_id,
                     'X-Frame-Seq':str(task['frame_seq']),
                     'X-Captured-At':str(task['captured_at'])}
            if self.token:headers['Authorization']='Bearer '+self.token
            try:
                request_base=task.get('mec_url',self.mec_url).rstrip('/')
                if self.baseline_pending:
                    request_json(request_base+'/v1/environment-baseline',task['jpeg'],headers,
                                 max(self.timeout,10))
                    self.baseline_pending=False;self.baseline_updated=True
                result=request_json(request_base+'/v1/infer',task['jpeg'],headers,self.timeout)
                rtt=(time.monotonic()-began)*1000
                events=result.get('result',{}).get('events',())
                for event in events:
                    self.outbox.add(normalize_event(event,self.device_id,'mec'))
                row={'frame_seq':task['frame_seq'],'rtt_ms':round(rtt,2),
                     'server_processing_ms':result.get('server_processing_ms'),
                     'end_to_end_ms':round((time.monotonic()-task['started'])*1000,2),
                     'upload_bytes':len(task['jpeg']),'ok':True}
                self.connectivity.success()
                with self.lock:
                    self.sent+=1;self.rows.append(row)
                    self.last_result={'frame_seq':task['frame_seq'],
                                      'posture':result.get('result',{}).get('posture'),
                                      'received_at':time.time()}
            except Exception as exc:
                self.connectivity.failure()
                with self.lock:
                    self.failed+=1;self.rows.append(
                        {'frame_seq':task['frame_seq'],'ok':False,'error':str(exc)[:300]})
            finally:self.tasks.task_done()


class OutboxDispatcher:
    def __init__(self,outbox,sink):
        self.outbox=outbox;self.sink=sink;self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._run,name='EventDispatcher',daemon=True)
        self.delivered=self.failed=0;self.channel_stats={}
    def start(self):self.thread.start()
    def close(self):
        self.stop_event.set();self.thread.join(timeout=6)
        # Make one final delivery attempt for an event created in the last frame.
        result=flush_outbox(self.outbox,self.sink)
        self.delivered+=result['delivered'];self.failed+=result['failed']
        if hasattr(self.sink,'stats'):self.channel_stats=self.sink.stats()
        self.sink.close()
    def _run(self):
        while not self.stop_event.wait(1):
            result=flush_outbox(self.outbox,self.sink)
            self.delivered+=result['delivered'];self.failed+=result['failed']


def build_event_sink(args):
    event_sink=None;label='未設定'
    if args.cht_iot_event_url:
        event_sink=ChtIotEventSink(args.cht_iot_event_url,args.cht_iot_api_key,args.timeout)
        label='中華電信 IoT 事件端點'
    elif args.event_sink_url:
        event_sink=WebhookEventSink(args.event_sink_url,args.event_sink_token,args.timeout)
        label='通用 Webhook'
    tas_values=(args.cht_tas_api_key,args.cht_tas_service_number,args.cht_tas_phones)
    if any(tas_values) and not all(tas_values):
        raise ValueError('CHT TAS requires API key, service number and recipient phones')
    notifier=None
    if all(tas_values):
        notifier=ChtTasNotifier(args.cht_tas_api_key,args.cht_tas_service_number,
                                args.cht_tas_phones.split(','),args.cht_tas_url,args.timeout)
        label=('中華電信 IoT＋TAS 電話通知' if event_sink else
               '中華電信 TAS 電話通知')
    if notifier is not None:
        return ChtPlatformSink(args.platform_ledger,event_sink,notifier),label
    return event_sink,label


def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def outage_active(args,sequence,elapsed_seconds):
    if args.outage_start_seconds>=0 and args.outage_seconds>0:
        return (args.outage_start_seconds<=elapsed_seconds<
                args.outage_start_seconds+args.outage_seconds)
    return (args.outage_start_frame>0 and
            args.outage_start_frame<=sequence<
            args.outage_start_frame+args.outage_frames)


def run(args,local_factory=LocalFallback):
    import cv2
    source=int(args.source) if str(args.source).isdigit() else str(args.source)
    capture=cv2.VideoCapture(source)
    if not capture.isOpened():raise RuntimeError(f'cannot open source: {args.source}')
    recorded=isinstance(source,str) and Path(source).is_file()
    source_fps=capture.get(cv2.CAP_PROP_FPS) if recorded else 0
    stride=max(1,round(source_fps/args.fps)) if source_fps else 1
    outbox=EventOutbox(args.database);local=local_factory(args.device_id)
    remote=RemoteWorker(args.mec_url,args.device_id,args.api_token,outbox,args.timeout,
                        args.set_baseline)
    sink,sink_label=build_event_sink(args)
    dispatcher=OutboxDispatcher(outbox,sink) if sink else None
    remote.start()
    if dispatcher:dispatcher.start()
    sequence=0;local_rows=[];started=time.monotonic();total_jpeg=0
    simulated_outage_active=False
    try:
        while ((args.frames<=0 or sequence<args.frames) and
               (args.duration_seconds<=0 or time.monotonic()-started<args.duration_seconds)):
            frame_started=time.monotonic();ok,frame=capture.read()
            if not ok:
                if recorded and args.loop_source:
                    capture.set(cv2.CAP_PROP_POS_FRAMES,0)
                    continue
                break
            sequence+=1;captured_at=time.time()
            encode_started=time.monotonic()
            encoded_ok,encoded=cv2.imencode('.jpg',frame,
                [cv2.IMWRITE_JPEG_QUALITY,args.jpeg_quality])
            if not encoded_ok:continue
            jpeg=encoded.tobytes();total_jpeg+=len(jpeg)
            forced_outage=outage_active(args,sequence,time.monotonic()-started)
            force_probe=simulated_outage_active and not forced_outage
            simulated_outage_active=forced_outage
            remote.submit({'frame_seq':sequence,'captured_at':captured_at,'jpeg':jpeg,
                           'started':frame_started,
                           'mec_url':'http://127.0.0.1:9' if forced_outage else args.mec_url},
                          force_probe=force_probe)
            local_result=local.process(frame,time.monotonic(),set_baseline=args.set_baseline and sequence==1)
            for event in local_result['events']:
                outbox.add(normalize_event(event,args.device_id,'local_fallback'))
            local_rows.append({'frame_seq':sequence,'fallback_available_ms':round(
                (time.monotonic()-frame_started)*1000,2),'posture':local_result['posture']})
            state={'updated_at':time.time(),'device_id':args.device_id,
                   'network_label':args.network_label,
                   'deployment_label':args.deployment_label,
                   'platform_channel':sink_label,
                   'mode':'mec' if remote.connectivity.snapshot()['state']=='online' else 'local_fallback',
                   'frames_captured':sequence,'local':local_rows[-1],
                   'remote':remote.snapshot(),'outbox':outbox.stats()}
            if dispatcher and hasattr(sink,'stats'):
                state['platform_delivery']=sink.stats()
            atomic_json(args.status_output,state)
            if recorded:
                for _ in range(stride-1):
                    if not capture.grab():break
            delay=1/max(args.fps,.1)-(time.monotonic()-frame_started)
            if delay>0:time.sleep(delay)
    finally:
        capture.release();remote.close()
        if dispatcher:dispatcher.close()
        duration=max(time.monotonic()-started,.001)
        remote_rows=list(remote.rows);successful=[r for r in remote_rows if r['ok']]
        rtt=[r['rtt_ms'] for r in successful];e2e=[r['end_to_end_ms'] for r in successful]
        local_ms=[r['fallback_available_ms'] for r in local_rows]
        report={'device_id':args.device_id,'source':str(args.source),
                'network_label':args.network_label,
                'deployment_label':args.deployment_label,
                'platform_channel':sink_label,
                'mec_url':args.mec_url,'duration_seconds':round(duration,2),
                'simulated_outage':{'start_frame':args.outage_start_frame,
                                    'frames':args.outage_frames,
                                    'start_seconds':args.outage_start_seconds,
                                    'seconds':args.outage_seconds},
                'frames_captured':sequence,'effective_capture_fps':round(sequence/duration,2),
                'local_safety_frames':len(local_rows),'remote':remote.snapshot(),
                'rtt_ms':{'p50':percentile(rtt,.5),'p95':percentile(rtt,.95),'p99':percentile(rtt,.99)},
                'end_to_end_ms':{'p50':percentile(e2e,.5),'p95':percentile(e2e,.95),'p99':percentile(e2e,.99)},
                'fallback_available_ms':{'p50':percentile(local_ms,.5),'p95':percentile(local_ms,.95)},
                'encoded_bytes':total_jpeg,'average_encoded_mbps':round(total_jpeg*8/duration/1e6,3),
                'outbox':outbox.stats(),'events_delivered':dispatcher.delivered if dispatcher else 0,
                'event_delivery_failures':dispatcher.failed if dispatcher else 0,
                'platform_delivery':dispatcher.channel_stats if dispatcher else {},
                'remote_rows':remote_rows}
        atomic_json(args.report,report);outbox.close()
    return report


def main():
    parser=argparse.ArgumentParser(description='Non-blocking homecare edge agent')
    parser.add_argument('--source',default='0');parser.add_argument('--mec-url',default='http://127.0.0.1:7000')
    parser.add_argument('--device-id',default='ranger-001');parser.add_argument('--frames',type=int,default=0)
    parser.add_argument('--network-label',default='目前網路（未標示 5G）')
    parser.add_argument('--deployment-label',default='MEC 相容端點')
    parser.add_argument('--duration-seconds',type=float,default=0)
    parser.add_argument('--loop-source',action='store_true')
    parser.add_argument('--fps',type=float,default=5);parser.add_argument('--jpeg-quality',type=int,default=75)
    parser.add_argument('--timeout',type=float,default=2);parser.add_argument('--set-baseline',action='store_true')
    parser.add_argument('--outage-start-frame',type=int,default=0)
    parser.add_argument('--outage-frames',type=int,default=0)
    parser.add_argument('--outage-start-seconds',type=float,default=-1)
    parser.add_argument('--outage-seconds',type=float,default=0)
    parser.add_argument('--api-token',default=os.getenv('MEC_API_TOKEN',''))
    parser.add_argument('--event-sink-url',default=os.getenv('EVENT_SINK_URL',''))
    parser.add_argument('--event-sink-token',default=os.getenv('EVENT_SINK_TOKEN',''))
    parser.add_argument('--cht-iot-event-url',default=os.getenv('CHT_IOT_EVENT_URL',''))
    parser.add_argument('--cht-iot-api-key',default=os.getenv('CHT_IOT_API_KEY',''))
    parser.add_argument('--cht-tas-url',default=os.getenv('CHT_TAS_URL',ChtTasNotifier.DEFAULT_URL))
    parser.add_argument('--cht-tas-api-key',default=os.getenv('CHT_TAS_API_KEY',''))
    parser.add_argument('--cht-tas-service-number',default=os.getenv('CHT_TAS_SERVICE_NUMBER',''))
    parser.add_argument('--cht-tas-phones',default=os.getenv('CHT_TAS_PHONES',''))
    parser.add_argument('--platform-ledger',type=Path,
                        default=Path('mec_output/cht_platform_ledger.sqlite3'))
    parser.add_argument('--database',type=Path,default=Path('mec_output/edge_outbox.sqlite3'))
    parser.add_argument('--status-output',type=Path,default=Path('mec_output/edge_status.json'))
    parser.add_argument('--report',type=Path,default=Path('mec_output/edge_agent_report.json'))
    args=parser.parse_args()
    if args.fps<=0 or args.timeout<=0:parser.error('fps and timeout must be positive')
    if (args.frames<0 or args.duration_seconds<0 or args.outage_start_frame<0 or
            args.outage_frames<0 or args.outage_start_seconds< -1 or args.outage_seconds<0):
        parser.error('frame, duration and outage values must be non-negative')
    if ((args.outage_start_seconds>=0) != (args.outage_seconds>0)):
        parser.error('set outage-start-seconds and a positive outage-seconds together')
    if not 20<=args.jpeg_quality<=95:parser.error('jpeg-quality must be 20..95')
    report=run(args)
    print(json.dumps({k:v for k,v in report.items() if k!='remote_rows'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
