import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from guardian.server import create_app


class SourceAlertTests(unittest.TestCase):
    def test_sitting_and_fall_source_alerts_create_mobile_notification_workflow(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root);source=base/'events';source.mkdir()
            db_path=source/'care.sqlite3'
            connection=sqlite3.connect(db_path)
            connection.execute('''CREATE TABLE care_events(
                event_uid TEXT PRIMARY KEY,kind TEXT,status TEXT,risk_level TEXT,
                created_wall REAL,updated_wall REAL,snapshot_path TEXT)''')
            now=time.time()
            connection.execute('INSERT INTO care_events VALUES(?,?,?,?,?,?,?)',
                ('sit-1','prolonged_sitting','alert','high',now,now,None))
            connection.commit();connection.close()
            app=create_app(base/'data',source)
            self.assertEqual(app.extensions['guardian_source_sync'](),1)
            guardian=app.extensions['guardian_db']
            row=guardian.execute("SELECT kind,risk,stage FROM care_workflows WHERE id='care:sit-1'").fetchone()
            self.assertEqual(tuple(row),('prolonged_sitting','high','notify_pending'))
            command=guardian.execute("SELECT kind,status FROM workflow_commands WHERE event='care:sit-1'").fetchone()
            self.assertEqual(tuple(command),('notify','pending'))
            connection=sqlite3.connect(db_path)
            connection.execute("UPDATE care_events SET status='cancelled',updated_wall=? WHERE event_uid='sit-1'",(now+1,))
            connection.commit();connection.close()
            app.extensions['guardian_source_sync']()
            stage=guardian.execute("SELECT stage FROM care_workflows WHERE id='care:sit-1'").fetchone()[0]
            self.assertEqual(stage,'resolved')
            guardian.close()


if __name__=='__main__':
    unittest.main()
