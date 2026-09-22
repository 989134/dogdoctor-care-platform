"""Event delivery adapters used by the edge outbox."""
import json
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class EventSink:
    def upload_event(self,event):
        raise NotImplementedError

    def close(self):
        pass


def require_secure_url(url):
    parsed=urlsplit(url)
    if parsed.scheme=='https':return url
    if parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1'):
        return url
    raise ValueError('platform endpoint must use HTTPS; HTTP is allowed only on localhost')


class LocalJsonlEventSink(EventSink):
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.seen=set()
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                try:self.seen.add(json.loads(line)['event_id'])
                except (ValueError,KeyError):pass

    def upload_event(self,event):
        event_id=event['event_id']
        if event_id in self.seen:return {'ok':True,'duplicate':True}
        with self.path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        self.seen.add(event_id)
        return {'ok':True,'duplicate':False}


class WebhookEventSink(EventSink):
    def __init__(self,url,token='',timeout=5):
        self.url=url
        self.token=token
        self.timeout=timeout

    def upload_event(self,event):
        headers={'Content-Type':'application/json','Idempotency-Key':event['event_id']}
        if self.token:headers['Authorization']='Bearer '+self.token
        request=urllib.request.Request(self.url,data=json.dumps(event,ensure_ascii=False).encode(),
                                       headers=headers,method='POST')
        with urllib.request.urlopen(request,timeout=self.timeout) as response:
            payload=json.loads(response.read())
            if not payload.get('ok'):
                raise RuntimeError(payload.get('error','event sink rejected event'))
            return payload


def post_json(url,payload,headers,timeout):
    request=urllib.request.Request(
        url,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json',**headers},method='POST')
    with urllib.request.urlopen(request,timeout=timeout) as response:
        raw=response.read()
        return json.loads(raw) if raw else {'ok':True}


class ChtIotEventSink(EventSink):
    """Upload normalized events to the tenant endpoint supplied by CHT IoT."""
    def __init__(self,url,api_key,timeout=5,transport=post_json):
        if not url or not api_key:raise ValueError('CHT IoT URL and API key are required')
        self.url=require_secure_url(url);self.api_key=api_key
        self.timeout=timeout;self.transport=transport

    def upload_event(self,event):
        result=self.transport(self.url,event,{
            'X-API-Key':self.api_key,'Idempotency-Key':event['event_id']},self.timeout)
        if not (result.get('ok') or result.get('status')=='ok'):
            raise RuntimeError(result.get('error') or result.get('msg') or
                               'CHT IoT event endpoint rejected event')
        return result


class ChtTasNotifier:
    """CHT Telephone Access Service callout adapter based on its public OpenAPI."""
    DEFAULT_URL='https://tasapi.cht.com.tw/apis/CHTIoT/phone-conn/v1/callout'
    PHONE=re.compile(r'^0[0-9]{8,9}$')

    DEFAULT_TYPES=('fall','tracking_review','suspected_fall')

    def __init__(self,api_key,service_number,phones,url=DEFAULT_URL,timeout=8,
                 transport=post_json,notify_types=DEFAULT_TYPES):
        phones=[phone.strip() for phone in phones if phone.strip()]
        if not api_key:raise ValueError('CHT TAS API key is required')
        if not self.PHONE.fullmatch(service_number):
            raise ValueError('invalid CHT TAS service number')
        if not phones or any(not self.PHONE.fullmatch(phone) for phone in phones):
            raise ValueError('invalid CHT TAS recipient phone number')
        self.api_key=api_key;self.service_number=service_number;self.phones=phones
        self.url=require_secure_url(url);self.timeout=timeout;self.transport=transport
        self.notify_types=frozenset(notify_types)

    def accepts(self,event):
        return event.get('severity')=='high' and event.get('type') in self.notify_types

    @staticmethod
    def message(event):
        if event.get('type') in ('fall','tracking_review','suspected_fall'):
            return ('銀髮守護犬偵測到高風險疑似跌倒，且沒有收到長者取消回應。'
                    '請立即聯絡或前往查看；若有緊急危險請撥打一一九。')
        return '銀髮守護犬偵測到高風險居家照護事件，請立即登入照護系統確認。'

    def notify_event(self,event):
        payload={'serviceNumber':self.service_number,'phones':self.phones,
                 'ivrData':{'text':self.message(event),'repeat':2,'promptMode':'F'}}
        result=self.transport(self.url,payload,{'X-API-Key':self.api_key},self.timeout)
        if not (result.get('ok') or result.get('status')=='ok'):
            raise RuntimeError(result.get('error') or result.get('msg') or
                               'CHT TAS rejected callout')
        return result

    def close(self):pass


class ChtPlatformSink(EventSink):
    """Deliver an incident to IoT and TAS once per channel with a durable ledger."""
    def __init__(self,ledger_path,event_sink=None,notifier=None):
        if event_sink is None and notifier is None:
            raise ValueError('at least one CHT platform channel is required')
        path=Path(ledger_path);path.parent.mkdir(parents=True,exist_ok=True)
        self.event_sink=event_sink;self.notifier=notifier;self.lock=threading.RLock()
        self.connection=sqlite3.connect(path,check_same_thread=False)
        self.connection.execute(
            'CREATE TABLE IF NOT EXISTS deliveries('
            'event_id TEXT PRIMARY KEY,event_uploaded INTEGER NOT NULL DEFAULT 0,'
            'notification_sent INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL)')
        self.connection.commit()

    def upload_event(self,event):
        event_id=event['event_id'];notify=(self.notifier is not None and
            (self.notifier.accepts(event) if hasattr(self.notifier,'accepts')
             else event.get('severity')=='high'))
        with self.lock:
            row=self.connection.execute(
                'SELECT event_uploaded,notification_sent FROM deliveries WHERE event_id=?',
                (event_id,)).fetchone() or (0,0)
            uploaded,notified=map(bool,row);was_complete=(uploaded or self.event_sink is None) and (
                notified or not notify)
            if self.event_sink is not None and not uploaded:
                self.event_sink.upload_event(event);uploaded=True
                self._save(event_id,uploaded,notified)
            if notify and not notified:
                self.notifier.notify_event(event);notified=True
                self._save(event_id,uploaded,notified)
            return {'ok':True,'duplicate':was_complete,'event_uploaded':uploaded,
                    'notification_sent':notified if notify else False}

    def _save(self,event_id,uploaded,notified):
        self.connection.execute(
            'INSERT INTO deliveries(event_id,event_uploaded,notification_sent,updated_at) '
            'VALUES(?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET '
            'event_uploaded=excluded.event_uploaded,'
            'notification_sent=excluded.notification_sent,updated_at=excluded.updated_at',
            (event_id,int(uploaded),int(notified),time.time()))
        self.connection.commit()

    def stats(self):
        with self.lock:
            row=self.connection.execute(
                'SELECT COUNT(*),COALESCE(SUM(event_uploaded),0),'
                'COALESCE(SUM(notification_sent),0) FROM deliveries').fetchone()
        return {'events':row[0],'uploaded':row[1],'notifications':row[2]}

    def close(self):
        for channel in (self.event_sink,self.notifier):
            if channel is not None and hasattr(channel,'close'):channel.close()
        with self.lock:self.connection.close()
