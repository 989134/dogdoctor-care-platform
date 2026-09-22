import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from guardian.server import create_app
from guardian.intercom import load_ice_servers

class IntercomTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
  self.app=create_app(root/'data',root/'sources');self.app.testing=True;self.addCleanup(self.app.extensions['guardian_db'].close)
  self.client=self.app.test_client();r=self.client.post('/api/login',json={'name':'admin','password':'long-test-password','setup':(root/'data/setup-token.txt').read_text()});self.csrf={'X-CSRF':r.json['csrf']}
  self.key=self.client.post('/api/intercom/pair',headers=self.csrf,json={}).json['token'];self.headers={'X-Vehicle-Key':self.key};self.instance='test-vehicle-instance'
 def device(self,**kw):return self.client.post('/api/intercom/device',headers=self.headers,json={'instance':self.instance,**kw})
 def start(self):return self.client.post('/api/intercom/calls',headers=self.csrf,json={'offer':{'type':'offer','sdp':'v=0\r\n'}})
 def test_auth_offline_busy_and_owner_isolation(self):
  self.assertEqual(self.start().status_code,409);self.assertEqual(self.device().status_code,200)
  ident=self.start().json['id'];self.assertEqual(self.start().status_code,409)
  self.assertEqual(self.client.post('/api/intercom/device',json={'instance':self.instance}).status_code,401)
  self.client.post('/api/users',headers=self.csrf,json={'name':'other','password':'long-other-password','role':'caregiver'})
  other=self.app.test_client();login=other.post('/api/login',json={'name':'other','password':'long-other-password'})
  self.assertEqual(other.get('/api/intercom/calls/'+ident).status_code,403)
  self.assertEqual(other.post('/api/intercom/pair',headers={'X-CSRF':login.json['csrf']},json={}).status_code,403)
 def test_answer_hangup_and_device_lease(self):
  self.device();ident=self.start().json['id']
  answer={'type':'answer','sdp':'v=0\r\n'};self.assertEqual(self.device(id=ident,answer=answer).status_code,200)
  self.assertEqual(self.client.get('/api/intercom/calls/'+ident).json['answer'],answer)
  self.assertEqual(self.client.post('/api/intercom/device',headers=self.headers,json={'instance':'other-vehicle-instance'}).status_code,409)
  self.client.post('/api/intercom/calls/'+ident,headers=self.csrf,json={'action':'end'})
  self.assertIsNone(self.device().json['call'])
  self.device(action='leave');self.assertFalse(self.client.get('/api/intercom/status').json['online'])
 def test_rotation_timeout_and_viewer_denied(self):
  self.device();ident=self.start().json['id']
  with patch('guardian.intercom.monotonic',return_value=time.monotonic()+21):self.assertEqual(self.client.get('/api/intercom/calls/'+ident).status_code,404)
  self.client.post('/api/intercom/pair',headers=self.csrf,json={});self.assertEqual(self.device().status_code,401)
  self.client.post('/api/users',headers=self.csrf,json={'name':'viewer','password':'long-viewer-password','role':'viewer'})
  viewer=self.app.test_client();login=viewer.post('/api/login',json={'name':'viewer','password':'long-viewer-password'})
  self.assertEqual(viewer.post('/api/intercom/calls',headers={'X-CSRF':login.json['csrf']},json={}).status_code,403)
 def test_csrf_and_malformed_description(self):
  self.assertEqual(self.client.post('/api/intercom/pair',json={}).status_code,403)
  self.assertEqual(self.client.post('/api/intercom/calls',headers=self.csrf,json={'offer':{'type':'answer','sdp':'x'}}).status_code,400)
  self.device();self.assertEqual(self.device(id='missing',answer={'type':'answer','sdp':'x'}).status_code,409)

 def test_ice_configuration_is_validated_and_normalized(self):
  value=load_ice_servers('[{"urls":["stun:rtc.example:3478","turns:rtc.example:5349?transport=tcp"],"username":"robot","credential":"limited"}]')
  self.assertEqual(value[0]['username'],'robot')
  for invalid in ('{}','["stun:bad"]','[{"urls":"https://bad"}]','[{"urls":[]}]'):
   with self.assertRaises(ValueError):load_ice_servers(invalid)

if __name__=='__main__':unittest.main()
