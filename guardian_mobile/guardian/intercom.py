"""Authenticated WebRTC signaling; audio stays between the two endpoints."""
import hashlib
import json
import os
import secrets
import time
from time import monotonic
from flask import abort,jsonify,request,session


def load_ice_servers(raw):
    try:
        value=json.loads(raw or '[]')
    except json.JSONDecodeError as exc:
        raise ValueError('GUARDIAN_ICE_SERVERS must be valid JSON') from exc
    if not isinstance(value,list) or len(value)>8:
        raise ValueError('GUARDIAN_ICE_SERVERS must be a JSON array with at most 8 entries')
    normalized=[]
    for entry in value:
        if not isinstance(entry,dict):
            raise ValueError('each ICE server must be a JSON object')
        urls=entry.get('urls')
        url_list=[urls] if isinstance(urls,str) else urls
        if (not isinstance(url_list,list) or not 1<=len(url_list)<=8 or
                not all(isinstance(url,str) and 1<=len(url)<=2048 and
                        url.startswith(('stun:','stuns:','turn:','turns:')) for url in url_list)):
            raise ValueError('ICE server urls must use stun, stuns, turn, or turns')
        server={'urls':urls}
        for key in ('username','credential'):
            if key in entry:
                if not isinstance(entry[key],str) or len(entry[key])>512:
                    raise ValueError(f'ICE server {key} must be a string of at most 512 characters')
                server[key]=entry[key]
        normalized.append(server)
    return normalized


def install_intercom(app,db,lock,auth):
    state={'device':None,'seen':0,'call':None}
    ice=load_ice_servers(os.environ.get('GUARDIAN_ICE_SERVERS','[]'))
    def expire():
        now=monotonic();call=state['call']
        if now-state['seen']>20:state['device']=None
        if call and (not state['device'] or now-call['seen']>20 or (not call['answer'] and now-call['created']>60)):
            state['call']=None
    def device_auth():
        supplied=request.headers.get('X-Vehicle-Key','')
        with lock:row=db.execute("SELECT value FROM settings WHERE key='intercom_device_hash'").fetchone()
        if not row or not supplied or not secrets.compare_digest(row[0],hashlib.sha256(supplied.encode()).hexdigest()):abort(401)
    def body():
        value=request.get_json(silent=True)
        if not isinstance(value,dict):abort(400)
        return value
    def description(value,kind):
        if not isinstance(value,dict) or value.get('type')!=kind or not isinstance(value.get('sdp'),str) or not 1<=len(value['sdp'])<=65536:abort(400)
        return {'type':kind,'sdp':value['sdp']}
    def owned(ident):
        expire();call=state['call']
        if not call or call['id']!=ident:abort(404)
        if call['user']!=session['user'] or call['session']!=session['csrf']:abort(403)
        return call

    @app.get('/vehicle')
    def vehicle_page():return app.send_static_file('vehicle.html')

    @app.post('/api/intercom/pair')
    @auth(('admin',))
    def pair():
        token=secrets.token_urlsafe(32)
        with lock:
            db.execute("INSERT OR REPLACE INTO settings VALUES('intercom_device_hash',?)",(hashlib.sha256(token.encode()).hexdigest(),));db.commit()
            state.update(device=None,seen=0,call=None)
        return jsonify(token=token)

    @app.get('/api/intercom/status')
    @auth(('admin','caregiver'))
    def status():
        with lock:
            expire()
            paired=bool(db.execute("SELECT 1 FROM settings WHERE key='intercom_device_hash'").fetchone())
            return jsonify(paired=paired,online=bool(state['device']),busy=bool(state['call']),ice_servers=ice if session.get('user') and not state['call'] else [],turn_configured=any(url.startswith(('turn:','turns:')) for s in ice for url in ([s['urls']] if isinstance(s['urls'],str) else s['urls'])))

    @app.post('/api/intercom/calls')
    @auth(('admin','caregiver'))
    def start():
        offer=description(body().get('offer'),'offer')
        with lock:
            expire()
            if not state['device']:return jsonify(error='車端尚未啟用或已離線。'),409
            if state['call']:return jsonify(error='已有照護者正在通話，請稍後再試。'),409
            ident=secrets.token_urlsafe(18)
            state['call']=dict(id=ident,user=session['user'],session=session['csrf'],offer=offer,answer=None,created=monotonic(),seen=monotonic())
            db.execute('INSERT INTO audit(user,action,target,at) VALUES(?,?,?,?)',(session['user'],'intercom_start',ident,time.time()));db.commit()
            return jsonify(id=ident),201

    @app.route('/api/intercom/calls/<ident>',methods=['GET','POST'])
    @auth(('admin','caregiver'))
    def call(ident):
        with lock:
            current=owned(ident)
            if request.method=='POST':
                if body().get('action')!='end':abort(400)
                state['call']=None;return jsonify(ok=True)
            current['seen']=monotonic()
            return jsonify(answer=current['answer'])

    @app.post('/api/intercom/device')
    def intercom_device():
        device_auth();value=body();instance=value.get('instance')
        if not isinstance(instance,str) or not 10<=len(instance)<=100:abort(400)
        with lock:
            expire()
            if state['device'] and state['device']!=instance:return jsonify(error='另一個車端接收頁已啟用，請先停止原接收頁。'),409
            if value.get('action')=='leave':
                state.update(device=None,seen=0,call=None);return jsonify(ok=True)
            state.update(device=instance,seen=monotonic())
            current=state['call']
            if value.get('answer') is not None:
                if not current or current['id']!=value.get('id'):abort(409)
                answer=description(value['answer'],'answer')
                if current['answer'] and current['answer']!=answer:abort(409)
                current['answer']=answer
            if value.get('action')=='end' and current and current['id']==value.get('id'):state['call']=None
            current=state['call']
            return jsonify(call={k:current[k] for k in ('id','user','offer')} if current else None,ice_servers=ice)

