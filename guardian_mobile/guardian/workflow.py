"""Durable care workflow and authenticated device/notification command bridge."""
import os
import json
import secrets
import time
import uuid

from flask import abort, jsonify, request, session, has_request_context

TERMINAL = {'resolved', 'elder_ok'}
LABELS = {
    'call_pending': '等待機器狗呼叫', 'calling': '機器狗呼叫中',
    'notify_pending': '等待通知送達', 'awaiting_caregiver': '等待照護者回應',
    'reviewing': '照護者確認中', 'next_step_pending': '緊急待處理：通知第二聯絡人',
    'handling': '後續處理中', 'resolved': '已結案', 'elder_ok': '長者回應正常',
    'legacy_review': '既有事件，待人工確認',
    'awaiting_secondary': '緊急待處理：等待第二聯絡人回應',
    'medical_pending': '緊急待處理：等待通報醫療機構',
    'medical_notified': '已通報醫療機構，仍待接手',
}


def install_workflow(app, db, lock, auth):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS care_workflows(
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, risk TEXT NOT NULL, stage TEXT NOT NULL,
      created REAL NOT NULL, updated REAL NOT NULL, notified_at REAL, deadline REAL,
      acknowledged_at REAL, owner TEXT, response TEXT NOT NULL DEFAULT '',
      version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS workflow_history(
      id INTEGER PRIMARY KEY, event TEXT NOT NULL, stage TEXT NOT NULL,
      actor TEXT NOT NULL, note TEXT NOT NULL, at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS workflow_commands(
      id TEXT PRIMARY KEY, event TEXT NOT NULL, kind TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', result TEXT, detail TEXT NOT NULL DEFAULT '',
      created REAL NOT NULL, updated REAL NOT NULL);
    ''')
    db.commit()
    if 'secondary_notified_at' not in {r[1] for r in db.execute('PRAGMA table_info(care_workflows)')}:
        db.execute('ALTER TABLE care_workflows ADD COLUMN secondary_notified_at REAL'); db.commit()
    tokens = {kind: os.environ.get(env, '').strip() for kind, env in (
        ('robot', 'GUARDIAN_ROBOT_TOKEN'), ('notify', 'GUARDIAN_NOTIFY_TOKEN'),
        ('next_step', 'GUARDIAN_NOTIFY_TOKEN'), ('medical', 'GUARDIAN_MEDICAL_TOKEN'))}
    app.config['WORKFLOW_TOKENS'] = tokens

    def get(ident):
        row = db.execute('SELECT * FROM care_workflows WHERE id=?', (ident,)).fetchone()
        if not row: abort(404)
        return row

    def history(ident, stage, actor, note, now):
        db.execute('INSERT INTO workflow_history(event,stage,actor,note,at) VALUES(?,?,?,?,?)',
                   (ident, stage, actor, note, now))

    def change(ident, stage, actor, note, now, **fields):
        values = dict(stage=stage, updated=now, **fields)
        db.execute('UPDATE care_workflows SET '+','.join(k+'=?' for k in values)+
                   ',version=version+1 WHERE id=?', (*values.values(), ident))
        history(ident, stage, actor, note, now)

    def command(ident, kind, now):
        db.execute('INSERT INTO workflow_commands(id,event,kind,created,updated) VALUES(?,?,?,?,?)',
                   (str(uuid.uuid4()), ident, kind, now, now))

    def escalate(ident, actor, note, now):
        change(ident, 'next_step_pending', actor, note, now, deadline=None)
        command(ident, 'next_step', now)

    def secondary_wait():
        row=db.execute("SELECT value FROM settings WHERE key='notification_contacts'").fetchone()
        return json.loads(row[0]).get('secondary_wait_seconds',600) if row else 600

    def emergency_contact():
        row=db.execute("SELECT value FROM settings WHERE key='notification_contacts'").fetchone()
        cfg=json.loads(row[0]) if row else {}
        return dict(emergency_phone=cfg.get('emergency_phone','119'),care_address=cfg.get('care_address',''),callback_phone=cfg.get('callback_phone',''))

    def secondary_delivered(ident, actor, detail, now):
        row=get(ident)
        if row['stage']=='next_step_pending':
            wait=secondary_wait()
            change(ident,'awaiting_secondary',actor,'第二聯絡人通知：'+detail,now,
                   secondary_notified_at=now,deadline=now+wait if wait is not None else None)
        else:
            change(ident,row['stage'],actor,'第二聯絡人通知：'+detail,now)

    def tick(now=None):
        now = time.time() if now is None else now
        with lock:
            try:
                db.execute('BEGIN IMMEDIATE')
                expired = db.execute("SELECT id FROM care_workflows WHERE stage='awaiting_caregiver' AND deadline<=?", (now,)).fetchall()
                for row in expired:
                    escalate(row['id'], 'system', '通知送達後 10 分鐘未回應，自動升級。', now)
                wait=secondary_wait()
                if wait is not None:
                    db.execute("UPDATE care_workflows SET deadline=secondary_notified_at+?,version=version+1,updated=? WHERE stage='awaiting_secondary' AND deadline IS NULL AND secondary_notified_at IS NOT NULL",(wait,now))
                expired_secondary=db.execute("SELECT id FROM care_workflows WHERE stage='awaiting_secondary' AND deadline<=?",(now,)).fetchall()
                for row in expired_secondary:
                    change(row['id'],'medical_pending','system','第二聯絡人通知後逾時未接手，排入醫療機構通報，尚未確認送出。',now,deadline=None)
                    command(row['id'],'medical',now)
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.extensions['workflow_tick'] = tick

    def output(row):
        item = dict(row)
        item['label'] = LABELS[row['stage']]
        if row['stage'] in ('medical_pending','medical_notified'):
            item['emergency_contact']=emergency_contact()
            item['automatic_call_configured']=bool(tokens['medical']) and all(emergency_contact().values())
        item['history'] = [dict(r) for r in db.execute('SELECT stage,actor,note,at FROM workflow_history WHERE event=? ORDER BY id', (row['id'],))]
        item['commands'] = [dict(r) for r in db.execute('SELECT id,kind,status,result,detail,updated FROM workflow_commands WHERE event=? ORDER BY created', (row['id'],))]
        cfg=app.extensions['notification_config']()
        recipient=cfg['primary_user'] if row['stage']=='notify_pending' else cfg['secondary_user'] if row['stage']=='next_step_pending' else None
        item['can_receive_in_app']=bool(has_request_context() and recipient and session.get('user')==recipient)
        for cmd in item['commands']:
            cmd['deliveries']=[dict(r) for r in db.execute('SELECT channel,status,detail,updated FROM notification_deliveries WHERE command_id=? ORDER BY channel',(cmd['id'],))]
        return item

    def snapshot():
        return {r['id']: output(r) for r in db.execute('SELECT * FROM care_workflows ORDER BY updated DESC')}
    app.extensions['workflow_snapshot'] = snapshot

    def notification_delivered(command_id, detail):
        now=time.time()
        with lock:
            cmd=db.execute('SELECT * FROM workflow_commands WHERE id=?',(command_id,)).fetchone()
            if not cmd or cmd['status']=='completed': return
            row=get(cmd['event'])
            if cmd['kind']=='notify' and row['stage']=='notify_pending':
                change(row['id'],'awaiting_caregiver','notification',detail,now,notified_at=now,deadline=now+600)
            elif cmd['kind']=='next_step':
                secondary_delivered(row['id'],'notification',detail,now)
            db.execute("UPDATE workflow_commands SET status='completed',result='delivered',detail=?,updated=? WHERE id=?",(detail,now,command_id)); db.commit()
    from .notifications import install_notifications
    install_notifications(app,db,lock,auth,notification_delivered)

    @app.get('/api/workflow/status')
    @auth()
    def integration_status():
        return jsonify(robot_configured=bool(tokens['robot']), notification_configured=bool(tokens['notify']) or app.extensions['notification_ready'](),
                       next_step_configured=bool(tokens['next_step']) or app.extensions['notification_ready'](), timeout_seconds=600,
                       medical_configured=bool(tokens['medical']) and all(emergency_contact().values()), secondary_timeout_seconds=secondary_wait(),
                       note='已設定金鑰不代表設備在線。未串接時，指令會保留待執行；站內通知須保持登入頁面開啟。')

    @app.post('/api/workflow/action')
    @auth(('admin', 'caregiver'))
    def workflow_action():
        body = request.get_json(silent=True)
        if not isinstance(body, dict): abort(400)
        ident = body.get('event'); operation = body.get('action'); note = body.get('note', '')
        if not isinstance(ident, str) or not isinstance(note, str) or len(note)>1000: abort(400)
        now = time.time(); actor = session['user']
        tick(now)
        with lock:
            row = get(ident)
            if body.get('version') != row['version']:
                return jsonify(error='事件已更新，請重新確認目前狀態後再操作。'),409
            stage = row['stage']
            if operation == 'notification_received' and stage in ('notify_pending','next_step_pending'):
                cfg=app.extensions['notification_config']()
                if actor!=cfg['primary_user' if stage=='notify_pending' else 'secondary_user']:abort(403)
                app.extensions['notification_plan']()
                if stage=='notify_pending':
                    change(ident, 'awaiting_caregiver', actor, '指定照護者登入頁面已顯示通知，開始 10 分鐘回應計時。', now,notified_at=now, deadline=now+600)
                else:secondary_delivered(ident,actor,'指定第二聯絡人登入頁面已顯示通知。',now)
                db.execute("UPDATE workflow_commands SET status='completed',result='delivered',detail='站內通知已顯示',updated=? WHERE event=? AND kind=? AND status!='completed'", (now,ident,'notify' if stage=='notify_pending' else 'next_step'))
            elif operation == 'acknowledge' and stage in ('awaiting_caregiver','legacy_review'):
                change(ident, 'reviewing', actor, note or '照護者接手並開始確認，停止未回應倒數。', now,
                       acknowledged_at=now, owner=actor, deadline=None)
            elif operation == 'confirm_issue' and stage == 'reviewing':
                if not note.strip(): return jsonify(error='請填寫確認異常的依據或觀察。'),400
                escalate(ident, actor, '人工確認有異常：'+note, now)
            elif operation == 'confirm_safe' and stage == 'reviewing':
                if not note.strip(): return jsonify(error='請填寫確認無異常的依據。'),400
                change(ident, 'resolved', actor, '人工確認無異常：'+note, now, deadline=None)
            elif operation == 'take_over' and stage in ('next_step_pending','awaiting_secondary','medical_pending','medical_notified'):
                if not note.strip(): return jsonify(error='請填寫實際開始執行的處理方式。'),400
                change(ident, 'handling', actor, '人工接手後續處理：'+note, now, owner=actor,deadline=None,acknowledged_at=now)
            elif operation == 'resolve' and stage == 'handling':
                if not note.strip(): return jsonify(error='請填寫處理結果後再結案。'),400
                change(ident, 'resolved', actor, note, now)
            elif operation == 'record_medical_call' and stage == 'medical_pending':
                if not note.strip():return jsonify(error='請記錄實際通報的對象、時間與通話結果。'),400
                change(ident,'medical_notified',actor,'人工回報已接通救護專線並通報：'+note,now)
                db.execute("UPDATE workflow_commands SET status='completed',result='manual',detail=?,updated=? WHERE event=? AND kind='medical' AND status!='completed'",(note,now,ident))
            else:
                return jsonify(error='目前階段不允許此操作。'),409
            db.commit()
            return jsonify(workflow=output(get(ident)))

    def integration_auth(kind):
        token = tokens[kind]
        supplied = request.headers.get('Authorization', '')
        if not token or not secrets.compare_digest(supplied, 'Bearer '+token): abort(401)

    @app.post('/api/integration/events')
    def detect():
        integration_auth('robot')
        body=request.get_json(silent=True)
        if not isinstance(body,dict): abort(400)
        ident=body.get('event_id'); kind=body.get('kind'); risk=body.get('risk','high')
        if not isinstance(ident,str) or not 1<=len(ident)<=128 or kind not in ('fall','inactivity','abnormal') or risk not in ('high','medium','low'): abort(400)
        ident='robot:'+ident
        now=time.time()
        with lock:
            existing=db.execute('SELECT * FROM care_workflows WHERE id=?',(ident,)).fetchone()
            if existing: return jsonify(workflow=output(existing),duplicate=True)
            db.execute('INSERT INTO care_workflows(id,kind,risk,stage,created,updated) VALUES(?,?,?,?,?,?)',
                       (ident,kind,risk,'call_pending',now,now))
            history(ident,'call_pending','robot','收到異常偵測，已排入呼叫長者指令，尚未確認執行。',now)
            command(ident,'robot',now); db.commit()
            return jsonify(workflow=output(get(ident))),201

    @app.get('/api/integration/commands/<kind>')
    def commands(kind):
        if kind not in tokens: abort(404)
        integration_auth(kind)
        with lock:
            items=[dict(r) for r in db.execute("SELECT * FROM workflow_commands WHERE kind=? AND status!='completed' ORDER BY created LIMIT 100", (kind,))]
            if kind=='medical':
                contact=emergency_contact()
                for item in items:
                    event=get(item['event'])
                    item['payload']=dict(contact,action='call_emergency',kind=event['kind'],created_at=event['created'],device_report=event['response'])
                    item['missing_configuration']=[key for key,value in contact.items() if not value]
            return jsonify(commands=items)

    @app.post('/api/integration/commands/<kind>/<command_id>/result')
    def command_result(kind, command_id):
        if kind not in tokens: abort(404)
        integration_auth(kind)
        body=request.get_json(silent=True)
        if not isinstance(body,dict): abort(400)
        result=body.get('result'); detail=body.get('detail','')
        valid={'robot':('started','normal','no_response','abnormal','failed'),
               'notify':('delivered','failed'), 'next_step':('delivered','failed'), 'medical':('delivered','failed')}
        if result not in valid[kind] or not isinstance(detail,str) or len(detail)>1000: abort(400)
        if kind=='medical' and result=='delivered' and not detail.strip():
            return jsonify(error='請提供實際接通並完成救護通報的紀錄。'),400
        now=time.time()
        with lock:
            cmd=db.execute('SELECT * FROM workflow_commands WHERE id=? AND kind=?',(command_id,kind)).fetchone()
            if not cmd: abort(404)
            if cmd['status']=='completed':
                if cmd['result']==result: return jsonify(ok=True,duplicate=True)
                return jsonify(error='指令已完成，不可重複變更。'),409
            ident=cmd['event']; row=get(ident); stage=row['stage']
            expected={'robot':('call_pending','calling'), 'notify':('notify_pending',), 'next_step':('next_step_pending','handling','resolved'), 'medical':('medical_pending','handling','resolved')}
            if stage not in expected[kind]: return jsonify(error='事件已進入其他階段。'),409
            status='completed'
            if result=='failed' and kind!='robot':
                status='failed'
                history(ident,stage,kind,'執行失敗，尚未送達／開始：'+detail,now)
                db.execute('UPDATE care_workflows SET updated=?,version=version+1 WHERE id=?',(now,ident))
            elif kind=='robot':
                if result=='started':
                    if stage=='calling': return jsonify(ok=True,duplicate=True)
                    status='in_progress'
                    change(ident,'calling','robot','設備回報已開始呼叫長者。',now)
                elif result=='normal':
                    if stage!='calling': return jsonify(error='請先回報呼叫已開始。'),409
                    change(ident,'elder_ok','robot','設備回報長者回應正常：'+detail,now,response=detail)
                else:
                    if result!='failed' and stage!='calling': return jsonify(error='請先回報呼叫已開始。'),409
                    reason={'no_response':'呼叫後無回應','abnormal':'回應異常','failed':'呼叫失敗，無法確認狀況'}[result]
                    change(ident,'notify_pending','robot',reason+'，等待通知照護者。'+detail,now,response=reason+'：'+detail)
                    command(ident,'notify',now)
            elif kind=='notify':
                change(ident,'awaiting_caregiver','notify','通知服務確認送達：'+detail,now,notified_at=now,deadline=now+600)
            elif kind=='next_step':
                secondary_delivered(ident,'next_step',detail,now)
            elif kind=='medical':
                change(ident,'medical_notified' if stage=='medical_pending' else stage,'medical','醫療機構通報管道回報已送出（不代表已派員）：'+detail,now)
            db.execute('UPDATE workflow_commands SET status=?,result=?,detail=?,updated=? WHERE id=?',
                       (status,result,detail,now,command_id)); db.commit()
            return jsonify(ok=True,workflow=output(get(ident)))

    def import_legacy(records):
        with lock:
            now=time.time()
            for event in records:
                terminal=event['status'] in ('resolved','cancelled','canceled','closed','cleared','recovered')
                urgent=(event['kind'] in ('fall','tracking_review','prolonged_sitting') and
                        event['status'] in ('pending','review','alert'))
                existing=db.execute('SELECT * FROM care_workflows WHERE id=?',(event['id'],)).fetchone()
                if existing:
                    if terminal and existing['stage'] not in TERMINAL|{'resolved'}:
                        change(event['id'],'resolved','source','來源照護事件已解除或取消。',now,deadline=None)
                    elif urgent and existing['stage']=='legacy_review':
                        change(event['id'],'notify_pending','source','收到疑似跌倒或久坐紅色警示，立即通知主要照護者。',now,risk='high')
                        command(event['id'],'notify',now)
                    continue
                stage='resolved' if terminal else 'notify_pending' if urgent else 'legacy_review'
                risk='high' if urgent else event.get('risk_level') or 'medium'
                db.execute('INSERT INTO care_workflows(id,kind,risk,stage,created,updated) VALUES(?,?,?,?,?,?)',
                           (event['id'],event['kind'],risk,stage,event['created_wall'],now))
                if urgent:
                    history(event['id'],stage,'source','收到疑似跌倒或久坐紅色警示，立即通知主要照護者。',now)
                    command(event['id'],'notify',now)
                else:
                    history(event['id'],stage,'system','匯入既有資料庫事件；來源未提供機器狗呼叫與語音回應紀錄，請人工確認。',now)
            db.commit()
    app.extensions['workflow_import_legacy']=import_legacy
