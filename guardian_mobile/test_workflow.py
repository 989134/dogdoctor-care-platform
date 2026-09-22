import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from guardian.server import create_app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.app=create_app(self.root/'data',self.root/'events'); self.app.testing=True
        self.addCleanup(lambda: self.app.extensions['guardian_db'].close())
        self.app.config['WORKFLOW_TOKENS'].update(robot='robot-test',notify='notify-test',next_step='notify-test',medical='medical-test')
        self.client=self.app.test_client()
        login=self.client.post('/api/login',json={'name':'admin','password':'long-password-for-tests','setup':(self.root/'data/setup-token.txt').read_text()})
        self.csrf={'X-CSRF':login.json['csrf']}

    def device(self,url,body=None,kind='robot'):
        headers={'Authorization':'Bearer '+({'robot':'robot-test','medical':'medical-test'}.get(kind,'notify-test'))}
        return self.client.get(url,headers=headers) if body is None else self.client.post(url,json=body,headers=headers)

    def detect(self):
        return self.device('/api/integration/events',{'event_id':'event-1','kind':'fall'}).json['workflow']

    def commands(self,kind):
        return self.device('/api/integration/commands/'+kind,kind=kind).json['commands']

    def result(self,kind,result,command=None):
        command=command or self.commands(kind)[0]['id']
        return self.device('/api/integration/commands/'+kind+'/'+command+'/result',{'result':result,'detail':'測試設備回報'},kind)

    def current(self):
        return self.client.get('/api/events').json['events'][0]['workflow']

    def action(self,action,note='已核對現場狀況',version=None):
        w=self.current()
        return self.client.post('/api/workflow/action',headers=self.csrf,json={'event':w['id'],'action':action,'note':note,'version':w['version'] if version is None else version})

    def waiting(self):
        self.detect(); self.result('robot','started'); self.result('robot','no_response'); self.result('notify','delivered')
        return self.current()

    def test_authentication_and_duplicate_detection(self):
        self.assertEqual(self.client.post('/api/integration/events',json={}).status_code,401)
        first=self.detect(); second=self.detect()
        self.assertEqual(first['id'],second['id']); self.assertEqual(len(self.commands('robot')),1)
        self.assertEqual(self.device('/api/integration/commands/notify',kind='robot').status_code,401)

    def test_call_must_start_before_response_and_normal_completion(self):
        self.detect()
        self.assertEqual(self.result('robot','normal').status_code,409)
        self.result('robot','started'); self.result('robot','normal')
        self.assertEqual(self.current()['stage'],'elder_ok'); self.assertEqual(self.commands('notify'),[])

    def test_unusual_response_and_failed_notification_have_no_deadline(self):
        self.detect(); self.result('robot','started'); self.result('robot','abnormal')
        self.result('notify','failed')
        w=self.current(); self.assertEqual(w['stage'],'notify_pending'); self.assertIsNone(w['deadline'])

    def test_exact_timeout_and_no_duplicate_escalation(self):
        w=self.waiting(); self.assertAlmostEqual(w['deadline']-w['notified_at'],600)
        self.app.extensions['workflow_tick'](w['deadline']-0.1)
        self.assertEqual(self.current()['stage'],'awaiting_caregiver')
        self.app.extensions['workflow_tick'](w['deadline'])
        self.app.extensions['workflow_tick'](w['deadline']+100)
        self.assertEqual(self.current()['stage'],'next_step_pending'); self.assertEqual(len(self.commands('next_step')),1)
        self.result('next_step','delivered')
        self.assertEqual(self.current()['stage'],'awaiting_secondary')

    def test_caregiver_ack_stops_timer_and_requires_confirmation(self):
        w=self.waiting()
        self.assertEqual(self.action('resolve').status_code,409)
        self.assertEqual(self.action('acknowledge').status_code,200)
        self.app.extensions['workflow_tick'](w['deadline']+1)
        self.assertEqual(self.current()['stage'],'reviewing'); self.assertEqual(self.commands('next_step'),[])
        self.assertEqual(self.action('confirm_safe',note='').status_code,400)
        self.assertEqual(self.action('confirm_safe').status_code,200)
        self.assertEqual(self.current()['stage'],'resolved')

    def test_confirm_issue_and_manual_handling(self):
        self.waiting(); self.action('acknowledge'); self.action('confirm_issue')
        self.assertEqual(self.current()['stage'],'next_step_pending')
        self.assertEqual(self.action('take_over').status_code,200)
        self.assertEqual(len(self.commands('next_step')),1) # Taking over must not cancel the second notification.
        self.result('next_step','delivered')
        self.assertEqual(self.current()['stage'],'handling')
        self.assertEqual(self.action('resolve').status_code,200)

    def test_stale_updates_and_viewer_cannot_acknowledge(self):
        w=self.waiting(); self.action('acknowledge')
        self.assertEqual(self.action('confirm_safe',version=w['version']).status_code,409)
        self.client.post('/api/users',headers=self.csrf,json={'name':'reader','password':'long-password-for-reader','role':'viewer'})
        login=self.client.post('/api/login',json={'name':'reader','password':'long-password-for-reader'})
        self.csrf={'X-CSRF':login.json['csrf']}
        self.assertEqual(self.action('confirm_safe').status_code,403)

    def test_browser_receipt_starts_deadline_once(self):
        self.client.post('/api/notification/config',headers=self.csrf,json={'primary_user':'admin','enabled':False})
        self.detect(); self.result('robot','started'); self.result('robot','no_response')
        self.assertIsNone(self.current()['deadline'])
        self.action('notification_received'); w=self.current()
        self.assertIsNotNone(w['deadline'])
        self.assertEqual(self.action('notification_received').status_code,409)
        self.assertEqual(self.current()['deadline'],w['deadline'])

    def test_restart_recovers_overdue_deadline(self):
        w=self.waiting()
        second=create_app(self.root/'data',self.root/'events')
        try:
            second.extensions['workflow_tick'](w['deadline']+1)
            self.assertEqual(self.current()['stage'],'next_step_pending')
            self.assertEqual(len(self.commands('next_step')),1)
        finally: second.extensions['guardian_db'].close()

    def secondary_waiting(self):
        w=self.waiting();self.app.extensions['workflow_tick'](w['deadline'])
        self.result('next_step','delivered')
        return self.current()

    def test_secondary_timeout_sends_medical_once(self):
        w=self.secondary_waiting()
        self.assertEqual(w['stage'],'awaiting_secondary')
        self.assertAlmostEqual(w['deadline']-w['secondary_notified_at'],600)
        self.app.extensions['workflow_tick'](w['deadline']-0.01)
        self.assertEqual(self.commands('medical'),[])
        self.app.extensions['workflow_tick'](w['deadline'])
        self.app.extensions['workflow_tick'](w['deadline']+1)
        self.assertEqual(self.current()['stage'],'medical_pending')
        commands=self.commands('medical');self.assertEqual(len(commands),1)
        self.result('medical','failed',commands[0]['id'])
        self.assertEqual(self.current()['stage'],'medical_pending')
        self.result('medical','delivered',commands[0]['id'])
        self.assertEqual(self.current()['stage'],'medical_notified')
        self.assertEqual(self.result('medical','delivered',commands[0]['id']).status_code,200)
        self.assertEqual(self.action('take_over').status_code,200)

    def test_secondary_ack_stops_medical_escalation(self):
        w=self.secondary_waiting();self.action('take_over')
        self.app.extensions['workflow_tick'](w['deadline']+1)
        self.assertEqual(self.current()['stage'],'handling')
        self.assertEqual(self.commands('medical'),[])

    def test_secondary_delivery_failure_does_not_start_timeout(self):
        w=self.waiting();self.app.extensions['workflow_tick'](w['deadline'])
        self.result('next_step','failed')
        self.assertIsNone(self.current()['deadline'])
        self.app.extensions['workflow_tick'](w['deadline']+3600)
        self.assertEqual(self.commands('medical'),[])

    def test_secondary_timer_survives_restart(self):
        w=self.secondary_waiting()
        app=create_app(self.root/'data',self.root/'events')
        try:
            app.extensions['workflow_tick'](w['deadline'])
            self.assertEqual(self.current()['stage'],'medical_pending')
            self.assertEqual(len(self.commands('medical')),1)
        finally:app.extensions['guardian_db'].close()

    def test_medical_manual_call_and_payload(self):
        w=self.secondary_waiting();self.app.extensions['workflow_tick'](w['deadline'])
        cmd=self.commands('medical')[0]
        self.assertEqual(cmd['payload']['emergency_phone'],'119')
        self.assertIn('care_address',cmd['missing_configuration'])
        self.assertIn('callback_phone',cmd['missing_configuration'])
        self.assertEqual(self.action('record_medical_call',note='').status_code,400)
        self.assertEqual(self.action('record_medical_call',note='測試：完成通報及地址確認').status_code,200)
        self.assertEqual(self.current()['stage'],'medical_notified')
        self.assertEqual(self.commands('medical'),[])

    def test_emergency_settings_phone_validation(self):
        response=self.client.post('/api/notification/config',headers=self.csrf,json={'primary':'','secondary':'','enabled':False,'emergency_phone':'119;bad'})
        self.assertEqual(response.status_code,400)


if __name__=='__main__': unittest.main()
