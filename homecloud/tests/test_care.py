import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as Point
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'eldertest'))
from care_engine import CareEngine, Settings
from environment_engine import EnvironmentEngine, EnvironmentSettings


def skeleton(kind='standing', x=.5, y=.5):
    p=[Point(x=x,y=y,visibility=1.) for _ in range(33)]
    coords={'standing':((x,y-.22),(x,y+.18),(x,y+.35)),
            'sitting':((x,y-.22),(x+.18,y+.02),(x+.18,y+.25)),
            'bending':((x-.18,y-.18),(x,y+.18),(x,y+.35)),
            'lying':((x-.22,y),(x+.15,y),(x+.3,y))}
    sh,kn,an=coords[kind]
    for ids,pt in [((11,12),sh),((23,24),(x,y)),((25,26),kn),((27,28),an)]:
        for i in ids:p[i]=Point(x=pt[0],y=pt[1],visibility=1.)
    return p


class TemporalTests(unittest.TestCase):
    def test_zero_cancel_cooldown_allows_immediate_new_alert(self):
        engine=CareEngine(Settings(cancel_seconds=0))
        engine._event('prolonged_sitting',True,1)
        self.assertTrue(engine.cancel(1,1.1))
        engine._event('prolonged_sitting',True,1.1)
        self.assertIn('prolonged_sitting',engine.active)

    def setUp(self):
        self.e=CareEngine(Settings(fall_hold=.5,confirm_seconds=.5,immobile_seconds=2,
                                  wandering_seconds=3,cancel_seconds=2,
                                  fall_response_seconds=.5,recovery_hold_seconds=.2))

    def feed(self,points,start,end):
        for tick in range(round(start*10),round(end*10)+1):
            result=self.e.update(points,(480,640),tick/10)
        return result

    def test_upright_and_pixel_angle(self):
        self.assertEqual(self.feed(skeleton(),0,1)['angle'],0)
        self.assertFalse(self.e.active)

    def test_sitting_bending_are_not_falls(self):
        for kind in ['sitting','bending']:
            self.e=CareEngine()
            result=self.feed(skeleton(kind),0,2)
            self.assertEqual(result['posture'],kind)
            self.assertNotIn('fall',self.e.active)

    def test_prolonged_sitting_alerts_after_ten_seconds(self):
        self.e=CareEngine(Settings(prolonged_sitting_seconds=10,immobile_seconds=60))
        result=self.feed(skeleton('sitting'),0,10.2)
        event=self.e.active['prolonged_sitting']
        self.assertEqual(event['status'],'alert')
        self.assertEqual(event['risk_level'],'high')
        self.assertGreaterEqual(result['sitting_seconds'],10)

    def test_persistent_low_horizontal_alert(self):
        self.feed(skeleton('lying',y=.8),0,1.5)
        self.assertEqual(self.e.active['fall']['status'],'alert')

    def test_transient_does_not_trigger(self):
        self.feed(skeleton('lying',y=.8),0,.3)
        self.feed(skeleton(),.4,1)
        self.assertNotIn('fall',self.e.active)

    def test_pending_fall_stays_latched_after_recovery_pose(self):
        self.feed(skeleton('lying',y=.8),0,.6)
        self.assertEqual(self.e.active['fall']['status'],'pending')
        self.feed(skeleton(),.7,.9)
        self.assertIn('fall',self.e.active)
        self.assertTrue(self.e.active['fall']['standing_observed'])

    def test_cancel_suppresses_repeated_condition(self):
        self.feed(skeleton('lying',y=.8),0,.6)
        event=self.e.active['fall']['id']
        self.assertTrue(self.e.cancel(event,.6))
        self.feed(skeleton('lying',y=.8),.7,2.5)
        self.assertNotIn('fall',self.e.active)
        self.assertFalse(self.e.cancel(event,2.5))
        self.feed(skeleton('lying',y=.8),2.6,3.3)
        self.assertEqual(self.e.active['fall']['status'],'alert')

    def test_rest_zone_and_rapid_transition(self):
        self.e.settings.rest_zones=((0,.4,1,1),)
        self.assertEqual(self.feed(skeleton('lying',y=.8),0,1.5)['posture'],'resting')
        self.assertNotIn('fall',self.e.active)
        self.feed(skeleton(y=.5),1.6,2)
        self.feed(skeleton('lying',y=.7),2.1,3.3)
        self.assertEqual(self.e.active['fall']['status'],'alert')

    def test_no_person_resets_stillness(self):
        self.feed(skeleton(),0,1.5)
        self.e.update(None,(480,640),1.6)
        self.feed(skeleton(),1.7,3)
        self.assertNotIn('immobile',self.e.active)

    def test_low_confidence_does_not_accumulate(self):
        self.e=CareEngine(Settings(immobile_seconds=60))
        p=skeleton('lying',y=.8);p[27].visibility=.1
        self.assertEqual(self.feed(p,0,4)['observation_mode'],'upper')
        self.assertFalse(self.e.active)

    def test_gap_does_not_silently_clear_pending_fall(self):
        self.feed(skeleton('lying',y=.8),0,.6)
        self.e.update(skeleton('lying',y=.8),(480,640),5)
        self.assertEqual(self.e.active['fall']['status'],'alert')

    def test_alert_is_not_erased_by_occlusion(self):
        self.feed(skeleton('lying',y=.8),0,1.5)
        self.e.update(None,(480,640),1.6)
        self.assertEqual(self.e.active['fall']['status'],'alert')

    def test_immobility_and_slow_drift(self):
        self.feed(skeleton(),0,2.6)
        self.assertEqual(self.e.active['immobile']['status'],'alert')
        self.e=CareEngine(self.e.settings)
        for i in range(40):self.e.update(skeleton(x=.3+i*.003),(480,640),i*.1)
        self.assertNotIn('immobile',self.e.active)

    def test_camera_motion_resets(self):
        self.feed(skeleton(),0,1.5)
        r=self.e.update(skeleton(),(480,640),1.6,camera_moving=True)
        self.assertEqual(r['posture'],'camera_moving')
        self.assertFalse(self.e.history)

    def test_repeated_lateral_motion(self):
        for i in range(40):
            x=.4+(i%10 if i%20<10 else 10-i%10)*.025
            self.e.update(skeleton(x=x),(480,640),i*.1)
        # Configure a shorter demonstration interval with two observable reversals.
        self.e.settings.wandering_reversals=2
        for i in range(40,60):
            x=.4+(i%10 if i%20<10 else 10-i%10)*.025
            self.e.update(skeleton(x=x),(480,640),i*.1)
        self.assertTrue(any(e['kind']=='wandering' for e in self.e.events))

    def test_upper_without_hips_observes_without_alerting(self):
        self.e=CareEngine(Settings(immobile_seconds=60))
        p=skeleton()
        for i in range(23,29):p[i].visibility=0
        result=self.feed(p,0,5)
        self.assertTrue(result['tracking'])
        self.assertEqual(result['observation_mode'],'upper')
        self.assertEqual(result['angle_source'],'head_shoulders')
        self.assertGreater(result['immobile_seconds'],2)
        self.assertFalse(self.e.active)

    def test_offscreen_legs_are_not_full_body(self):
        p=skeleton()
        p[27].y=1.1
        self.assertEqual(self.feed(p,0,.5)['observation_mode'],'upper')

    def test_upper_to_full_resets_stillness(self):
        self.e=CareEngine(Settings(immobile_seconds=60))
        p=skeleton();p[27].visibility=0
        self.feed(p,0,3)
        result=self.feed(skeleton(),3.1,3.5)
        self.assertLess(result['immobile_seconds'],1)
        self.assertNotIn('immobile',self.e.active)

    def test_partial_visibility_does_not_clear_pending_fall(self):
        p=skeleton('lying',y=.8)
        self.feed(p,0,.6)
        self.assertEqual(self.e.active['fall']['status'],'pending')
        p[27].visibility=0
        self.feed(p,.7,1.5)
        self.assertEqual(self.e.active['fall']['status'],'alert')

    def test_upper_arm_movement_resets_observation_timer(self):
        p=skeleton();p[27].visibility=0
        self.feed(p,0,1)
        p[15].x+=.1
        result=self.feed(p,1.1,1.1)
        self.assertEqual(result['activity'],'moving')
        self.assertEqual(result['immobile_seconds'],0)

    def test_upper_torso_posture_and_immobility(self):
        self.e=CareEngine(Settings(immobile_seconds=.5,confirm_seconds=.2))
        p=skeleton('bending')
        for i in range(25,29):p[i].visibility=0
        result=self.feed(p,0,1)
        self.assertEqual(result['observation_mode'],'upper')
        self.assertEqual(result['posture'],'upper_bending')
        self.assertEqual(self.e.active['immobile']['status'],'alert')

    def test_upper_body_rest_zone_suppresses_immobility(self):
        self.e=CareEngine(Settings(immobile_seconds=.5,confirm_seconds=.2,
                                  rest_zones=((0,.3,1,.9),)))
        p=skeleton('lying',y=.6)
        for i in range(25,29):p[i].visibility=0
        result=self.feed(p,0,1)
        self.assertEqual(result['posture'],'upper_resting')
        self.assertNotIn('immobile',self.e.active)

    def test_missing_shoulders_disables_observation(self):
        p=skeleton();p[11].visibility=0
        self.assertFalse(self.feed(p,0,1)['tracking'])

    def test_horizontal_then_occluded_creates_review_not_fall_alert(self):
        self.feed(skeleton('lying',y=.6),0,.5)
        self.e.update(None,(480,640),.6)
        self.assertEqual(self.e.active['tracking_review']['status'],'review')
        self.assertNotIn('fall',self.e.active)

    def test_normal_exit_does_not_create_review(self):
        self.feed(skeleton(),0,.5)
        self.e.update(None,(480,640),.6)
        self.assertNotIn('tracking_review',self.e.active)

    def test_camera_outage_does_not_create_review(self):
        self.feed(skeleton('lying',y=.6),0,.5)
        r=self.e.update(None,(480,640),.6,stream_available=False)
        self.assertEqual(r['posture'],'stream_unavailable')
        self.assertNotIn('tracking_review',self.e.active)

    def test_review_cancel_and_rest_zone(self):
        self.feed(skeleton('lying',y=.6),0,.5)
        self.e.update(None,(480,640),.6)
        self.assertTrue(self.e.cancel(self.e.active['tracking_review']['id'],.7))
        self.feed(skeleton('lying',y=.6),.8,1.3)
        self.e.update(None,(480,640),1.4)
        self.assertNotIn('tracking_review',self.e.active)
        self.e=CareEngine(Settings(rest_zones=((0,0,1,1),)))
        self.feed(skeleton('lying',y=.6),0,.5)
        self.e.update(None,(480,640),.6)
        self.assertFalse(self.e.active)

    def test_tracking_review_escalates_without_response(self):
        self.e._request_tracking_review(0)
        result=self.e.update(None,(480,640),.6)
        event=result['active_events'][0]
        self.assertEqual(event['status'],'alert')
        self.assertEqual(event['risk_level'],'high')
        self.assertIn('你還好嗎',event['voice_prompt'])

    def test_tracking_review_stays_latched_after_standing(self):
        self.e._request_tracking_review(0)
        for now in (.1,.2,.4):
            self.e.update(skeleton('standing'),(480,640),now)
        self.assertIn('tracking_review',self.e.active)
        self.assertTrue(self.e.events[-1]['standing_observed'])

    def test_low_camera_upper_body_rise_keeps_review_latched(self):
        self.e._request_tracking_review(0, risk_pose=(.72, .86))
        raised=skeleton('standing',y=.60)
        for i in range(25,29):
            raised[i].visibility=0
        for now in (.1,.2,.4):
            result=self.e.update(raised,(480,640),now)
        self.assertEqual(result['observation_mode'],'upper')
        self.assertIn('tracking_review',self.e.active)
        self.assertTrue(self.e.events[-1]['standing_observed'])

    def test_upper_body_that_has_not_risen_keeps_review(self):
        self.e._request_tracking_review(0, risk_pose=(.72, .86))
        low=skeleton('standing',y=.80)
        for i in range(25,29):
            low[i].visibility=0
        for now in (.1,.2,.4):
            self.e.update(low,(480,640),now)
        self.assertIn('tracking_review',self.e.active)

    def test_descent_then_loss_requests_review(self):
        for i in range(8):
            self.e.update(skeleton(y=.55+i*.04),(480,640),i*.1)
        self.e.update(None,(480,640),.8)
        self.assertEqual(self.e.active['tracking_review']['status'],'review')

    def test_slow_descent_then_loss_does_not_request_review(self):
        for i in range(30):
            self.e.update(skeleton(y=.55+i*.008),(480,640),i*.1)
        self.e.update(None,(480,640),3)
        self.assertNotIn('tracking_review',self.e.active)

    def test_seated_lower_frame_descent_then_leg_loss_requests_review(self):
        # Low camera: torso sinks toward the bottom before legs leave the frame.
        for i in range(10):
            self.e.update(skeleton('sitting',y=.58+i*.025),(480,640),i*.2)
        partial=skeleton('sitting',y=.82)
        for i in range(25,29):partial[i].visibility=.1
        result=self.e.update(partial,(480,640),2.0)
        self.assertEqual(result['observation_mode'],'upper')
        self.assertIn('tracking_review',self.e.active)
        self.assertEqual(self.e.active['tracking_review']['status'],'review')

    def test_forward_fall_from_chair_then_loss_requests_review(self):
        seated=skeleton('sitting',y=.65)
        for now in (0,.2,.4,.6):
            self.e.update(seated,(480,640),now)
        tipping=skeleton('sitting',y=.68)
        for i in (11,12):
            tipping[i].x=.32;tipping[i].y=.62
        self.e.update(tipping,(480,640),.8)
        self.e.update(None,(480,640),1.0)
        self.assertIn('tracking_review',self.e.active)

    def test_small_bend_then_loss_does_not_request_review(self):
        upright=skeleton('sitting',y=.65)
        for now in (0,.2,.4,.6):
            self.e.update(upright,(480,640),now)
        bending=skeleton('sitting',y=.67)
        for i in (11,12):
            bending[i].x=.40;bending[i].y=.55
        self.e.update(bending,(480,640),.8)
        self.e.update(None,(480,640),1.0)
        self.assertNotIn('tracking_review',self.e.active)

    def test_recorded_pose_regression(self):
        import json
        from types import SimpleNamespace
        path=Path(__file__).with_name('pose_fixture_cache.json')
        recordings=json.loads(path.read_text())
        for name,rows in recordings.items():
            e=CareEngine()
            for r in rows:
                e.update([SimpleNamespace(**p) for p in r['points']] if r['points'] else None,r['shape'],r['t'])
            if '_adl_' in name:
                self.assertFalse(e.events,name)
            else:
                self.assertTrue(any(x['kind']=='tracking_review' for x in e.events),name)

    def test_invalid_settings(self):
        with self.assertRaises(ValueError):Settings(confirm_seconds=-1)
        with self.assertRaises(ValueError):Settings(rest_zones=((0,0,2,1),))


class WebTests(unittest.TestCase):
    def test_edge_status_reports_missing_live_and_stale(self):
        import json
        import tempfile
        import time
        from flask import Flask
        from care_service import CareService
        from care_web import create_blueprint
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'edge.json'
            service=CareService();app=Flask(__name__)
            app.register_blueprint(create_blueprint(service,path))
            client=app.test_client()
            self.assertEqual(client.get('/care/edge-status').json['mode'],'not_started')
            path.write_text(json.dumps({'updated_at':time.time(),'mode':'mec',
                                        'network_label':'本機 Wi-Fi',
                                        'deployment_label':'本機 MEC 模擬',
                                        'outbox':{'pending':0}}),encoding='utf-8')
            self.assertEqual(client.get('/care/edge-status').json['mode'],'mec')
            path.write_text(json.dumps({'updated_at':time.time()-20,'mode':'mec'}),encoding='utf-8')
            self.assertEqual(client.get('/care/edge-status').json['mode'],'agent_stopped')

    def test_page_status_cancel_without_hardware(self):
        from flask import Flask
        from care_service import CareService
        from care_web import create_blueprint
        service=CareService()
        app=Flask(__name__);app.register_blueprint(create_blueprint(service))
        client=app.test_client()
        page=client.get('/care')
        self.assertEqual(page.status_code,200)
        self.assertIn('跌倒後安全小貼士',page.get_data(as_text=True))
        self.assertIn('隱私骨架模式',page.get_data(as_text=True))
        self.assertIn('正常影像模式',page.get_data(as_text=True))
        self.assertIn("event.status==='cancelled'",page.get_data(as_text=True))
        self.assertTrue(client.get('/care/status').json['stream_stale'])
        with service.lock:service.engine._event('fall',True,0)
        self.assertEqual(client.post('/care/events/1/cancel',json={}).status_code,200)
        self.assertEqual(client.post('/care/events/1/cancel',json={}).status_code,404)
        self.assertEqual(client.post('/care/events/1/cancel').status_code,415)

    def test_fall_response_api_cancels_or_escalates_latest_event(self):
        from flask import Flask
        from care_service import CareService
        from care_web import create_blueprint
        service=CareService(Settings(fall_response_seconds=20))
        app=Flask(__name__);app.register_blueprint(create_blueprint(service))
        client=app.test_client()
        self.assertEqual(client.post('/care/fall-response',json={'response':'ok'}).status_code,404)
        service.engine._request_tracking_review(0)
        self.assertEqual(client.post('/care/fall-response',json={'response':'help'}).status_code,200)
        self.assertEqual(service.engine.active['tracking_review']['status'],'alert')
        self.assertEqual(service.engine.active['tracking_review']['escalation_reason'],
                         '長者語音或介面回覆需要協助')
        self.assertEqual(client.post('/care/fall-response',json={'response':'ok'}).status_code,200)
        self.assertFalse(service.engine.active)
        self.assertEqual(client.post('/care/fall-response',json={'response':'bad'}).status_code,400)

    def test_environment_baseline_requires_frame(self):
        from flask import Flask
        from care_service import CareService
        from care_web import create_blueprint
        service=CareService();app=Flask(__name__);app.register_blueprint(create_blueprint(service))
        self.assertEqual(app.test_client().post('/care/environment/baseline',json={}).status_code,409)

    def test_rest_zone_is_validated_saved_and_cleared(self):
        import json
        import tempfile
        from dataclasses import asdict
        from flask import Flask
        from care_service import CareService
        from care_web import create_blueprint
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json'
            path.write_text(json.dumps(asdict(Settings())),encoding='utf-8')
            service=CareService(Settings(),path)
            app=Flask(__name__);app.register_blueprint(create_blueprint(service))
            client=app.test_client()
            self.assertEqual(client.post('/care/rest-zone',json={
                'x1':.1,'y1':.2,'x2':.8,'y2':.9}).status_code,200)
            self.assertEqual(service.engine.settings.rest_zones,((.1,.2,.8,.9),))
            self.assertEqual(json.loads(path.read_text())['rest_zones'],[[.1,.2,.8,.9]])
            self.assertEqual(client.post('/care/rest-zone',json={
                'x1':.8,'y1':.2,'x2':.1,'y2':.9}).status_code,400)
            self.assertEqual(client.post('/care/rest-zones/clear',json={}).status_code,200)
            self.assertEqual(service.engine.settings.rest_zones,())


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np=np
        self.settings=EnvironmentSettings(confirm_seconds=.3,minimum_area_ratio=.003)
        self.engine=EnvironmentEngine(self.settings)
        self.clear=np.full((240,320,3),100,dtype=np.uint8)
        self.engine.set_baseline(self.clear)

    def test_tall_floor_change_is_obstacle(self):
        frame=self.clear.copy();frame[130:225,130:190]=(20,20,180)
        self.engine.update(frame,0);result=self.engine.update(frame,.4)
        self.assertEqual(result['path_status'],'obstacle')
        self.assertEqual(self.engine.active['path_obstacle']['status'],'alert')
        self.assertFalse(any(r['kind']=='possible_water' for r in result['regions']))

    def test_wide_low_saturation_change_is_possible_water(self):
        frame=self.clear.copy();frame[190:220,55:265]=(205,205,205)
        self.engine.update(frame,0);result=self.engine.update(frame,.4)
        self.assertEqual(result['water_status'],'possible_water')
        self.assertEqual(self.engine.active['possible_water']['status'],'alert')
        self.assertTrue(result['water_is_visual_screening_only'])
        self.assertTrue(result['gas_requires_physical_sensor'])

    def test_lighting_change_pauses_and_camera_motion_clears_pending(self):
        shifted=self.np.full_like(self.clear,180)
        result=self.engine.update(shifted,0)
        self.assertEqual(result['status'],'lighting_changed')
        frame=self.clear.copy();frame[130:225,130:190]=(20,20,180)
        self.engine.update(frame,1)
        result=self.engine.update(frame,1.1,camera_moving=True)
        self.assertEqual(result['path_status'],'paused')
        self.assertNotIn('path_obstacle',self.engine.active)

    def test_ignore_mask_excludes_person_region(self):
        frame=self.clear.copy();frame[130:225,130:190]=(20,20,180)
        mask=self.np.zeros(self.clear.shape[:2],dtype=self.np.uint8)
        mask[120:230,120:200]=1
        result=self.engine.update(frame,0,ignore_mask=mask)
        self.assertEqual(result['path_status'],'clear')
        self.assertFalse(result['regions'])

    def test_gas_requires_hardware_sample(self):
        self.assertEqual(self.engine.snapshot(0)['gas_status'],'sensor_not_connected')
        result=self.engine.update_gas(False,True,0,value=0,unit='digital_alarm')
        self.assertEqual(result['gas_status'],'normal')
        self.engine.update_gas(True,True,.1,value=1,unit='digital_alarm')
        result=self.engine.update_gas(True,True,.5,value=1,unit='digital_alarm')
        self.assertEqual(result['gas_status'],'alarm')
        self.assertEqual(self.engine.active['gas_alarm']['status'],'alert')


if __name__=='__main__':unittest.main()
