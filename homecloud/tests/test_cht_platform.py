import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cht_platform_emulator import create_app
from mec_edge_client import normalize_event
from mec_event_sink import ChtIotEventSink,ChtPlatformSink,ChtTasNotifier
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cht_tas_register import registration_payload


class Recorder:
    def __init__(self,result=None):self.calls=[];self.result=result or {'ok':True}
    def __call__(self,url,payload,headers,timeout):
        self.calls.append((url,payload,headers,timeout));return self.result


class FlakyNotifier:
    def __init__(self):self.calls=0
    def notify_event(self,event):
        self.calls+=1
        if self.calls==1:raise RuntimeError('temporary TAS outage')
        return {'status':'ok','group':'g1'}


class CountingSink:
    def __init__(self):self.calls=0
    def upload_event(self,event):self.calls+=1;return {'ok':True}


class ChtPlatformTests(unittest.TestCase):
    def test_tas_registration_payload(self):
        self.assertEqual(registration_payload('SilverGuardianDog','長照通告'),
                         {'appName':'SilverGuardianDog','appDesc':'長照通告'})
        with self.assertRaises(ValueError):registration_payload(' ','description')

    def test_iot_sink_uses_api_key_and_idempotency_key(self):
        transport=Recorder()
        sink=ChtIotEventSink('https://tenant.example/events','secret',transport=transport)
        sink.upload_event({'event_id':'event-1'})
        self.assertEqual(transport.calls[0][2]['X-API-Key'],'secret')
        self.assertEqual(transport.calls[0][2]['Idempotency-Key'],'event-1')
        with self.assertRaises(ValueError):
            ChtIotEventSink('http://public.example/events','secret')

    def test_tas_payload_matches_official_contract(self):
        transport=Recorder({'status':'ok','group':'g1'})
        notifier=ChtTasNotifier('secret','0281920000',['0912345678'],transport=transport)
        notifier.notify_event({'type':'fall','severity':'high'})
        payload=transport.calls[0][1]
        self.assertEqual(payload['serviceNumber'],'0281920000')
        self.assertEqual(payload['phones'],['0912345678'])
        self.assertIn('疑似跌倒',payload['ivrData']['text'])
        self.assertFalse(notifier.accepts({'type':'path_obstacle','severity':'high'}))
        self.assertTrue(notifier.accepts({'type':'tracking_review','severity':'high'}))
        with self.assertRaises(ValueError):
            ChtTasNotifier('secret','bad',['0912345678'])

    def test_tas_skips_non_fall_high_risk_event(self):
        with tempfile.TemporaryDirectory() as directory:
            transport=Recorder({'status':'ok','group':'g1'})
            notifier=ChtTasNotifier('secret','0281920000',['0912345678'],
                                    transport=transport)
            sink=ChtPlatformSink(Path(directory)/'ledger.sqlite3',notifier=notifier)
            result=sink.upload_event({'event_id':'path-1:alert','type':'path_obstacle',
                                      'severity':'high'})
            self.assertFalse(result['notification_sent']);self.assertFalse(transport.calls)
            sink.close()

    def test_channel_ledger_retries_only_failed_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            event_sink=CountingSink();notifier=FlakyNotifier()
            sink=ChtPlatformSink(Path(directory)/'ledger.sqlite3',event_sink,notifier)
            event={'event_id':'fall-1:alert','severity':'high'}
            with self.assertRaises(RuntimeError):sink.upload_event(event)
            self.assertEqual(event_sink.calls,1)
            sink.upload_event(event);sink.upload_event(event)
            self.assertEqual(event_sink.calls,1);self.assertEqual(notifier.calls,2)
            self.assertEqual(sink.stats()['notifications'],1);sink.close()

    def test_review_and_alert_are_distinct_revisions(self):
        base={'event_id':'ranger-fall-1','kind':'fall'}
        review=normalize_event({**base,'status':'review'},'ranger','local')
        alert=normalize_event({**base,'status':'alert'},'ranger','local')
        self.assertNotEqual(review['event_id'],alert['event_id'])
        self.assertEqual(review['incident_id'],alert['incident_id'])
        self.assertEqual(alert['severity'],'high')

    def test_local_contract_emulator_auth_dedupe_and_callout(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app('test-key',Path(directory)/'platform.sqlite3')
            client=app.test_client();headers={'X-API-Key':'test-key'}
            event={'event_id':'fall-1:alert','device_id':'ranger','type':'fall'}
            self.assertEqual(client.post('/v1/iot/events',json=event).status_code,401)
            self.assertFalse(client.post('/v1/iot/events',json=event,headers=headers).json['duplicate'])
            self.assertTrue(client.post('/v1/iot/events',json=event,headers=headers).json['duplicate'])
            call={'serviceNumber':'0281920000','phones':['0912345678'],
                  'ivrData':{'text':'跌倒警報'}}
            self.assertEqual(client.post('/apis/CHTIoT/phone-conn/v1/callout',
                                         json=call,headers=headers).json['status'],'ok')
            records=client.get('/v1/records',headers=headers).json
            self.assertEqual(len(records['events']),1);self.assertEqual(len(records['callouts']),1)
            app.extensions['cht_platform_store'].close()


if __name__=='__main__':unittest.main()
