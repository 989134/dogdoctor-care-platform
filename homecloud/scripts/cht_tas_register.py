"""List or rent a CHT TAS service number using the official REST API."""
import argparse
import json
import os
import urllib.request
from pathlib import Path


DEFAULT_BASE='https://tasapi.cht.com.tw/apis/CHTIoT'


def request_json(url,api_key,method='GET',payload=None,timeout=10):
    raw=(json.dumps(payload,ensure_ascii=False).encode('utf-8')
         if payload is not None else None)
    request=urllib.request.Request(url,data=raw,method=method,headers={
        'X-API-Key':api_key,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        body=response.read();return json.loads(body) if body else {}


def registration_payload(app_name,app_description):
    if not app_name.strip():raise ValueError('app name is required')
    result={'appName':app_name.strip()}
    if app_description.strip():result['appDesc']=app_description.strip()
    return result


def main():
    parser=argparse.ArgumentParser(description='CHT TAS service-number helper')
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--list',action='store_true',help='list existing service numbers')
    action.add_argument('--create',action='store_true',help='rent one service number')
    parser.add_argument('--api-key',default=os.getenv('CHT_TAS_API_KEY',''))
    parser.add_argument('--base-url',default=os.getenv('CHT_TAS_BASE_URL',DEFAULT_BASE))
    parser.add_argument('--app-name',default='SilverGuardianDog')
    parser.add_argument('--app-description',default='長照跌倒高風險電話通告')
    parser.add_argument('--output',type=Path,default=Path('mec_output/cht_tas_registration.json'))
    args=parser.parse_args()
    if not args.api_key:parser.error('set CHT_TAS_API_KEY or --api-key')
    url=args.base_url.rstrip('/')+'/phone-conn/v1/reg'
    result=(request_json(url,args.api_key) if args.list else
            request_json(url,args.api_key,'POST',
                         registration_payload(args.app_name,args.app_description)))
    report={'action':'list' if args.list else 'create','url':url,'result':result}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
