"""Local CHT platform contract emulator for event upload and TAS callout tests."""
import argparse
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from flask import Flask,jsonify,request


EVENT_ID=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$')
PHONE=re.compile(r'^0[0-9]{8,9}$')


class PlatformStore:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS events('
                        'event_id TEXT PRIMARY KEY,received_at REAL,payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS callouts('
                        'group_id TEXT PRIMARY KEY,received_at REAL,payload TEXT)')
        self.db.commit()
    def add_event(self,event):
        with self.lock:
            cursor=self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?)',(
                event['event_id'],time.time(),json.dumps(event,ensure_ascii=False)))
            self.db.commit();return cursor.rowcount==1
    def add_callout(self,payload):
        group=uuid.uuid4().hex[:16]
        with self.lock:
            self.db.execute('INSERT INTO callouts VALUES(?,?,?)',(
                group,time.time(),json.dumps(payload,ensure_ascii=False)))
            self.db.commit()
        return group
    def records(self):
        with self.lock:
            events=[json.loads(row[0]) for row in self.db.execute(
                'SELECT payload FROM events ORDER BY received_at DESC')]
            callouts=[{'group':row[0],**json.loads(row[1])} for row in self.db.execute(
                'SELECT group_id,payload FROM callouts ORDER BY received_at DESC')]
        return {'events':events,'callouts':callouts}
    def close(self):
        with self.lock:self.db.close()


def create_app(api_key,event_store='mec_output/cht_platform_emulator.sqlite3'):
    if not api_key:raise ValueError('emulator API key is required')
    app=Flask(__name__);store=PlatformStore(event_store)
    app.extensions['cht_platform_store']=store

    def authorized():
        return hmac.compare_digest(request.headers.get('X-API-Key',''),api_key)

    @app.get('/health')
    def health():
        records=store.records()
        return jsonify(ok=True,service='cht-platform-contract-emulator',
                       mode='local_simulation_not_chunghwa_telecom',
                       events=len(records['events']),callouts=len(records['callouts']))

    @app.post('/v1/iot/events')
    def event():
        if not authorized():return jsonify(ok=False,error='unauthorized'),401
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict) or not EVENT_ID.fullmatch(str(payload.get('event_id',''))):
            return jsonify(ok=False,error='invalid event'),400
        if not payload.get('device_id') or not payload.get('type'):
            return jsonify(ok=False,error='device_id and type are required'),400
        inserted=store.add_event(payload)
        return jsonify(ok=True,event_id=payload['event_id'],duplicate=not inserted)

    @app.post('/apis/CHTIoT/phone-conn/v1/callout')
    def callout():
        if not authorized():return jsonify(status='err',msg='unauthorized'),401
        payload=request.get_json(silent=True) or {};phones=payload.get('phones')
        ivr=payload.get('ivrData') or {}
        if (not PHONE.fullmatch(str(payload.get('serviceNumber',''))) or
                not isinstance(phones,list) or not phones or
                any(not PHONE.fullmatch(str(phone)) for phone in phones) or
                not isinstance(ivr.get('text'),str) or not ivr['text']):
            return jsonify(status='err',msg='invalid callout payload'),400
        return jsonify(status='ok',group=store.add_callout(payload))

    @app.get('/v1/records')
    def records():
        if not authorized():return jsonify(ok=False,error='unauthorized'),401
        return jsonify(ok=True,**store.records())
    return app


def main():
    parser=argparse.ArgumentParser(description='Local CHT platform contract emulator')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=7100)
    parser.add_argument('--api-key',default=os.getenv('CHT_EMULATOR_API_KEY',''))
    parser.add_argument('--database',type=Path,
                        default=Path('mec_output/cht_platform_emulator.sqlite3'))
    args=parser.parse_args()
    create_app(args.api_key,args.database).run(host=args.host,port=args.port,
                                               debug=False,use_reloader=False,threaded=True)


if __name__=='__main__':main()
