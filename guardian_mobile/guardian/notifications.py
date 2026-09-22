"""Durable independent App push and SMS notification delivery."""
import json
import re
import time
import uuid
from urllib.error import HTTPError
from flask import abort, jsonify, request
from .sms import SmsTransport
from .push import install_push


def phone_number(value):
    if not isinstance(value,str):return None
    value=re.sub(r'[\s()-]','',value)
    if re.fullmatch(r'09\d{8}',value):value='+886'+value[1:]
    return value if not value or re.fullmatch(r'\+[1-9]\d{7,14}',value) else None


def install_notifications(app, db, lock, auth, delivered):
    db.execute('''CREATE TABLE IF NOT EXISTS notification_deliveries(
      id TEXT PRIMARY KEY,command_id TEXT NOT NULL,channel TEXT NOT NULL,recipient TEXT NOT NULL,
      status TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',provider_id TEXT,created REAL NOT NULL,updated REAL NOT NULL,
      UNIQUE(command_id,channel))''');db.commit()
    sms=SmsTransport()
    app.extensions['sms_transport']=sms

    def config():
        row=db.execute("SELECT value FROM settings WHERE key='notification_contacts'").fetchone()
        old=json.loads(row[0]) if row else {}
        value=dict(version=2,enabled=False,primary_user='',secondary_user='',primary_phone='',secondary_phone='',
                   secondary_wait_seconds=600,emergency_phone='119',care_address='',callback_phone='')
        value.update({k:v for k,v in old.items() if k in value and (old.get('version')==2 or k in ('secondary_wait_seconds','emergency_phone','care_address','callback_phone'))})
        return value
    app.extensions['notification_config']=config

    @app.route('/api/notification/config',methods=['GET','POST'])
    @auth(('admin',))
    def settings():
        with lock:
            if request.method=='POST':
                body=request.get_json(silent=True)
                if not isinstance(body,dict) or type(body.get('enabled')) is not bool:abort(400)
                value=config();value['enabled']=body['enabled']
                for prefix in ('primary','secondary'):
                    user=body.get(prefix+'_user','');phone=phone_number(body.get(prefix+'_phone',''))
                    if not isinstance(user,str) or len(user)>64 or phone is None:
                        return jsonify(error='請填寫有效帳號與手機號碼，例如 0912345678 或 +886912345678。'),400
                    if user and not db.execute("SELECT 1 FROM users WHERE name=? AND role IN ('admin','caregiver')",(user,)).fetchone():
                        return jsonify(error='聯絡人需選擇已建立的管理員或照護者帳號。'),400
                    value[prefix+'_user']=user;value[prefix+'_phone']=phone
                if value['enabled'] and (not all(value[k] for k in ('primary_user','secondary_user','primary_phone','secondary_phone')) or value['primary_user']==value['secondary_user'] or value['primary_phone']==value['secondary_phone']):
                    return jsonify(error='啟用前請設定兩位不同的聯絡人帳號與手機號碼。'),400
                wait=body.get('secondary_wait_seconds',600)
                if type(wait) is not int or not 0<=wait<=3600:return jsonify(error='請選擇第二聯絡人的等候時間。'),400
                value['secondary_wait_seconds']=wait
                for key in ('emergency_phone','care_address','callback_phone'):
                    field=body.get(key,value[key])
                    if not isinstance(field,str) or len(field)>500:abort(400)
                    field=field.strip()
                    if key!='care_address' and field and not re.fullmatch(r'\+?[0-9]{2,20}',field):return jsonify(error='通報電話請只填數字，可含開頭的 +。'),400
                    value[key]=field
                db.execute("INSERT OR REPLACE INTO settings VALUES('notification_contacts',?)",(json.dumps(value),));db.commit()
            users=[dict(r) for r in db.execute("SELECT u.name,count(s.id) AS devices FROM users u LEFT JOIN push_subscriptions s ON s.user=u.name WHERE u.role IN ('admin','caregiver') GROUP BY u.name ORDER BY u.name")]
            return jsonify(**config(),users=users,sms_configured=sms.ready,push_configured=bool(app.config['PUBLIC_URL']))

    def mark_delivered(ident,detail):
        with lock:
            row=db.execute('SELECT * FROM notification_deliveries WHERE id=?',(ident,)).fetchone()
            if not row or row['status'] in ('delivered','cancelled'):return
            db.execute("UPDATE notification_deliveries SET status='delivered',detail=?,updated=? WHERE id=?",(detail,time.time(),ident))
            delivered(row['command_id'],('App 推播：' if row['channel']=='push' else '簡訊：')+detail)
            db.commit()
    app.extensions['notification_delivered']=mark_delivered
    install_push(app,db,lock,auth,mark_delivered)

    def plan():
        with lock:
            cfg=config()
            if not cfg['enabled']:return
            now=time.time()
            # An in-page receipt must not suppress either external channel.
            rows=db.execute("""SELECT c.* FROM workflow_commands c JOIN care_workflows w ON w.id=c.event
                WHERE (c.kind='notify' AND w.stage IN ('notify_pending','awaiting_caregiver'))
                   OR (c.kind='next_step' AND w.stage IN ('next_step_pending','awaiting_secondary'))""").fetchall()
            for row in rows:
                prefix='primary' if row['kind']=='notify' else 'secondary'
                for channel,key in (('push','user'),('sms','phone')):
                    db.execute('INSERT OR IGNORE INTO notification_deliveries(id,command_id,channel,recipient,status,created,updated) VALUES(?,?,?,?,?,?,?)',
                               (str(uuid.uuid4()),row['id'],channel,cfg[prefix+'_'+key],'pending',now,now))
            db.commit()

    def update(item,status,detail,provider=None):
        with lock:
            db.execute("UPDATE notification_deliveries SET status=?,detail=?,provider_id=COALESCE(?,provider_id),updated=? WHERE id=? AND status NOT IN ('delivered','cancelled')",(status,detail,provider,time.time(),item['id']));db.commit()

    def claim(channel):
        plan();now=time.time()
        with lock:
            if not config()['enabled']:return None
            db.execute('BEGIN IMMEDIATE')
            # An interrupted SMS POST may already have incurred a charge; do not resend.
            db.execute("UPDATE notification_deliveries SET status='accepted' WHERE channel='sms' AND status='sending' AND provider_id IS NOT NULL AND updated<?",(now-120,))
            db.execute("UPDATE notification_deliveries SET status='unknown',detail='傳送程序中斷，請人工確認服務紀錄；系統不會重複寄送。' WHERE channel='sms' AND status='sending' AND updated<?",(now-120,))
            db.execute("UPDATE notification_deliveries SET status='failed',detail='推播程序中斷，等待重試。' WHERE channel='push' AND status='sending' AND updated<?",(now-120,))
            db.execute("""UPDATE notification_deliveries SET status='cancelled',detail='事件已有人接手或進入下一階段，停止尚未送出的通知。'
                WHERE status IN ('pending','blocked','failed') AND command_id IN
                (SELECT c.id FROM workflow_commands c JOIN care_workflows w ON c.event=w.id WHERE
                 (c.kind='notify' AND w.stage NOT IN ('notify_pending','awaiting_caregiver')) OR
                 (c.kind='next_step' AND w.stage NOT IN ('next_step_pending','awaiting_secondary')))""")
            row=db.execute("""SELECT * FROM notification_deliveries WHERE channel=? AND
                (status='pending' OR (status='blocked' AND updated<?) OR
                 (channel='push' AND status='failed' AND updated<?) OR
                 (channel='sms' AND status='accepted' AND updated<?)) ORDER BY updated LIMIT 1""",(channel,now-60,now-60,now-15)).fetchone()
            if row:db.execute("UPDATE notification_deliveries SET status='sending',updated=? WHERE id=?",(now,row['id']))
            db.commit()
            return dict(row) if row else None

    def push_tick():
        item=claim('push')
        if not item:return
        status,detail=app.extensions['push_send'](item)
        update(item,status,detail)

    def sms_tick():
        item=claim('sms')
        if not item:return
        if not sms.ready or not app.config['PUBLIC_URL']:
            update(item,'blocked','尚未設定簡訊服務或手機可連線的 HTTPS App 網址。');return
        try:
            if item['provider_id']:provider,status=sms.request(message_id=item['provider_id'])
            else:provider,status=sms.request(phone=item['recipient'],body='汪無一失：有照護事件需要您接手確認。請開啟 '+app.config['PUBLIC_URL']+'/?page=events')
            if status=='delivered':
                update(item,'accepted','正在記錄送達',provider);mark_delivered(item['id'],'電信服務回報已送達（尚未接手）')
            elif status in ('failed','undelivered','canceled'):update(item,'failed','簡訊未送達，請確認手機號碼與簡訊服務。',provider)
            else:update(item,'accepted','簡訊服務已接受，等待電信送達回報。',provider)
        except HTTPError as error:
            if item['provider_id']:update(item,'accepted','暫時無法查詢送達回報，稍後再查。')
            elif 400<=error.code<500 and error.code!=408:update(item,'failed','簡訊服務拒絕請求，請檢查帳號與發送地區設定。')
            else:update(item,'unknown','簡訊請求結果不明，請人工確認服務紀錄，避免重複收費。')
        except (OSError,ValueError):
            update(item,'accepted' if item['provider_id'] else 'unknown','暫時無法確認簡訊狀態；已取得編號者會繼續查詢，未取得者請人工確認。')

    app.extensions['notification_plan']=plan
    app.extensions['notification_tick']=push_tick
    app.extensions['sms_tick']=sms_tick
    app.extensions['notification_ready']=lambda: config()['enabled'] and bool(app.config['PUBLIC_URL']) and sms.ready
