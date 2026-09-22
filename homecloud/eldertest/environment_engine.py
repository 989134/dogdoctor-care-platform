"""Fixed-camera environmental screening with an explicit clear-floor baseline."""
import math
from collections import deque
from dataclasses import dataclass, asdict


@dataclass
class EnvironmentSettings:
    floor_top: float = 0.48
    floor_bottom: float = 0.98
    pixel_change_threshold: int = 24
    minimum_area_ratio: float = 0.006
    confirm_seconds: float = 2.0
    cancel_seconds: float = 60.0
    lighting_change_threshold: float = 32.0

    def __post_init__(self):
        if not 0 <= self.floor_top < self.floor_bottom <= 1:
            raise ValueError('invalid floor ROI')
        if not 1 <= self.pixel_change_threshold <= 254:
            raise ValueError('invalid pixel threshold')
        if not 0 < self.minimum_area_ratio < .25:
            raise ValueError('invalid minimum area')
        for value in (self.confirm_seconds, self.cancel_seconds,
                      self.lighting_change_threshold):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid environment setting')


class EnvironmentEngine:
    """Detect changes on a previously clear floor; water output is screening only."""
    def __init__(self, settings=None):
        self.settings = settings or EnvironmentSettings()
        self.baseline = None
        self.sequence = 1_000_000
        self.events = deque(maxlen=100)
        self.active = {}
        self.suppressed = {}
        self.last_regions = []
        self.state = {
            'status': 'awaiting_baseline',
            'baseline_ready': False,
            'path_status': 'unknown',
            'water_status': 'unknown',
            'gas_status': 'sensor_not_connected',
            'gas_value': None,
            'changed_ratio': 0.0,
            'regions': [],
        }

    def set_baseline(self, frame):
        import cv2
        if frame is None or getattr(frame, 'size', 0) == 0:
            raise ValueError('empty baseline frame')
        self.baseline = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.last_regions = []
        for kind, event in list(self.active.items()):
            if event['status'] == 'pending':
                event.update(status='cleared', ended_at=0)
                del self.active[kind]
        self.state.update(status='monitoring', baseline_ready=True, path_status='clear',
                          water_status='no_suspect', changed_ratio=0.0, regions=[])

    def cancel(self, event_id, now):
        for kind, event in list(self.active.items()):
            if event['id'] == event_id:
                event.update(status='cancelled', ended_at=now)
                self.suppressed[kind] = now + self.settings.cancel_seconds
                del self.active[kind]
                return True
        return False

    def _event(self, kind, condition, now):
        event = self.active.get(kind)
        if not condition:
            if event and event['status'] == 'pending':
                event.update(status='cleared', ended_at=now)
                del self.active[kind]
            return
        if now < self.suppressed.get(kind, -1):
            return
        if not event:
            self.sequence += 1
            event = {'id': self.sequence, 'kind': kind, 'status': 'pending',
                     'created_at': now, 'confirm_at': now + self.settings.confirm_seconds}
            self.events.append(event)
            self.active[kind] = event
        if now >= event['confirm_at']:
            event['status'] = 'alert'

    def update(self, frame, now, camera_moving=False, stream_available=True,
               ignore_mask=None):
        import cv2
        import numpy as np
        if not stream_available:
            self.state.update(status='stream_unavailable', path_status='unknown',
                              water_status='unknown', regions=[])
            return self.snapshot(now)
        if camera_moving:
            self.state.update(status='camera_moving', path_status='paused',
                              water_status='paused', regions=[])
            for kind in ('path_obstacle', 'possible_water'):
                self._event(kind, False, now)
            return self.snapshot(now)
        if self.baseline is None:
            self.state.update(status='awaiting_baseline', baseline_ready=False,
                              path_status='unknown', water_status='unknown', regions=[])
            return self.snapshot(now)
        if frame.shape[:2] != self.baseline.shape[:2]:
            self.baseline = None
            self.state.update(status='baseline_size_changed', baseline_ready=False,
                              path_status='unknown', water_status='unknown', regions=[])
            return self.snapshot(now)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, w = gray.shape
        y1, y2 = int(h*self.settings.floor_top), int(h*self.settings.floor_bottom)
        delta = cv2.absdiff(gray[y1:y2], self.baseline[y1:y2])
        lighting_delta = float(np.median(delta))
        if lighting_delta >= self.settings.lighting_change_threshold:
            self.state.update(status='lighting_changed', path_status='unknown',
                              water_status='unknown', changed_ratio=0.0, regions=[])
            for kind in ('path_obstacle', 'possible_water'):
                self._event(kind, False, now)
            return self.snapshot(now)

        mask = cv2.threshold(delta, self.settings.pixel_change_threshold, 255, cv2.THRESH_BINARY)[1]
        if ignore_mask is not None:
            if ignore_mask.shape != gray.shape:
                raise ValueError('ignore mask size does not match frame')
            person = (ignore_mask[y1:y2] > 0).astype(np.uint8)*255
            person = cv2.dilate(person, np.ones((17, 17), np.uint8))
            mask[person > 0] = 0
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        minimum_area = h*w*self.settings.minimum_area_ratio
        regions = []
        has_obstacle = False
        has_water = False
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < minimum_area:
                continue
            x, local_y, rw, rh = cv2.boundingRect(contour)
            y = local_y + y1
            contour_mask = np.zeros((rh, rw), dtype=np.uint8)
            shifted = contour.copy()
            shifted[:, 0, 0] -= x
            shifted[:, 0, 1] -= local_y
            cv2.drawContours(contour_mask, [shifted], -1, 255, -1)
            patch = hsv[y:y+rh, x:x+rw]
            selected = contour_mask > 0
            saturation = float(np.mean(patch[:, :, 1][selected]))
            value = patch[:, :, 2][selected]
            highlight_ratio = float(np.mean(value >= 190))
            aspect = rw/max(rh, 1)
            relative_height = rh/h
            # Wide, shallow, low-saturation/reflective changes are water-like only.
            water_like = (aspect >= 1.8 and relative_height <= .22 and
                          (saturation <= 85 or highlight_ratio >= .12))
            kind = 'possible_water' if water_like else 'path_obstacle'
            has_water |= water_like
            has_obstacle |= not water_like
            regions.append({
                'kind': kind, 'x': x, 'y': y, 'w': rw, 'h': rh,
                'area_ratio': round(area/(h*w), 4),
                'screening_reason': ('wide_shallow_low_saturation_or_reflection'
                                     if water_like else 'persistent_floor_change'),
            })

        changed_ratio = float(np.count_nonzero(mask))/mask.size if mask.size else 0.0
        self._event('path_obstacle', has_obstacle, now)
        self._event('possible_water', has_water, now)
        self.last_regions = regions
        self.state.update(
            status='monitoring', baseline_ready=True,
            path_status='obstacle' if has_obstacle else 'clear',
            water_status='possible_water' if has_water else 'no_suspect',
            changed_ratio=round(changed_ratio, 4), regions=regions,
        )
        return self.snapshot(now)

    def update_gas(self, alarm, sensor_ok, now, value=None, unit=None):
        """Accept a hardware detector result; vision never calls this method."""
        if not sensor_ok:
            self.state.update(gas_status='sensor_error', gas_value=value,
                              gas_unit=unit)
            self._event('gas_alarm', False, now)
            return self.snapshot(now)
        self.state['gas_status'] = 'alarm' if bool(alarm) else 'normal'
        self.state['gas_value'] = value
        self.state['gas_unit'] = unit
        self._event('gas_alarm', bool(alarm), now)
        return self.snapshot(now)

    def draw(self, frame):
        import cv2
        h, w = frame.shape[:2]
        y1, y2 = int(h*self.settings.floor_top), int(h*self.settings.floor_bottom)
        cv2.rectangle(frame, (0, y1), (w-1, y2), (180, 180, 60), 1)
        for region in self.last_regions:
            color = (0, 180, 255) if region['kind'] == 'possible_water' else (0, 0, 255)
            x, y, rw, rh = (region[k] for k in ('x', 'y', 'w', 'h'))
            cv2.rectangle(frame, (x, y), (x+rw, y+rh), color, 2)
            label = 'POSSIBLE WATER' if region['kind'] == 'possible_water' else 'PATH OBSTACLE'
            cv2.putText(frame, label, (x, max(18, y-6)), cv2.FONT_HERSHEY_SIMPLEX,
                        .5, color, 2)
        return frame

    def snapshot(self, now):
        return {
            **self.state,
            'events': [dict(e, remaining_seconds=round(max(0, e['confirm_at']-now), 1))
                       for e in self.events],
            'active_events': [dict(e) for e in self.active.values()],
            'settings': asdict(self.settings),
            'water_is_visual_screening_only': True,
            'gas_requires_physical_sensor': True,
        }
