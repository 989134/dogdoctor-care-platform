"""Per-account Web Push registration and verified notification display receipts."""
import base64
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import abort, jsonify, request, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pywebpush import webpush, WebPushException
from requests import Session, RequestException


class NoRedirectSession(Session):
    def request(self,*args,**kwargs):
        kwargs['allow_redirects']=False
        return super().request(*args,**kwargs)


def valid_subscription(body):
    if not isinstance(body,dict):return False
    endpoint=body.get('endpoint');keys=body.get('keys')
    if not isinstance(endpoint,str) or len(endpoint)>4096 or not isinstance(keys,dict):return False
    try:
        url=urlsplit(endpoint);host=url.hostname or ''
        allowed=host in ('fcm.googleapis.com','web.push.apple.com','updates.push.services.mozilla.com') or host.endswith('.push.services.mozilla.com')
        if not allowed or url.scheme!='https' or url.port not in (None,443) or url.username or url.password or url.fragment:return False
        decoded=[]
        for key in ('p256dh','auth'):
            value=keys.get(key,'')
            if not isinstance(value,str) or not 1<=len(value)<=120:return False
            decoded.append(base64.b64decode(value+'='*((-len(value))%4),altchars=b'-_',validate=True))
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),decoded[0])
        return len(decoded[1])==16
    except (ValueError,TypeError):return False


def install_push(app,db,lock,auth,mark_delivered):
    db.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions(
      id TEXT PRIMARY KEY,user TEXT NOT NULL,endpoint TEXT NOT NULL UNIQUE,keys_json TEXT NOT NULL,updated REAL NOT NULL)''');db.commit()
    key_path=Path(app.config['DATA_DIR'])/'push-private.pem'
    if not key_path.exists():
        private=ec.generate_private_key(ec.SECP256R1())
        key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    private=serialization.load_pem_private_key(key_path.read_bytes(),password=None)
    public=base64.urlsafe_b64encode(private.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)).decode().rstrip('=')
    signer=URLSafeTimedSerializer(app.secret_key,salt='guardian-push-receipt')

    @app.get('/api/push/config')
    @auth()
    def push_config():
        with lock:count=db.execute('SELECT count(*) FROM push_subscriptions WHERE user=?',(session['user'],)).fetchone()[0]
        return jsonify(public_key=public,devices=count,public_url=app.config['PUBLIC_URL'],configured=bool(app.config['PUBLIC_URL']))

    @app.post('/api/push/subscribe')
    @auth(('admin','caregiver'))
    def subscribe():
        body=request.get_json(silent=True)
        if not valid_subscription(body):return jsonify(error='無效或不支援的推播訂閱。'),400
        with lock:
            owner=session['user']
            count=db.execute('SELECT count(*) FROM push_subscriptions WHERE user=?',(owner,)).fetchone()[0]
            existing=db.execute('SELECT id FROM push_subscriptions WHERE endpoint=?',(body['endpoint'],)).fetchone()
            if count>=10 and not existing:return jsonify(error='每個帳號最多綁定 10 個通知裝置。'),400
            db.execute('''INSERT INTO push_subscriptions VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET user=excluded.user,keys_json=excluded.keys_json,updated=excluded.updated''',
                       (str(uuid.uuid4()),owner,body['endpoint'],json.dumps(body['keys']),time.time()));db.commit()
            session['push_endpoint']=body['endpoint']
        return jsonify(ok=True)

    @app.post('/api/push/unsubscribe')
    @auth()
    def unsubscribe():
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or not isinstance(body.get('endpoint'),str):abort(400)
        with lock:
            db.execute('DELETE FROM push_subscriptions WHERE user=? AND endpoint=?',(session['user'],body['endpoint']));db.commit()
        session.pop('push_endpoint',None)
        return jsonify(ok=True)

    @app.post('/api/push/device')
    @auth()
    def device():
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or not isinstance(body.get('endpoint'),str):abort(400)
        with lock:
            bound=bool(db.execute('SELECT 1 FROM push_subscriptions WHERE user=? AND endpoint=?',(session['user'],body['endpoint'])).fetchone())
        if bound:session['push_endpoint']=body['endpoint']
        return jsonify(bound=bound)

    @app.post('/api/push/received')
    def received():
        body=request.get_json(silent=True)
        try:claims=signer.loads(body.get('receipt',''),max_age=3600)
        except (BadSignature,SignatureExpired,AttributeError,TypeError):abort(403)
        with lock:
            sub=db.execute('''SELECT s.id FROM push_subscriptions s JOIN users u ON u.name=s.user
                WHERE s.id=? AND s.user=? AND u.role IN ('admin','caregiver')''',(claims['subscription'],claims['user'])).fetchone()
            item=db.execute("SELECT id FROM notification_deliveries WHERE id=? AND recipient=? AND channel='push'",(claims['delivery'],claims['user'])).fetchone()
            if not sub or not item:abort(403)
            mark_delivered(claims['delivery'],'裝置已顯示 App 推播（尚未接手）')
        return jsonify(ok=True)

    def send(item):
        with lock:
            subscriptions=[dict(r) for r in db.execute('''SELECT s.* FROM push_subscriptions s JOIN users u ON u.name=s.user
                WHERE s.user=? AND u.role IN ('admin','caregiver')''',(item['recipient'],))]
        if not subscriptions:return 'blocked','此照護者尚未啟用任何裝置的 App 通知。'
        if not app.config['PUBLIC_URL']:return 'blocked','尚未設定手機可連線的 HTTPS App 網址。'
        accepted=False
        for sub in subscriptions:
            receipt=signer.dumps(dict(delivery=item['id'],subscription=sub['id'],user=sub['user']))
            payload=dict(title='汪無一失',body='有照護事件需要您接手確認，請開啟 App。',url='/?page=events',receipt=receipt,tag=item['id'])
            try:
                response=webpush(subscription_info={'endpoint':sub['endpoint'],'keys':json.loads(sub['keys_json'])},data=json.dumps(payload),
                    vapid_private_key=str(key_path),vapid_claims={'sub':app.config['PUBLIC_URL']},ttl=600,timeout=10,
                    headers={'Urgency':'high'},requests_session=NoRedirectSession())
                if 200<=response.status_code<300:accepted=True
            except WebPushException as error:
                if error.response is not None and error.response.status_code in (404,410):
                    with lock:db.execute('DELETE FROM push_subscriptions WHERE id=?',(sub['id'],));db.commit()
            except (OSError,ValueError,RequestException):pass
        return ('accepted','推播服務已接受，等待裝置顯示回報。') if accepted else ('failed','推播傳送失敗，稍後重試。')
    app.extensions['push_send']=send
