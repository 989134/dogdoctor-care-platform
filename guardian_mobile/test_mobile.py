import base64
import json
import unittest
from unittest.mock import patch,Mock
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from test_workflow import WorkflowTests
from guardian.push import valid_subscription


class MobileTests(WorkflowTests):
    def setUp(self):
        super().setUp()
        self.client.post('/api/users',headers=self.csrf,json={'name':'second','password':'long-second-password','role':'caregiver'})
        self.cfg=dict(primary_user='admin',secondary_user='second',primary_phone='0912345678',secondary_phone='0987654321',enabled=True)
        self.assertEqual(self.client.post('/api/notification/config',headers=self.csrf,json=self.cfg).status_code,200)
        self.app.config['PUBLIC_URL']='https://care.example.com'

    def pending(self):
        self.detect();self.result('robot','started');self.result('robot','no_response')
        self.app.extensions['notification_plan']()
        return [dict(r) for r in self.app.extensions['guardian_db'].execute('SELECT * FROM notification_deliveries ORDER BY channel')]

    def subscription(self):
        key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
        enc=lambda value:base64.urlsafe_b64encode(value).decode().rstrip('=')
        return dict(endpoint='https://fcm.googleapis.com/fcm/send/test-device',keys=dict(p256dh=enc(key),auth=enc(b'0123456789abcdef')))

    def test_config_normalizes_numbers_and_rejects_same_person(self):
        cfg=self.client.get('/api/notification/config').json
        self.assertEqual(cfg['primary_phone'],'+886912345678')
        self.assertNotIn('primary',cfg)
        for changes in ({'secondary_user':'admin'},{'secondary_phone':'0912345678'},{'primary_phone':'119'},{'primary_user':'missing'}):
            self.assertEqual(self.client.post('/api/notification/config',headers=self.csrf,json=self.cfg|changes).status_code,400)

    def test_both_channels_are_durable_and_first_delivery_starts_clock_once(self):
        items=self.pending();self.assertEqual({x['channel'] for x in items},{'push','sms'})
        self.app.extensions['notification_delivered'](items[0]['id'],'測試送達')
        w=self.current();self.assertEqual(w['stage'],'awaiting_caregiver')
        self.app.extensions['notification_plan']()
        self.app.extensions['notification_delivered'](items[1]['id'],'另一管道送達')
        self.assertEqual(self.current()['deadline'],w['deadline'])
        self.assertEqual(len(self.current()['commands'][-1]['deliveries']),2)

    def test_queued_sms_does_not_start_timer_and_poll_does_not_resend(self):
        self.pending();sms=self.app.extensions['sms_transport'];sms.ready=True
        with patch.object(sms,'request',return_value=('SM'+'1'*32,'queued')) as send:
            self.app.extensions['sms_tick']();self.assertEqual(send.call_count,1)
        self.assertIsNone(self.current()['deadline'])
        db=self.app.extensions['guardian_db'];db.execute("UPDATE notification_deliveries SET updated=0 WHERE channel='sms'");db.commit()
        with patch.object(sms,'request',return_value=('SM'+'1'*32,'delivered')) as poll:
            self.app.extensions['sms_tick']();self.assertEqual(poll.call_args.kwargs,{'message_id':'SM'+'1'*32})
        self.assertEqual(self.current()['stage'],'awaiting_caregiver')

    def test_ambiguous_sms_is_not_automatically_resent(self):
        self.pending();sms=self.app.extensions['sms_transport'];sms.ready=True
        with patch.object(sms,'request',side_effect=OSError('timeout')) as send:
            self.app.extensions['sms_tick']();self.app.extensions['sms_tick']();self.assertEqual(send.call_count,1)
        self.assertIsNone(self.current()['deadline'])
        self.assertEqual(self.app.extensions['guardian_db'].execute("SELECT status FROM notification_deliveries WHERE channel='sms'").fetchone()[0],'unknown')

    def test_push_receipt_requires_signature_and_correct_active_device(self):
        sub=self.subscription();self.assertEqual(self.client.post('/api/push/subscribe',headers=self.csrf,json=sub).status_code,200)
        self.pending()
        with patch('guardian.push.webpush',return_value=Mock(status_code=201)) as send:
            self.app.extensions['notification_tick']()
            receipt=json.loads(send.call_args.kwargs['data'])['receipt']
        self.assertIsNone(self.current()['deadline'])
        self.assertEqual(self.client.post('/api/push/received',json={'receipt':'forged'}).status_code,403)
        self.assertEqual(self.client.post('/api/push/received',json={'receipt':receipt}).status_code,200)
        deadline=self.current()['deadline']
        self.client.post('/api/push/received',json={'receipt':receipt})
        self.assertEqual(self.current()['deadline'],deadline)
        self.client.post('/api/push/unsubscribe',headers=self.csrf,json={'endpoint':sub['endpoint']})
        self.assertEqual(self.client.post('/api/push/received',json={'receipt':receipt}).status_code,403)

    def test_endpoint_validation_and_logout_revokes_subscription(self):
        sub=self.subscription();self.assertTrue(valid_subscription(sub))
        for endpoint in ('http://fcm.googleapis.com/x','https://127.0.0.1/x','https://evil.example/x','https://fcm.googleapis.com.evil.example/x'):
            self.assertFalse(valid_subscription(sub|{'endpoint':endpoint}))
        self.client.post('/api/push/subscribe',headers=self.csrf,json=sub)
        self.client.post('/api/logout',headers=self.csrf,json={})
        self.assertEqual(self.app.extensions['guardian_db'].execute('SELECT count(*) FROM push_subscriptions').fetchone()[0],0)

    def test_wrong_account_does_not_start_receipt_timer(self):
        self.pending()
        self.client.post('/api/notification/config',headers=self.csrf,json=self.cfg|{'primary_user':'second','secondary_user':'admin'})
        self.assertFalse(self.current()['can_receive_in_app'])
        self.assertEqual(self.action('notification_received').status_code,403)
        self.assertIsNone(self.current()['deadline'])

    def test_missing_service_remains_blocked_without_false_delivery(self):
        self.pending();self.app.extensions['sms_transport'].ready=False
        self.app.extensions['notification_tick']();self.app.extensions['sms_tick']()
        self.assertEqual(self.current()['stage'],'notify_pending')
        rows=self.app.extensions['guardian_db'].execute('SELECT status FROM notification_deliveries').fetchall()
        self.assertTrue(all(r[0]=='blocked' for r in rows))

    def test_second_contact_delivery_starts_second_clock_and_medical_escalation(self):
        self.pending();self.action('notification_received');self.action('acknowledge');self.action('confirm_issue')
        self.app.extensions['notification_plan']()
        db=self.app.extensions['guardian_db'];rows=db.execute("SELECT d.* FROM notification_deliveries d JOIN workflow_commands c ON c.id=d.command_id WHERE c.kind='next_step'").fetchall()
        self.assertEqual({r['recipient'] for r in rows},{'second','+886987654321'})
        self.app.extensions['notification_delivered'](rows[0]['id'],'測試第二聯絡人送達')
        w=self.current();self.assertEqual(w['stage'],'awaiting_secondary')
        self.app.extensions['workflow_tick'](w['deadline'])
        self.assertEqual(self.current()['stage'],'medical_pending')

    def test_pwa_assets_and_no_private_offline_cache(self):
        with self.client.get('/sw.js') as response:
            self.assertEqual(response.headers['Service-Worker-Allowed'],'/')
        with self.client.get('/static/manifest.webmanifest') as response:
            manifest=json.loads(response.data.decode('utf-8-sig'))
        self.assertEqual(manifest['display'],'standalone')
        for url in [i['src'] for i in manifest['icons']]+['/static/offline.html']:
            with self.client.get(url) as response:self.assertEqual(response.status_code,200)

if __name__=='__main__':unittest.main()
