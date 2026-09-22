"""Verify CHT IoT event upload and TAS notification contracts end to end."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mec_event_sink import ChtIotEventSink,ChtPlatformSink,ChtTasNotifier


def masked(phone):return phone[:2]+'*'*(len(phone)-4)+phone[-2:]


def main():
    parser=argparse.ArgumentParser(description='CHT platform event and callout smoke test')
    parser.add_argument('--iot-url',default=os.getenv('CHT_IOT_EVENT_URL',''))
    parser.add_argument('--iot-api-key',default=os.getenv('CHT_IOT_API_KEY',''))
    parser.add_argument('--tas-url',default=os.getenv('CHT_TAS_URL',ChtTasNotifier.DEFAULT_URL))
    parser.add_argument('--tas-api-key',default=os.getenv('CHT_TAS_API_KEY',''))
    parser.add_argument('--service-number',default=os.getenv('CHT_TAS_SERVICE_NUMBER',''))
    parser.add_argument('--phone',default=(os.getenv('CHT_TAS_PHONES','').split(',')[0]))
    parser.add_argument('--device-id',default='ranger-platform-smoke')
    parser.add_argument('--ledger',type=Path,default=Path('mec_output/cht_smoke_ledger.sqlite3'))
    parser.add_argument('--output',type=Path,default=Path('mec_output/cht_platform_smoke.json'))
    parser.add_argument('--timeout',type=float,default=8)
    args=parser.parse_args()
    event_sink=ChtIotEventSink(args.iot_url,args.iot_api_key,args.timeout)
    notifier=ChtTasNotifier(args.tas_api_key,args.service_number,[args.phone],
                            args.tas_url,args.timeout)
    sink=ChtPlatformSink(args.ledger,event_sink,notifier)
    sequence=int(time.time()*1000)
    event={'schema_version':'1.0','event_id':f'{args.device_id}-smoke-{sequence}:alert',
           'incident_id':f'{args.device_id}-smoke-{sequence}','device_id':args.device_id,
           'occurred_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
           'type':'tracking_review','severity':'high','mode':'platform_smoke',
           'acknowledged':False,'details':{'test':True}}
    first=sink.upload_event(event);second=sink.upload_event(event);stats=sink.stats();sink.close()
    report={'passed':bool(first['event_uploaded'] and first['notification_sent'] and
                          second['duplicate'] and stats['uploaded']==1 and
                          stats['notifications']==1),
            'event_id':event['event_id'],'device_id':args.device_id,
            'iot_url':args.iot_url,'tas_url':args.tas_url,
            'recipient':masked(args.phone),'first':first,'second':second,'ledger':stats}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
