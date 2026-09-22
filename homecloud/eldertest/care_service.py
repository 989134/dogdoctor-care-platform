"""Latest-frame pose worker; shared by the focused app and existing full app."""
import copy
import logging
import queue
import threading
import time
import json
from collections import deque
from pathlib import Path
from care_engine import CareEngine, Settings
from care_event_store import CareEventStore
from environment_engine import EnvironmentEngine
from care_privacy import skeleton_required

log = logging.getLogger(__name__)


class CareService:
    def __init__(self, settings=None, settings_path=None, event_store_path=None,
                 snapshot_dir=None,restore_events=True,follow_runtime=None):
        self.engine = CareEngine(settings)
        self.settings_path = Path(settings_path) if settings_path else None
        self.event_store = CareEventStore(event_store_path) if event_store_path else None
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        if self.snapshot_dir:
            self.snapshot_dir.mkdir(parents=True,exist_ok=True)
        self.snapshot_paths = {}
        self.snapshot_finalize_at = {}
        self.evidence_frames = deque(maxlen=12)
        self.last_evidence_at = None
        self._stored_signatures = {}
        self.environment = EnvironmentEngine()
        self.follow = follow_runtime
        self.lock = threading.RLock()
        self.frames = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.jpeg = None
        self.jpeg_private = False
        self.raw_frame = None
        self.last_frame = None
        self.error = '尚未啟動'
        self.fps = 0.0
        if restore_events:
            self._restore_events()

    def enable_persistence(self,event_store_path,snapshot_dir):
        with self.lock:
            if self.event_store:
                return
            self.event_store=CareEventStore(event_store_path)
            self.snapshot_dir=Path(snapshot_dir)
            self.snapshot_dir.mkdir(parents=True,exist_ok=True)
            self._restore_events()

    def submit(self, frame, camera_moving=False):
        if self.follow is not None and self.follow.is_moving():
            camera_moving = True
        item = (frame.copy(), time.monotonic(), camera_moving)
        try:
            self.frames.put_nowait(item)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(item)
            except queue.Full:
                pass

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='CarePose', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.event_store:
            self.event_store.close()
            self.event_store=None
        if self.follow is not None:
            self.follow.close()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            stale = self.last_frame is None or now-self.last_frame > self.engine.settings.max_gap
            if stale:
                self.engine.update(None, (480,640), now, stream_available=False)
                self.environment.update(None, now, stream_available=False)
                self._sync_events(now)
            result = self.engine.snapshot(now)
            environment = self.environment.snapshot(now)
            follow = self.follow.snapshot() if self.follow is not None else {
                'available': False, 'enabled': False, 'locked': False,
                'target_visible': False, 'reason': '跟隨功能尚未啟動'
            }
            return copy.deepcopy(dict(result, environment=environment, follow=follow,
                                      error=self.error, stream_stale=stale,
                                      pose_fps=round(self.fps,1),
                                      frame_age_seconds=None if self.last_frame is None else round(now-self.last_frame,2)))

    def cancel(self, event_id):
        with self.lock:
            now = time.monotonic()
            ok = (self.engine.cancel(event_id, now) or
                  self.environment.cancel(event_id, now))
            self._sync_events(now)
            return ok

    def respond_to_fall(self, response):
        if response not in ('ok', 'help'):
            raise ValueError('response must be ok or help')
        with self.lock:
            candidates = [event for kind,event in self.engine.active.items()
                          if kind in ('fall','tracking_review')]
            if not candidates:
                return False
            event = max(candidates, key=lambda item: item['created_at'])
            now = time.monotonic()
            if response == 'ok':
                ok=self.engine.cancel(event['id'], now)
            else:
                ok=self.engine.escalate(event['id'], now, '長者語音或介面回覆需要協助')
            self._sync_events(now)
            return ok

    def event_history(self,limit=100):
        return self.event_store.history(limit) if self.event_store else []

    def event_snapshot(self,event_uid):
        path=(self.event_store.snapshot_for(event_uid) if self.event_store else
              self.snapshot_paths.get(event_uid))
        return path if path and path.is_file() else None

    def _restore_events(self):
        if not self.event_store:
            return
        now=time.monotonic();wall=time.time()
        for payload,snapshot_path in self.event_store.unresolved():
            saved_wall=float(payload.pop('_saved_wall',wall))
            remaining=float(payload.pop('_remaining_seconds',0))-max(0,wall-saved_wall)
            kind=payload.get('kind')
            if kind not in ('fall','tracking_review') or kind in self.engine.active:
                continue
            payload['created_at']=now
            if payload.get('status') in ('pending','review'):
                key='escalate_at' if kind=='tracking_review' else 'confirm_at'
                payload[key]=now+max(0,remaining)
                if remaining<=0:
                    payload.update(status='alert',risk_level='high',escalated_at=now)
            self.engine.sequence=max(self.engine.sequence,int(payload.get('id',0)))
            self.engine.events.append(payload)
            self.engine.active[kind]=payload
            if snapshot_path:
                self.snapshot_paths[payload['event_uid']]=Path(snapshot_path)
                payload['snapshot_available']=True
            self._sync_events(now,force=True)

    def _capture_event_snapshots(self,frame,now):
        if self.snapshot_dir is None:
            return
        import cv2
        def write_jpeg(path,image):
            ok,encoded=cv2.imencode('.jpg',image)
            if not ok:
                return False
            temporary=path.with_suffix(path.suffix+'.tmp')
            temporary.write_bytes(encoded.tobytes())
            temporary.replace(path)
            return True
        if self.last_evidence_at is None or now-self.last_evidence_at>=.45:
            self.evidence_frames.append((now,frame.copy()))
            self.last_evidence_at=now
        for event in self.engine.events:
            uid=event.get('event_uid')
            if (event.get('kind') not in ('fall','tracking_review') or not uid or
                    uid in self.snapshot_paths):
                continue
            path=self.snapshot_dir/(time.strftime('%Y%m%d-%H%M%S')+'-'+uid+'.jpg')
            if write_jpeg(path,frame):
                self.snapshot_paths[uid]=path.resolve()
                event['snapshot_available']=True
                self.snapshot_finalize_at[uid]=event.get('created_at',now)+2.0
        for event in self.engine.events:
            uid=event.get('event_uid')
            finalize_at=self.snapshot_finalize_at.get(uid)
            if finalize_at is None or now<finalize_at or not self.evidence_frames:
                continue
            created=float(event.get('created_at',now))
            targets=(created-1.0,created,created+2.0)
            selected=[min(self.evidence_frames,key=lambda item:abs(item[0]-target))[1].copy()
                      for target in targets]
            height=min(image.shape[0] for image in selected)
            panels=[]
            for label,image in zip(('BEFORE','EVENT','AFTER'),selected):
                width=round(image.shape[1]*height/image.shape[0])
                panel=cv2.resize(image,(width,height))
                cv2.rectangle(panel,(0,0),(125,30),(15,25,30),-1)
                cv2.putText(panel,label,(8,22),cv2.FONT_HERSHEY_SIMPLEX,.6,
                            (255,255,255),2)
                panels.append(panel)
            path=self.snapshot_paths.get(uid)
            if path is not None and write_jpeg(path,cv2.hconcat(panels)):
                event['evidence_frames']=3
            self.snapshot_finalize_at.pop(uid,None)
        self._sync_events(now,force=True)

    def _sync_events(self,now,force=False):
        if not self.event_store:
            return
        for event in self.engine.events:
            uid=event.get('event_uid')
            if not uid:
                continue
            signature=(event.get('status'),event.get('risk_level'),event.get('ended_at'),
                       event.get('escalated_at'),event.get('snapshot_available'))
            if force or self._stored_signatures.get(uid)!=signature:
                self.event_store.upsert(event,now,self.snapshot_paths.get(uid))
                self._stored_signatures[uid]=signature

    def set_rest_zone(self, zone):
        values = tuple(float(value) for value in zone)
        # Apply the same validation used when care_settings.json is loaded.
        candidate = Settings(**{**self.engine.snapshot(time.monotonic())['settings'],
                                'rest_zones': (values,)})
        with self.lock:
            self.engine.settings.rest_zones = candidate.rest_zones
            self._save_settings()
        return values

    def clear_rest_zones(self):
        with self.lock:
            self.engine.settings.rest_zones = ()
            self._save_settings()

    def _save_settings(self):
        if self.settings_path is None:
            return
        data = self.engine.snapshot(time.monotonic())['settings']
        temporary = self.settings_path.with_suffix(self.settings_path.suffix+'.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.settings_path)

    def set_environment_baseline(self):
        with self.lock:
            if self.raw_frame is None or self.last_frame is None:
                return False, '目前沒有可用畫面'
            if time.monotonic() - self.last_frame > self.engine.settings.max_gap:
                return False, '畫面已過期，請確認攝影機'
            self.environment.set_baseline(self.raw_frame)
            return True, '已將目前淨空通道設為基準'

    def _run(self):
        try:
            import cv2
            import mediapipe as mp
            if not hasattr(mp, 'solutions'):
                raise RuntimeError('此程式需要含 solutions.pose 的 MediaPipe；請依 requirements-care.txt 安裝')
            with mp.solutions.pose.Pose(model_complexity=1, smooth_landmarks=True,
                                       enable_segmentation=True,
                                       min_detection_confidence=0.6, min_tracking_confidence=0.6) as pose:
                self.ready.set()
                while not self.stop_event.is_set():
                    try:
                        frame, captured, moving = self.frames.get(timeout=0.2)
                    except queue.Empty:
                        self.snapshot()
                        continue
                    if time.monotonic()-captured > self.engine.settings.max_gap:
                        continue
                    began = time.monotonic()
                    result = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    points = result.pose_landmarks.landmark if result.pose_landmarks else None
                    person_mask = ((result.segmentation_mask > .25).astype('uint8')
                                   if result.segmentation_mask is not None else None)
                    with self.lock:
                        self.last_frame = captured
                        self.raw_frame = frame.copy()
                        self.error = ''
                        state = self.engine.update(points, frame.shape, captured, moving)
                        environment = self.environment.update(
                            frame, captured, moving, ignore_mask=person_mask
                        )
                        self.fps = 1/max(time.monotonic()-began, 0.001)
                        private=skeleton_required()
                        if private:
                            frame=frame.copy()
                            frame[:]=0
                            # Buffered evidence must never reintroduce raw imagery.
                            self.evidence_frames.clear()
                    if self.follow is not None:
                        self.follow.process(frame, captured, state.get('posture',''))
                    if result.pose_landmarks:
                        mp.solutions.drawing_utils.draw_landmarks(frame, result.pose_landmarks,
                                                                  mp.solutions.pose.POSE_CONNECTIONS)
                    with self.lock:
                        self._capture_event_snapshots(frame,captured)
                    for x1,y1,x2,y2 in self.engine.settings.rest_zones:
                        h,w = frame.shape[:2]
                        cv2.rectangle(frame,(int(x1*w),int(y1*h)),(int(x2*w),int(y2*h)),(255,180,0),2)
                    if not private:
                        self.environment.draw(frame)
                    else:
                        cv2.putText(frame,'PRIVACY: SKELETON ONLY',(12,86),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,220,255),2)
                    if self.follow is not None:
                        self.follow.draw(frame)
                    cv2.putText(frame, state['posture'], (12,30), cv2.FONT_HERSHEY_SIMPLEX,0.7,(0,220,255),2)
                    cv2.putText(frame, f"{state['angle_source']}: {state['angle']}  Still: {state['immobile_seconds']}s",
                                (12,58),cv2.FONT_HERSHEY_SIMPLEX,0.55,(0,220,255),2)
                    ok, encoded = cv2.imencode('.jpg',frame)
                    if ok:
                        with self.lock:
                            self.jpeg = encoded.tobytes()
                            self.jpeg_private = private
        except Exception as exc:
            log.exception('姿態監測無法執行')
            with self.lock:
                self.error = str(exc)


def configured_service():
    root = Path(__file__).resolve().parents[1]
    path = root / 'care_settings.json'
    return CareService(Settings(**json.loads(path.read_text(encoding='utf-8'))), path)


SERVICE = configured_service()
