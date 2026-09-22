"""Camera-independent, single-person temporal screening. No model/network side effects."""
import math
import uuid
from collections import deque
from dataclasses import dataclass, asdict


@dataclass
class Settings:
    visibility: float = 0.6
    max_gap: float = 0.8
    fall_hold: float = 2.0
    confirm_seconds: float = 8.0
    fall_response_seconds: float = 20.0
    recovery_hold_seconds: float = 0.5
    immobile_seconds: float = 60.0
    prolonged_sitting_seconds: float = 10.0
    wandering_seconds: float = 30.0
    wandering_reversals: int = 4
    cancel_seconds: float = 0.0
    rest_zones: tuple = ()  # normalized x1,y1,x2,y2; fixed camera only

    def __post_init__(self):
        for name in ('max_gap', 'fall_hold', 'confirm_seconds', 'fall_response_seconds',
                     'recovery_hold_seconds', 'immobile_seconds', 'prolonged_sitting_seconds', 'wandering_seconds'):
            value = getattr(self, name)
            if not isinstance(value, (int,float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(name + ' must be positive and finite')
        if (not isinstance(self.cancel_seconds,(int,float)) or
                not math.isfinite(self.cancel_seconds) or self.cancel_seconds < 0):
            raise ValueError('cancel_seconds must be non-negative and finite')
        if not 0 < self.visibility <= 1 or not isinstance(self.wandering_reversals,int) or self.wandering_reversals < 1:
            raise ValueError('Invalid confidence/reversal setting')
        for zone in self.rest_zones:
            if len(zone) != 4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in zone):
                raise ValueError('Invalid rest zone')
            x1,y1,x2,y2=zone
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
                raise ValueError('Rest zones must be normalized rectangles')


class CareEngine:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.torso_motion = deque()
        self.descent_motion = deque()
        self.risk_history = deque()
        self.last_risk = None
        self.history = deque()
        self.events = deque(maxlen=100)
        self.active = {}
        self.suppressed = {}
        self.sequence = 0
        self.last_time = None
        self.anchor = None
        self.still_since = None
        self.sitting_since = None
        self.fall_since = None
        self.fall_transition = False
        self.review_recovery_since = None
        self.state = {'posture': 'no_person', 'tracking': False, 'angle': None,
                       'immobile_seconds': 0, 'sitting_seconds': 0, 'wandering_reversals': 0,
                      'observation_mode': 'none', 'angle_source': 'none', 'activity': 'unknown'}

    def _reset_tracking(self):
        self.history.clear()
        self.anchor = None
        self.still_since = None
        self.sitting_since = None
        self.fall_since = None
        self.fall_transition = False

    def cancel(self, event_id, now):
        for kind, event in list(self.active.items()):
            if event['id'] == event_id:
                event.update(status='cancelled', ended_at=now)
                self.suppressed[kind] = now + self.settings.cancel_seconds
                del self.active[kind]
                if kind in ('fall', 'tracking_review'):
                    self.review_recovery_since = None
                return True
        return False

    def escalate(self, event_id, now, reason='收到求助回覆'):
        for kind in ('fall', 'tracking_review'):
            event = self.active.get(kind)
            if event and event['id'] == event_id:
                event.update(status='alert', risk_level='high', escalated_at=now,
                             escalation_reason=reason)
                self.review_recovery_since = None
                return True
        return False

    def _event(self, kind, condition, now):
        event = self.active.get(kind)
        if not condition:
            if kind == 'fall' and event and event['status'] in ('pending', 'review'):
                # A suspected fall is cleared only by cancellation or sustained
                # standing; occlusion/sitting must not silently dismiss it.
                return
            # Confirmed events stay visible until acknowledged; loss of visibility is not recovery.
            if event and event['status'] == 'pending':
                event.update(status='cleared', ended_at=now)
                del self.active[kind]
            return
        if now < self.suppressed.get(kind, -1):
            return
        if not event:
            self.sequence += 1
            confirm_seconds = (self.settings.fall_response_seconds if kind == 'fall' else
                               0.0 if kind == 'prolonged_sitting' else
                               self.settings.confirm_seconds)
            event = {'id': self.sequence, 'event_uid': uuid.uuid4().hex,
                     'kind': kind, 'status': 'pending',
                     'created_at': now, 'confirm_at': now + confirm_seconds}
            if kind == 'fall':
                event.update(risk_level='needs_confirmation',
                             voice_prompt='偵測到可能跌倒。你還好嗎？請先不要急著站起來；若沒事，請按取消警報。')
            elif kind == 'prolonged_sitting':
                event.update(risk_level='high',
                             reason=f'連續坐姿已達 {self.settings.prolonged_sitting_seconds:g} 秒，請照護者確認狀況')
            self.events.append(event)
            self.active[kind] = event
        if now >= event['confirm_at']:
            event['status'] = 'alert'
            if kind in ('fall', 'prolonged_sitting'):
                event.update(risk_level='high', escalated_at=now)

    def _request_tracking_review(self, now, risk_pose=None):
        """Record an immediate review when a risky motion ends in lost landmarks."""
        if ('tracking_review' not in self.active and 'fall' not in self.active and
                now >= self.suppressed.get('tracking_review', -1)):
            self.sequence += 1
            event = {'id': self.sequence, 'event_uid': uuid.uuid4().hex,
                     'kind': 'tracking_review', 'status': 'review',
                     'created_at': now, 'confirm_at': now,
                     'escalate_at': now + self.settings.fall_response_seconds,
                     'risk_level': 'needs_confirmation',
                     'voice_prompt': '偵測到可能跌倒。你還好嗎？請先不要急著站起來；若沒事，請按取消警報。',
                     'reason': '近期觀察到水平軀幹或快速下降，隨後關鍵點不足；需人工確認，未判定跌倒'}
            if risk_pose is not None:
                event.update(risk_shoulder_y=float(risk_pose[0]),
                             risk_hip_y=float(risk_pose[1]))
            self.events.append(event)
            self.active['tracking_review'] = event

    def _advance_fall_response(self, now, standing):
        events = [self.active[k] for k in ('fall','tracking_review') if k in self.active]
        waiting = [e for e in events if e['status'] in ('pending','review')]
        if not waiting:
            self.review_recovery_since = None
            return
        # Once a fall-like transition has been observed, keep the warning
        # latched for a caregiver to acknowledge. Standing again is useful
        # context, but must not make a real fall disappear from the live UI.
        if standing:
            for event in waiting:
                event.setdefault('standing_observed_at', now)
                event['standing_observed'] = True
        self.review_recovery_since = None
        for event in waiting:
            deadline = event.get('escalate_at', event['confirm_at'])
            if now >= deadline:
                event.update(status='alert', risk_level='high', escalated_at=now)

    def update(self, landmarks, shape, now, camera_moving=False, stream_available=True):
        s = self.settings
        if self.last_time is not None and (now <= self.last_time or now-self.last_time > s.max_gap):
            self._reset_tracking()
            for kind in list(self.active):
                self._event(kind, False, now)
        if camera_moving or not stream_available or (self.last_time is not None and now-self.last_time > s.max_gap):
            self.risk_history.clear()
            self.torso_motion.clear()
            self.descent_motion.clear()
            self.last_risk = None
        self.last_time = now
        required = [11, 12, 23, 24, 25, 26, 27, 28]
        def visible(ids):
            return (landmarks is not None and len(landmarks) > max(ids) and
                    all(math.isfinite(float(getattr(landmarks[i], a, float('nan'))))
                        for i in ids for a in ('x', 'y', 'visibility')) and
                    all(landmarks[i].visibility >= s.visibility and
                        0 <= landmarks[i].x <= 1 and 0 <= landmarks[i].y <= 1 for i in ids))
        torso_visible = visible([11,12,23,24])
        if torso_visible and not camera_moving and stream_available:
            h,w = shape[:2]
            sx,sy = (landmarks[11].x+landmarks[12].x)/2,(landmarks[11].y+landmarks[12].y)/2
            hx,hy = (landmarks[23].x+landmarks[24].x)/2,(landmarks[23].y+landmarks[24].y)/2
            while self.descent_motion and now-self.descent_motion[0][0] > 2.0:
                self.descent_motion.popleft()
            lower_frame_descent = (hy >= .82 and any(
                .6 <= now-old_t <= 2.0 and old_hy < .78 and
                sy-old_sy >= .05 and hy-old_hy >= .12
                for old_t,old_sy,old_hy in self.descent_motion))
            self.descent_motion.append((now,sy,hy))
            # A low-mounted camera commonly loses knees/ankles before it loses the
            # torso.  Preserve this as a review case instead of silently changing
            # from full-body to upper-body observation.
            if lower_frame_descent and not visible(required):
                self._request_tracking_review(now, (sy, hy))
                self.last_risk = None
            tilt = math.degrees(math.atan2(abs((sx-hx)*w),abs((sy-hy)*h)+1e-9))
            # A forward fall from a chair can rotate the torso out of view while
            # the hips barely descend. Require both a near-horizontal torso and
            # a substantial shoulder drop before treating the following loss as
            # a review case; ordinary bending remains below this combination.
            forward_rotation = (tilt >= 55 and any(
                .4 <= now-old_t <= 1.2 and sy-old_sy >= .14 and
                hy >= old_hy-.03
                for old_t,old_sy,old_hy in list(self.descent_motion)[:-1]))
            resting = any(x1<=hx<=x2 and y1<=hy<=y2 for x1,y1,x2,y2 in s.rest_zones)
            while self.torso_motion and now-self.torso_motion[0][0]>.8:
                self.torso_motion.popleft()
            rapid_descent = (len(self.torso_motion)>=3 and hy>.75 and
                any(.18<=sy-old_sy<=.4 and .18<=hy-old_hy<=.4
                    for _,old_sy,old_hy in self.torso_motion))
            self.torso_motion.append((now,sy,hy))
            if (rapid_descent or lower_frame_descent or forward_rotation) and not resting:
                self.last_risk=now
            if tilt >= 65 and not resting:
                self.risk_history.append(now)
                while self.risk_history and now-self.risk_history[0]>1.2:
                    self.risk_history.popleft()
                if len(self.risk_history)>=3 and now-self.risk_history[0]>=.25:
                    self.last_risk=now
            else:
                self.risk_history.clear()
                if not (rapid_descent or lower_frame_descent or forward_rotation):
                    self.last_risk=None
        elif (stream_available and not camera_moving and self.last_risk is not None
              and 0<=now-self.last_risk<=2):
            risk_pose = ((self.descent_motion[-1][1], self.descent_motion[-1][2])
                         if self.descent_motion else None)
            self._request_tracking_review(now, risk_pose)
            self.last_risk=None
            self.risk_history.clear()
        full = visible(required)
        upper = visible([11,12])
        mode = 'full' if full else ('upper' if upper else 'none')
        if camera_moving:
            mode = 'none'
        if mode != self.state['observation_mode']:
            self._reset_tracking()
            for kind in list(self.active):
                self._event(kind, False, now)
        self.state['observation_mode'] = mode
        if camera_moving or mode == 'none':
            self._reset_tracking()
            self.state.update(posture='stream_unavailable' if not stream_available else ('camera_moving' if camera_moving else 'no_person'),
                              tracking=False, angle=None, immobile_seconds=0, sitting_seconds=0, wandering_reversals=0,
                              angle_source='none', activity='unknown')
            for kind in list(self.active):
                self._event(kind, False, now)
            self._advance_fall_response(now, False)
            return self.snapshot(now)
        if mode == 'upper':
            # Observe visible upper-body motion only; never infer a fall from missing legs.
            ids = tuple(i for i in (0,11,12,13,14,15,16,23,24) if visible([i]))
            points = tuple((landmarks[i].x,landmarks[i].y) for i in ids)
            if getattr(self, '_upper_ids', None) != ids:
                self.anchor = None
            self._upper_ids = ids
            moved = (self.anchor is None or max(math.hypot(a[0]-b[0],a[1]-b[1])
                     for a,b in zip(points,self.anchor)) > 0.025)
            if moved:
                self.anchor, self.still_since = points, now
            h,w = shape[:2]
            shoulder = ((landmarks[11].x+landmarks[12].x)/2,
                        (landmarks[11].y+landmarks[12].y)/2)
            source, angle = 'none', None
            if visible([23,24]):
                target = ((landmarks[23].x+landmarks[24].x)/2,
                          (landmarks[23].y+landmarks[24].y)/2)
                source = 'torso'
            elif visible([0]):
                target = (landmarks[0].x,landmarks[0].y)
                source = 'head_shoulders'
            if source != 'none':
                dx,dy = (target[0]-shoulder[0])*w,(target[1]-shoulder[1])*h
                if math.hypot(dx,dy) >= 5:
                    angle = round(math.degrees(math.atan2(abs(dx),abs(dy)+1e-9)),1)
                else:
                    source = 'none'
            # With a low robot camera the legs may stay outside the image after a
            # fall.  A review can still be resolved when the visible torso is
            # upright and the hips have risen clearly above the recorded risk
            # position.  The sustained hold in _advance_fall_response prevents a
            # single noisy frame from cancelling the alert.
            upper_recovered = False
            if source == 'torso' and angle is not None and angle <= 25:
                waiting = [self.active[k] for k in ('fall', 'tracking_review')
                           if k in self.active and
                           self.active[k]['status'] in ('pending', 'review')]
                upper_recovered = any(
                    event.get('risk_hip_y') is not None and
                    target[1] <= float(event['risk_hip_y']) - .15
                    for event in waiting)
            upper_posture = 'upper_body'
            if source == 'torso' and angle is not None:
                upper_posture = ('upper_horizontal' if angle >= 65 else
                                 ('upper_bending' if angle > 30 else 'upper_upright'))
            rest_point = target if source == 'torso' else shoulder
            upper_in_rest = any(x1<=rest_point[0]<=x2 and y1<=rest_point[1]<=y2
                                for x1,y1,x2,y2 in s.rest_zones)
            if upper_posture == 'upper_horizontal' and upper_in_rest:
                upper_posture = 'upper_resting'
            upper_still = now-self.still_since
            self._event('immobile',upper_still >= s.immobile_seconds and
                        not upper_in_rest,now)
            self._event('prolonged_sitting',False,now)
            self._event('wandering',False,now)
            self.state.update(posture=upper_posture,tracking=True,angle=angle,
                              angle_source=source,activity='moving' if moved else 'still',
                              immobile_seconds=round(upper_still,1),sitting_seconds=0,
                              wandering_reversals=0)
            self._advance_fall_response(now, upper_recovered)
            return self.snapshot(now)
        h, w = shape[:2]
        def midpoint(a, b):
            return ((landmarks[a].x+landmarks[b].x)/2, (landmarks[a].y+landmarks[b].y)/2)
        shoulder, hip = midpoint(11, 12), midpoint(23, 24)
        knee, ankle = midpoint(25, 26), midpoint(27, 28)
        angle = math.degrees(math.atan2(abs((shoulder[0]-hip[0])*w),
                                       abs((shoulder[1]-hip[1])*h) + 1e-9))
        # Pixel aspect ratio matters. Both upright directions yield zero tilt.
        horizontal = angle >= 65
        seated = (not horizontal and abs((hip[1]-knee[1])*h) <
                  max(abs((knee[1]-ankle[1])*h)*0.55, h*0.025))
        posture = 'lying_unverified' if horizontal else ('sitting' if seated else
                  ('bending' if angle > 30 else 'standing'))
        if posture == 'sitting':
            if self.sitting_since is None:
                self.sitting_since = now
        else:
            self.sitting_since = None
        sitting_seconds = 0 if self.sitting_since is None else now-self.sitting_since
        recent = [p for p in self.history if now-p[0] <= 1.2]
        rapid_drop = any(hip[1]-p[2] >= 0.12 and p[3] < 35 and angle-p[3] >= 35 for p in recent)
        # Large identity/camera jumps must not accumulate another person's stillness.
        if self.history and math.hypot(hip[0]-self.history[-1][1], hip[1]-self.history[-1][2]) > 0.35:
            self._reset_tracking()
            rapid_drop = False
        in_rest = any(x1 <= hip[0] <= x2 and y1 <= hip[1] <= y2
                      for x1,y1,x2,y2 in s.rest_zones)
        if horizontal and rapid_drop:
            self.fall_transition = True
        if not horizontal:
            self.fall_transition = False
        candidate = horizontal and (self.fall_transition or (hip[1] > 0.72 and not in_rest))
        if in_rest and not self.fall_transition and horizontal:
            posture = 'resting'
        if candidate:
            if self.fall_since is None:
                self.fall_since = now
            posture = 'suspected_fall'
        else:
            self.fall_since = None
        points = tuple((landmarks[i].x, landmarks[i].y) for i in required)
        if self.anchor is None or max(math.hypot(a[0]-b[0], a[1]-b[1])
                                      for a,b in zip(points,self.anchor)) > 0.025:
            self.anchor, self.still_since = points, now
        still = now - self.still_since
        self.history.append((now, hip[0], hip[1], angle))
        while self.history and now-self.history[0][0] > s.wandering_seconds:
            self.history.popleft()
        turns, direction = 0, 0
        pivot = self.history[0][1]
        for _,x,_,_ in self.history:
            if abs(x-pivot) >= 0.08:
                new_direction = 1 if x > pivot else -1
                if direction and new_direction != direction:
                    turns += 1
                direction, pivot = new_direction, x
        wandering = (posture == 'standing' and turns >= s.wandering_reversals and
                     now-self.history[0][0] >= s.wandering_seconds*0.8)
        self._event('fall', self.fall_since is not None and now-self.fall_since >= s.fall_hold, now)
        fall_event = self.active.get('fall')
        if fall_event is not None:
            fall_event.setdefault('risk_shoulder_y', float(shoulder[1]))
            fall_event.setdefault('risk_hip_y', float(hip[1]))
        self._event('immobile', still >= s.immobile_seconds and not in_rest, now)
        self._event('prolonged_sitting',sitting_seconds >= s.prolonged_sitting_seconds,now)
        self._event('wandering', wandering, now)
        self.state.update(posture=posture, tracking=True, angle=round(angle,1),
                          angle_source='torso', activity='still' if still > 0 else 'moving',
                          immobile_seconds=round(still,1),sitting_seconds=round(sitting_seconds,1),
                          wandering_reversals=turns)
        self._advance_fall_response(now, posture == 'standing')
        return self.snapshot(now)

    def snapshot(self, now):
        return {**self.state, 'events': [dict(e, remaining_seconds=round(max(0,e.get('escalate_at',e['confirm_at'])-now),1))
                                       for e in self.events],
                'active_events': [dict(e) for e in self.active.values()],
                'settings': asdict(self.settings)}
