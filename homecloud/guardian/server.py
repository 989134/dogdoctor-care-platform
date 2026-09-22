"""Authenticated caregiver portal. Local source databases are read-only."""
import functools
import io
import json
import secrets
import sqlite3
import threading
import time
import hashlib
from datetime import date
from pathlib import Path
from flask import Flask, abort, jsonify, request, session, send_file, has_request_context
from werkzeug.security import generate_password_hash, check_password_hash

ROOT = Path(__file__).resolve().parents[1]

def create_app(data_dir=None, source_dir=None):
    data = Path(data_dir or ROOT/'guardian_data'); data.mkdir(parents=True, exist_ok=True)
    sources = Path(source_dir or ROOT/'mec_output').resolve()
    key = data/'session.key'
    if not key.exists(): key.write_text(secrets.token_hex(32))
    app = Flask(__name__)
    app.secret_key = key.read_text()
    app.config.update(MAX_CONTENT_LENGTH=8*1024*1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=3600)
    lock = threading.RLock()
    db = sqlite3.connect(data/'portal.sqlite3', check_same_thread=False)
    db.row_factory = sqlite3.Row
    app.extensions['guardian_db']=db
    db.executescript('''CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY,password TEXT,role TEXT);
    CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY, event TEXT,user TEXT,status TEXT,note TEXT,at REAL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user TEXT,action TEXT,target TEXT,at REAL);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS medicines(id INTEGER PRIMARY KEY,name TEXT,directions TEXT,expiry TEXT,owner TEXT,active INTEGER,updated REAL);
    INSERT OR IGNORE INTO settings VALUES('retention_days','30');'''); db.commit()
    bootstrap = secrets.token_urlsafe(24)
    with lock:
        if not db.execute('SELECT 1 FROM users').fetchone(): (data/'setup-token.txt').write_text(bootstrap)
    attempts = {}
    def audit(action,target=''):
        db.execute('INSERT INTO audit(user,action,target,at) VALUES(?,?,?,?)',
                   (session.get('user','anonymous') if has_request_context() else 'system',action,target,time.time())); db.commit()
    def auth(role=None):
        def decorate(fn):
            @functools.wraps(fn)
            def wrapped(*args,**kwargs):
                with lock: user=db.execute('SELECT role,password FROM users WHERE name=?',(session.get('user'),)).fetchone()
                if not user: abort(401)
                if session.get('credential')!=hashlib.sha256(user['password'].encode()).hexdigest(): abort(401)
                if role and user['role'] not in role: abort(403)
                if request.method!='GET' and not secrets.compare_digest(request.headers.get('X-CSRF',''),session.get('csrf','missing')): abort(403)
                return fn(*args,**kwargs)
            return wrapped
        return decorate
    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        return response
    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def error(e): return jsonify(error={401:'請先登入',403:'沒有權限或請重新登入',429:'嘗試次數過多，請稍後再試'}.get(e.code,'請求無效')),e.code
    @app.get('/')
    def page(): return Path(__file__).with_name('page.html').read_text(encoding='utf-8')
    @app.post('/api/login')
    def login():
        if not request.is_json: abort(400)
        body=request.get_json(); name=str(body.get('name','')); password=str(body.get('password',''))
        address=request.remote_addr; now=time.time()
        attempts[address]=[t for t in attempts.get(address,[]) if now-t<300]
        if len(attempts[address])>=10: abort(429)
        attempts[address].append(now)
        with lock:
            if not db.execute('SELECT 1 FROM users').fetchone():
                token=data/'setup-token.txt'
                if not token.exists() or not secrets.compare_digest(str(body.get('setup','')),token.read_text()): abort(403)
                if not 1<=len(name)<=64 or not 12<=len(password)<=256: abort(400)
                db.execute('INSERT INTO users VALUES(?,?,?)',(name,generate_password_hash(password),'admin')); db.commit(); token.unlink()
            user=db.execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
            if not user or not check_password_hash(user['password'],password): abort(401)
            session.clear(); session.update(user=name,csrf=secrets.token_urlsafe(24)); session.permanent=True
            session['credential']=hashlib.sha256(user['password'].encode()).hexdigest()
            audit('login'); attempts[address]=[]
            return jsonify(user=name,role=user['role'],csrf=session['csrf'])
    @app.get('/api/me')
    @auth()
    def me():
        with lock: role=db.execute('SELECT role FROM users WHERE name=?',(session['user'],)).fetchone()[0]
        return jsonify(user=session['user'],role=role,csrf=session['csrf'])
    @app.route('/api/privacy',methods=['GET','POST'])
    @auth(('admin','caregiver','viewer'))
    def privacy():
        path=data/'privacy.json'
        if request.method=='POST':
            with lock:
                role=db.execute('SELECT role FROM users WHERE name=?',(session['user'],)).fetchone()[0]
                if role=='viewer':abort(403)
                mode=request.get_json().get('mode')
                if mode not in ('auto','skeleton'):abort(400)
                value=dict(mode=mode,private=True,location_verified=False,updated=time.time())
                temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value),encoding='utf-8');temporary.replace(path)
                audit('privacy_mode',mode)
        try: value=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError): value={'mode':'skeleton'}
        return jsonify(mode=value.get('mode','skeleton'),message='位置未驗證時使用骨架畫面；實車區域定位尚未介接。新照護截圖也使用骨架，舊影像不會自動改寫。')
    @app.post('/api/logout')
    @auth()
    def logout(): session.clear(); return jsonify(ok=True)
    @app.post('/api/password')
    @auth()
    def password_change():
        b=request.get_json(); new=str(b.get('new',''))
        if not 12<=len(new)<=256: return jsonify(error='新密碼需為 12～256 個字元'),400
        with lock:
            old=db.execute('SELECT password FROM users WHERE name=?',(session['user'],)).fetchone()[0]
            if not check_password_hash(old,str(b.get('current',''))): return jsonify(error='目前密碼不正確'),400
            db.execute('UPDATE users SET password=? WHERE name=?',(generate_password_hash(new),session['user'])); db.commit()
            audit('password_change'); session.clear()
        return jsonify(ok=True)
    def events():
        result=[]
        for path in sources.glob('*.sqlite3'):
            try:
                connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True); connection.row_factory=sqlite3.Row
                try: rows=connection.execute('SELECT event_uid,kind,status,risk_level,created_wall,updated_wall,snapshot_path FROM care_events ORDER BY updated_wall DESC').fetchall()
                finally: connection.close()
                for row in rows:
                    entry=dict(row); entry['id']=path.stem+':'+entry['event_uid']; result.append(entry)
            except sqlite3.Error: continue
        with lock:
            for r in db.execute('SELECT * FROM demo_events'):
                result.append(dict(id=r['id'],event_uid=r['id'],kind='fall',status='review',risk_level='medium',created_wall=r['created'],updated_wall=r['created'],snapshot_path=None,demo=True,location_label=r['label']))
        return sorted(result,key=lambda r:r['updated_wall'],reverse=True)
    def image_path(event):
        if not event.get('snapshot_path'): return None
        p=Path(event['snapshot_path']).resolve()
        if not p.is_relative_to(sources) or p.suffix.lower() not in ('.jpg','.jpeg','.png'): return None
        return p
    def cleanup():
        days=int(db.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0])
        records=events()
        protected={image_path(e) for e in records if time.time()-e['created_wall']<=days*86400}
        for event in records:
            p=image_path(event)
            if time.time()-event['created_wall']>days*86400 and p and p not in protected and p.exists():
                p.unlink(); audit('retention_delete',event['id'])
    def maintenance():
        with lock: cleanup()
    app.extensions['guardian_maintenance']=maintenance
    @app.get('/api/events')
    @auth()
    def event_list():
        with lock:
            cleanup(); output=[]
            for e in events():
                p=image_path(e); e.pop('snapshot_path',None); e['image_available']=bool(p and p.exists())
                e['actions']=[dict(r) for r in db.execute('SELECT user,status,note,at FROM actions WHERE event=? ORDER BY id',(e['id'],))]
                review=db.execute('SELECT * FROM event_reviews WHERE event=?',(e['id'],)).fetchone()
                e['review']=dict(review) if review else None
                output.append(e)
            return jsonify(events=output)
    @app.get('/api/image/<path:event_id>')
    @auth()
    def image(event_id):
        with lock:
            cleanup(); event=next((e for e in events() if e['id']==event_id),None)
            p=image_path(event) if event else None
            if not p or not p.is_file(): abort(404)
            audit('view_image',event_id)
            return send_file(p)
    @app.post('/api/action')
    @auth(('admin','caregiver'))
    def action():
        body=request.get_json(); state=body.get('status'); event=body.get('event')
        if state not in ('acknowledged','handling','resolved') or not any(e['id']==event for e in events()): abort(400)
        with lock:
            db.execute('INSERT INTO actions(event,user,status,note,at) VALUES(?,?,?,?,?)',(event,session['user'],state,str(body.get('note',''))[:1000],time.time())); db.commit(); audit(state,event)
        return jsonify(ok=True)
    @app.route('/api/users',methods=['GET','POST'])
    @auth(('admin',))
    def users():
        with lock:
            if request.method=='POST':
                b=request.get_json(); name=str(b.get('name','')); pw=str(b.get('password','')); role=b.get('role')
                if not 1<=len(name)<=64 or not 12<=len(pw)<=256 or role not in ('caregiver','viewer'): abort(400)
                try: db.execute('INSERT INTO users VALUES(?,?,?)',(name,generate_password_hash(pw),role)); db.commit()
                except sqlite3.IntegrityError: abort(400)
                audit('create_user',name)
            return jsonify(users=[dict(r) for r in db.execute('SELECT name,role FROM users')])
    @app.post('/api/users/remove')
    @auth(('admin',))
    def remove_user():
        name=request.get_json().get('name')
        if name==session['user']: abort(400)
        with lock:
            db.execute('DELETE FROM users WHERE name=? AND role!=?',(name,'admin')); db.commit()
            audit('remove_user',str(name))
        return jsonify(ok=True)
    @app.route('/api/retention',methods=['GET','POST'])
    @auth(('admin',))
    def retention():
        with lock:
            if request.method=='POST':
                days=request.get_json().get('days')
                if not isinstance(days,int) or not 1<=days<=365: abort(400)
                db.execute("UPDATE settings SET value=? WHERE key='retention_days'",(str(days),)); db.commit(); audit('retention_change',str(days)); cleanup()
            return jsonify(days=int(db.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0]))
    @app.get('/api/audit')
    @auth(('admin',))
    def logs():
        with lock: return jsonify(logs=[dict(r) for r in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')])
    @app.get('/api/medicines')
    @auth()
    def medicines_list():
        with lock:
            return jsonify(medicines=[dict(r) for r in db.execute('SELECT * FROM medicines ORDER BY active DESC,updated DESC')])
    @app.post('/api/medicines')
    @auth(('admin','caregiver'))
    def medicines_save():
        b=request.get_json()
        name=str(b.get('name','')).strip(); directions=str(b.get('directions','')).strip(); expiry=str(b.get('expiry',''))
        if not b.get('confirmed') is True or not 1<=len(name)<=200 or not 1<=len(directions)<=1000: abort(400)
        if expiry:
            try: date.fromisoformat(expiry)
            except ValueError: abort(400)
        with lock:
            db.execute('INSERT INTO medicines(name,directions,expiry,owner,active,updated) VALUES(?,?,?,?,1,?)',(name,directions,expiry,session['user'],time.time())); db.commit(); audit('medicine_save',name)
        return jsonify(ok=True)
    @app.post('/api/medicines/archive')
    @auth(('admin','caregiver'))
    def medicine_archive():
        with lock:
            ident=request.get_json().get('id')
            if not isinstance(ident,int): abort(400)
            db.execute('UPDATE medicines SET active=0,updated=? WHERE id=?',(time.time(),ident)); db.commit(); audit('medicine_archive',str(ident))
        return jsonify(ok=True)
    catalog=None; ocr=None; drug_lock=threading.Lock()
    @app.post('/api/drug')
    @auth(('admin','caregiver'))
    def drug():
        nonlocal catalog,ocr
        from .drug.drug_reader import Catalog,recognize
        from PIL import Image,ImageOps
        with drug_lock:
            if catalog is None:
                base=Path(__file__).with_name('drug'); catalog=Catalog(base/'36_1.xml',next(base.glob('*.xlsx')))
            text=request.form.get('text','')[:4000]
            if 'image' in request.files:
                try:
                    with Image.open(request.files['image'].stream) as src:
                        if src.width*src.height>16000000: abort(400)
                        pic=ImageOps.exif_transpose(src).convert('RGB'); pic.thumbnail((1800,1800)); buf=io.BytesIO(); pic.save(buf,format='PNG'); buf.seek(0)
                    text,ocr=recognize(buf,ocr)
                except (OSError,ValueError): abort(400)
            if not text.strip(): abort(400)
            matches=catalog.search(text)
            from .medicine_info import medicine_info
            with lock: audit('drug_lookup')
            return jsonify(text=text,matches=matches,details=medicine_info(text,matches),needs_confirmation=True,message='請核對完整品名、劑量及許可證；此結果不是用藥指示。')
    from .areas import register_areas
    register_areas(app,db,lock,auth,audit,events)
    from .analytics import register_analytics
    register_analytics(app,db,lock,auth,events)
    return app
