import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from guardian.server import create_app

class PortalTests(unittest.TestCase):
    def test_permissions_events_and_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; source.mkdir()
            image=source/'old.jpg'; image.write_bytes(b'example')
            connection=sqlite3.connect(source/'care.sqlite3')
            connection.execute('CREATE TABLE care_events(event_uid,kind,status,risk_level,created_wall,updated_wall,snapshot_path)')
            connection.execute('INSERT INTO care_events VALUES(?,?,?,?,?,?,?)',('one','fall','active','high',time.time()-40*86400,time.time(),str(image)))
            connection.commit()
            app=create_app(root/'data',source); app.testing=True
            admin=app.test_client()
            self.assertEqual(admin.get('/api/events').status_code,401)
            self.assertEqual(admin.get('/api/image/care:one').status_code,401)
            credentials={'name':'admin','password':'a-long-test-password','setup':'wrong'}
            self.assertEqual(admin.post('/api/login',json=credentials).status_code,403)
            credentials['setup']=(root/'data/setup-token.txt').read_text()
            response=admin.post('/api/login',json=credentials)
            self.assertEqual(response.status_code,200)
            header={'X-CSRF':response.json['csrf']}
            self.assertFalse((root/'data/setup-token.txt').exists())
            self.assertEqual(admin.post('/api/action',json={}).status_code,403)
            events=admin.get('/api/events').json['events']
            self.assertEqual(len(events),1); self.assertFalse(image.exists())
            self.assertFalse(events[0]['image_available'])
            self.assertEqual(admin.post('/api/action',headers=header,json={'event':'care:one','status':'acknowledged'}).status_code,409)
            version=events[0]['workflow']['version']
            self.assertEqual(admin.post('/api/workflow/action',headers=header,json={'event':'care:one','action':'acknowledge','version':version}).status_code,200)
            self.assertEqual(connection.execute('SELECT status FROM care_events').fetchone()[0],'active')
            self.assertEqual(admin.post('/api/users',headers=header,json={'name':'reader','password':'another-long-password','role':'viewer'}).status_code,200)
            reader=app.test_client(); login=reader.post('/api/login',json={'name':'reader','password':'another-long-password'})
            self.assertEqual(reader.get('/api/events').status_code,200)
            rh={'X-CSRF':login.json['csrf']}
            self.assertEqual(reader.post('/api/action',headers=rh,json={}).status_code,403)
            self.assertEqual(reader.post('/api/drug',headers=rh,data={'text':'test'}).status_code,403)
            self.assertEqual(reader.get('/api/users').status_code,403)
            admin.post('/api/users/remove',headers=header,json={'name':'reader'})
            self.assertEqual(reader.get('/api/events').status_code,401)
            connection.execute('UPDATE care_events SET snapshot_path=?,created_wall=?',(str(root/'secret.jpg'),time.time())); connection.commit()
            (root/'secret.jpg').write_bytes(b'private')
            self.assertEqual(admin.get('/api/image/care:one').status_code,404)
            self.assertEqual(admin.post('/api/medicines',headers=header,json={'name':'sample','directions':'label'}).status_code,400)
            self.assertEqual(admin.post('/api/medicines',headers=header,json={'name':'sample','directions':'label','confirmed':True,'expiry':'2027-02-30'}).status_code,400)
            self.assertEqual(admin.post('/api/medicines',headers=header,json={'name':'sample','directions':'label','confirmed':True,'expiry':'2027-02-28'}).status_code,200)
            saved=admin.get('/api/medicines').json['medicines'][0]
            self.assertEqual(saved['directions'],'label')
            admin.post('/api/medicines/archive',headers=header,json={'id':saved['id']})
            self.assertEqual(admin.get('/api/medicines').json['medicines'][0]['active'],0)
            app.extensions['guardian_maintenance']()
            changed=admin.post('/api/password',headers=header,json={'current':'a-long-test-password','new':'replacement-password-long'})
            self.assertEqual(changed.status_code,200)
            self.assertEqual(admin.get('/api/events').status_code,401)
            self.assertEqual(admin.post('/api/login',json={'name':'admin','password':'a-long-test-password'}).status_code,401)
            self.assertEqual(admin.post('/api/login',json={'name':'admin','password':'replacement-password-long'}).status_code,200)
            connection.close()
            app.extensions['guardian_db'].close()

if __name__=='__main__': unittest.main()
