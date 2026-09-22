import tempfile
import unittest
from pathlib import Path
from guardian.server import create_app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.app=create_app(root/'data',root/'source');self.app.testing=True
        self.addCleanup(self.app.extensions['guardian_db'].close)
        self.client=self.app.test_client()
        self.token=(root/'data/setup-token.txt').read_text()

    def login(self,**overrides):
        body=dict(name='admin',password='my-long-test-password',setup=self.token)
        body.update(overrides)
        return self.client.post('/api/login',json=body)

    def test_setup_status_does_not_expose_token(self):
        result=self.client.get('/api/auth/status')
        self.assertTrue(result.json['setup_required']);self.assertNotIn(self.token,result.text)
        self.assertEqual(self.login().status_code,200)
        self.assertFalse(self.client.get('/api/auth/status').json['setup_required'])

    def test_format_errors_do_not_lock_setup(self):
        for _ in range(15):
            result=self.login(password='1234')
            self.assertEqual(result.status_code,400);self.assertIn('12',result.json['error'])
        self.assertEqual(self.client.get('/api/auth/status').json['retry_after'],0)
        self.assertEqual(self.login().status_code,200)

    def test_correct_local_secret_recovers_failed_setup(self):
        for _ in range(10):self.assertEqual(self.login(setup='wrong').status_code,403)
        denied=self.login(setup='wrong');self.assertEqual(denied.status_code,429)
        self.assertGreater(denied.json['retry_after'],0)
        self.assertEqual(self.login(setup=' \n'+self.token+'\n ',name=' admin ').status_code,200)
        self.assertEqual(self.login(setup='').status_code,200)

    def test_existing_login_limit_still_enforced(self):
        self.login()
        for _ in range(10):self.assertEqual(self.login(password='wrong').status_code,401)
        response=self.login()
        self.assertEqual(response.status_code,429);self.assertIn('Retry-After',response.headers)
        self.assertFalse(self.client.get('/api/auth/status').json['setup_required'])

    def test_login_without_setup_after_creation(self):
        self.assertEqual(self.login().status_code,200)
        other=self.app.test_client()
        self.assertEqual(other.post('/api/login',json={'name':'admin','password':'my-long-test-password'}).status_code,200)
        self.assertEqual(other.post('/api/login',json=[]).status_code,400)


if __name__=='__main__':unittest.main()
