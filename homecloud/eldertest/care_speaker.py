"""Optional local speaker output for the fall confirmation workflow."""
import logging
import queue
import threading
import time


log = logging.getLogger(__name__)


class CareSpeaker:
    def __init__(self, engine_factory=None):
        self._engine_factory = engine_factory
        self._queue = queue.Queue(maxsize=8)
        self._spoken = set()
        self._stop = threading.Event()
        self._thread = None
        self.available = engine_factory is not None
        if engine_factory is None:
            try:
                import pyttsx3
                self._engine_factory = pyttsx3.init
                self.available = True
            except Exception as exc:
                log.warning('本機語音輸出無法使用：%s', exc)

    def start(self):
        if not self.available or (self._thread and self._thread.is_alive()):
            return self.available
        self._thread = threading.Thread(target=self._run,name='CareSpeaker',daemon=True)
        self._thread.start()
        return True

    def observe(self, events):
        for event in events:
            if event.get('kind') not in ('fall','tracking_review'):
                continue
            status=event.get('status')
            if status not in ('pending','review','alert'):
                continue
            uid=event.get('event_uid') or f"{event.get('kind')}:{event.get('id')}"
            phase='alert' if status=='alert' else 'question'
            key=f'{uid}:{phase}'
            if key in self._spoken:
                continue
            self._spoken.add(key)
            text=('警報已升級。未收到回應，也沒有偵測到重新站立，請家人立即查看。'
                  if phase=='alert' else
                  '偵測到可能跌倒。你還好嗎？如果沒事請說我沒事，需要協助請說救命。')
            try:
                self._queue.put_nowait(text)
            except queue.Full:
                log.warning('語音提示佇列已滿，略過重複提示')

    def close(self):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        try:
            engine=self._engine_factory()
            try:
                engine.setProperty('rate',155)
            except Exception:
                pass
            while not self._stop.is_set():
                text=self._queue.get()
                if text is None:
                    break
                engine.say(text)
                engine.runAndWait()
        except Exception as exc:
            self.available=False
            log.error('本機語音輸出失敗：%s',exc)


class RobotCareSpeaker:
    """Send arrival greetings and care alerts to the speaker on the robot."""

    ALERT_TEXT = {
        'prolonged_sitting': '紅色警示，連續坐姿已超過十秒，請立即確認長者狀況。',
        'immobile': '警告，偵測到長時間沒有活動，請確認主人目前狀況。',
        'wandering': '警告，偵測到持續徘徊，請照護者留意。',
        'path_obstacle': '安全警告，前方通道出現障礙物。',
        'possible_water': '安全警告，地面可能有積水，請小心行走。',
        'gas_alarm': '緊急警告，瓦斯感測器發出警報，請立即離開並聯絡協助。',
    }

    def __init__(self, audio_client):
        self.audio_client = audio_client
        self._queue = queue.Queue(maxsize=16)
        self._queued = set()
        self._spoken = set()
        self._retry_after = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._arrival_sequence = 0
        self.available = audio_client is not None

    def start(self):
        if not self.available or (self._thread and self._thread.is_alive()):
            return self.available
        self._thread = threading.Thread(target=self._run, name='RobotCareSpeaker', daemon=True)
        self._thread.start()
        return True

    def say(self, key, text, priority='normal'):
        with self._lock:
            if (key in self._spoken or key in self._queued or
                    time.monotonic() < self._retry_after.get(key, 0)):
                return False
            self._queued.add(key)
        try:
            self._queue.put_nowait((key, text, priority))
            return True
        except queue.Full:
            with self._lock:
                self._queued.discard(key)
            log.warning('車上語音提示佇列已滿')
            return False

    def arrival(self):
        # The follow runtime invokes this once per approach.  Use an increasing
        # key so approaching again later produces another greeting.
        with self._lock:
            self._arrival_sequence += 1
            sequence = self._arrival_sequence
        return self.say(f'arrival:{sequence}', '主人你今天好嗎？我也很想你。')

    def observe(self, events):
        for event in events:
            kind = event.get('kind')
            status = event.get('status')
            if status not in ('pending', 'review', 'alert'):
                continue
            uid = event.get('event_uid') or f"{kind}:{event.get('id')}"
            phase = 'alert' if status == 'alert' else 'question'
            if kind in ('fall', 'tracking_review'):
                text = ('緊急警告，疑似跌倒後沒有收到回應，請家人立即查看。'
                        if phase == 'alert' else
                        '警告，偵測到可能跌倒。你還好嗎？如果沒事請說我沒事，需要協助請說救命。')
                self.say(f'{uid}:{phase}', text, 'emergency' if phase == 'alert' else 'high')
            elif kind in self.ALERT_TEXT and status == 'alert':
                self.say(f'{uid}:alert', self.ALERT_TEXT[kind], 'emergency')

    def close(self):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=6)

    def _run(self):
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            key, text, priority = item
            try:
                self.audio_client.speak(text, priority=priority)
            except Exception as exc:
                log.error('車上喇叭播放失敗：%s', exc)
                with self._lock:
                    self._retry_after[key] = time.monotonic() + 10.0
            else:
                with self._lock:
                    self._spoken.add(key)
                    self._retry_after.pop(key, None)
            finally:
                with self._lock:
                    self._queued.discard(key)


def watch_service(service, speaker, stop_event):
    """Poll the care state without blocking pose inference or Flask requests."""
    while not stop_event.wait(.25):
        state = service.snapshot()
        events = list(state.get('active_events', ()))
        events.extend(state.get('environment', {}).get('active_events', ()))
        speaker.observe(events)
