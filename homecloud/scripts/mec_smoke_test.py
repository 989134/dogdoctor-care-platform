"""Black-box verification for a deployed MEC endpoint."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np


def call(url,method='GET',body=None,headers=None,expected=200):
    request=urllib.request.Request(url,data=body,method=method,headers=headers or {})
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            status=response.status;payload=json.loads(response.read())
    except urllib.error.HTTPError as exc:
        status=exc.code;payload=json.loads(exc.read())
    if status!=expected:raise RuntimeError(f'{method} {url}: expected {expected}, got {status}: {payload}')
    return payload


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:7000')
    parser.add_argument('--token',default=os.getenv('MEC_API_TOKEN',''))
    parser.add_argument('--output',type=Path,default=Path('mec_output/mec_smoke_test.json'))
    args=parser.parse_args();base=args.url.rstrip('/')
    health=call(base+'/health')
    auth={'Authorization':'Bearer '+args.token} if args.token else {}
    sequence=int(time.time()*1000)%1_000_000_000
    frame=np.full((120,160,3),110,dtype=np.uint8)
    ok,jpeg=cv2.imencode('.jpg',frame);assert ok
    headers={**auth,'Content-Type':'image/jpeg','X-Device-ID':'smoke-ranger',
             'X-Frame-Seq':str(sequence),'X-Captured-At':str(time.time())}
    unauthorized_verified=False
    if args.token:
        call(base+'/v1/infer','POST',jpeg.tobytes(),
             {k:v for k,v in headers.items() if k!='Authorization'},401)
        unauthorized_verified=True
    first=call(base+'/v1/infer','POST',jpeg.tobytes(),headers)
    duplicate=call(base+'/v1/infer','POST',jpeg.tobytes(),headers)
    old={**headers,'X-Frame-Seq':str(sequence-1)}
    rejected=call(base+'/v1/infer','POST',jpeg.tobytes(),old,409)
    event={'schema_version':'1.0','event_id':f'smoke-ranger:test:{sequence}',
           'device_id':'smoke-ranger','occurred_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
           'type':'smoke_test','severity':'info','mode':'test','acknowledged':False}
    event_headers={**auth,'Content-Type':'application/json'}
    event_raw=json.dumps(event).encode()
    event_first=call(base+'/v1/events','POST',event_raw,event_headers)
    event_duplicate=call(base+'/v1/events','POST',event_raw,event_headers)
    events=call(base+'/v1/events?device_id=smoke-ranger&limit=5',headers=auth)
    metrics=call(base+'/metrics',headers=auth)
    report={'passed':all([health.get('ok'),first.get('ok'),not first.get('duplicate'),
                          duplicate.get('duplicate'),rejected.get('error')=='out-of-order frame_seq',
                          not event_first.get('duplicate'),event_duplicate.get('duplicate'),
                          any(e['event_id']==event['event_id'] for e in events.get('events',()))]),
            'url':base,'auth_required':health.get('auth_required'),
            'unauthorized_verified':unauthorized_verified,
            'frame_seq':sequence,'server_processing_ms':first.get('server_processing_ms'),
            'event_id':event['event_id'],'metrics':metrics}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
