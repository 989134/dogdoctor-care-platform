"""SQLite persistence for care events and restart-safe confirmation deadlines."""
import json
import sqlite3
import threading
import time
from pathlib import Path


class CareEventStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.connection=sqlite3.connect(self.path,check_same_thread=False)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute(
            'CREATE TABLE IF NOT EXISTS care_events ('
            'event_uid TEXT PRIMARY KEY,event_id INTEGER NOT NULL,kind TEXT NOT NULL,'
            'status TEXT NOT NULL,risk_level TEXT,created_wall REAL NOT NULL,'
            'updated_wall REAL NOT NULL,payload TEXT NOT NULL,snapshot_path TEXT)')
        self.connection.commit()

    def upsert(self,event,now_monotonic,snapshot_path=None):
        wall=time.time()
        deadline=event.get('escalate_at',event.get('confirm_at',now_monotonic))
        payload=dict(event)
        payload['_saved_wall']=wall
        payload['_remaining_seconds']=max(0,float(deadline)-now_monotonic)
        created_wall=wall-max(0,now_monotonic-float(event.get('created_at',now_monotonic)))
        with self.lock:
            self.connection.execute(
                'INSERT INTO care_events(event_uid,event_id,kind,status,risk_level,'
                'created_wall,updated_wall,payload,snapshot_path) VALUES(?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(event_uid) DO UPDATE SET status=excluded.status,'
                'risk_level=excluded.risk_level,updated_wall=excluded.updated_wall,'
                'payload=excluded.payload,snapshot_path=COALESCE(excluded.snapshot_path,care_events.snapshot_path)',
                (event['event_uid'],event['id'],event['kind'],event['status'],
                 event.get('risk_level'),created_wall,wall,
                 json.dumps(payload,ensure_ascii=False),str(snapshot_path) if snapshot_path else None))
            self.connection.commit()

    def unresolved(self):
        with self.lock:
            rows=self.connection.execute(
                "SELECT payload,snapshot_path FROM care_events WHERE status IN ('pending','review','alert') "
                'ORDER BY updated_wall DESC').fetchall()
        return [(json.loads(row['payload']),row['snapshot_path']) for row in rows]

    def history(self,limit=100):
        limit=max(1,min(int(limit),500))
        with self.lock:
            rows=self.connection.execute(
                'SELECT event_uid,event_id,kind,status,risk_level,created_wall,updated_wall,'
                'snapshot_path FROM care_events ORDER BY updated_wall DESC LIMIT ?',
                (limit,)).fetchall()
        return [{**dict(row),'snapshot_available':bool(row['snapshot_path'])} for row in rows]

    def snapshot_for(self,event_uid):
        with self.lock:
            row=self.connection.execute('SELECT snapshot_path FROM care_events WHERE event_uid=?',
                                        (event_uid,)).fetchone()
        return Path(row['snapshot_path']) if row and row['snapshot_path'] else None

    def close(self):
        with self.lock:
            self.connection.close()
