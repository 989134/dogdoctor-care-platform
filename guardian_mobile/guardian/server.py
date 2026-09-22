"""Authenticated caregiver portal. Local source databases are read-only."""
import functools
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import hashlib
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
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
    public_url=os.environ.get('GUARDIAN_PUBLIC_URL','').strip().rstrip('/')
    parsed=urlsplit(public_url)
    if public_url and (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('GUARDIAN_PUBLIC_URL must be an HTTPS origin without a path')
    app.config.update(DATA_DIR=str(data),PUBLIC_URL=public_url,SESSION_COOKIE_SECURE=bool(public_url))
    app.config.update(MAX_CONTENT_LENGTH=8*1024*1024, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=3600)
    gemini_key_file = data/'gemini-api-key.txt'
    app.config['GEMINI_API_KEY'] = (os.environ.get('GEMINI_API_KEY', '').strip()
        or (gemini_key_file.read_text(encoding='utf-8').strip() if gemini_key_file.is_file() else ''))
    gemini_model_file = data/'gemini-model.txt'
    app.config['GEMINI_MODEL'] = (os.environ.get('GEMINI_MODEL', '').strip()
        or (gemini_model_file.read_text(encoding='utf-8').strip() if gemini_model_file.is_file() else 'gemini-flash-lite-latest'))
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
        if not db.execute('SELECT 1 FROM users').fetchone() and not (data/'setup-token.txt').exists(): (data/'setup-token.txt').write_text(bootstrap)
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
    from .workflow import install_workflow
    install_workflow(app, db, lock, auth)
    from .intercom import install_intercom
    install_intercom(app, db, lock, auth)
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
    @app.get('/sw.js')
    def service_worker():
        response=app.send_static_file('sw.js')
        response.headers['Service-Worker-Allowed']='/'
        return response
    def retry_seconds(address, now):
        attempts[address]=[t for t in attempts.get(address,[]) if now-t<300]
        return max(1,int(300-(now-attempts[address][0]))+1) if len(attempts[address])>=10 else 0
    @app.get('/api/auth/status')
    def auth_status():
        with lock:
            setup_required=not bool(db.execute('SELECT 1 FROM users').fetchone())
            remaining=retry_seconds(request.remote_addr,time.time())
        return jsonify(setup_required=setup_required,retry_after=remaining)
    @app.post('/api/login')
    def login():
        if not request.is_json: abort(400)
        body=request.get_json()
        if not isinstance(body,dict): abort(400)
        name=body.get('name',''); password=body.get('password',''); setup=body.get('setup','')
        if not all(isinstance(v,str) for v in (name,password,setup)): abort(400)
        name=name.strip(); setup=setup.strip()
        if not 1<=len(name)<=64: return jsonify(error='請輸入帳號（1～64 個字元）。'),400
        if not password or len(password)>256: return jsonify(error='請輸入密碼（最多 256 個字元）。'),400
        address=request.remote_addr; now=time.time()
        with lock:
            first=not bool(db.execute('SELECT 1 FROM users').fetchone())
            if first:
                if len(password)<12: return jsonify(error='建立帳號的密碼至少需要 12 個字元，請增加長度後再試。'),400
                if not setup: return jsonify(error='首次使用需要本機設定碼。請選取 setup-token.txt，或使用本機設定連結。'),400
                token=data/'setup-token.txt'
                # Possession of the local bootstrap secret permits recovery from
                # failed setup attempts without removing the normal login limit.
                valid=token.exists() and secrets.compare_digest(setup,token.read_text().strip())
                if not valid:
                    remaining=retry_seconds(address,now)
                    if remaining:
                        return jsonify(error=f'設定碼錯誤次數過多，請於 {remaining} 秒後重試，或使用正確的本機設定連結。',retry_after=remaining),429,{'Retry-After':str(remaining)}
                    attempts[address].append(now)
                    return jsonify(error='設定碼不正確。這不是您的密碼，請使用本機 setup-token.txt 的內容。'),403
                db.execute('INSERT INTO users VALUES(?,?,?)',(name,generate_password_hash(password),'admin')); db.commit(); token.unlink(missing_ok=True)
            else:
                remaining=retry_seconds(address,now)
                if remaining:
                    return jsonify(error=f'登入失敗次數過多，請於 {remaining} 秒後重試。',retry_after=remaining),429,{'Retry-After':str(remaining)}
            user=db.execute('SELECT * FROM users WHERE name=?',(name,)).fetchone()
            if not user or not check_password_hash(user['password'],password):
                attempts.setdefault(address,[]).append(now)
                return jsonify(error='帳號或密碼不正確，請使用此網頁自行建立的帳號。'),401
            session.clear(); session.update(user=name,csrf=secrets.token_urlsafe(24)); session.permanent=True
            session['credential']=hashlib.sha256(user['password'].encode()).hexdigest()
            audit('login'); attempts[address]=[]
            return jsonify(user=name,role=user['role'],csrf=session['csrf'])
    @app.get('/api/me')
    @auth()
    def me():
        with lock: role=db.execute('SELECT role FROM users WHERE name=?',(session['user'],)).fetchone()[0]
        return jsonify(user=session['user'],role=role,csrf=session['csrf'])
    @app.post('/api/logout')
    @auth()
    def logout():
        with lock:
            db.execute('DELETE FROM push_subscriptions WHERE user=? AND endpoint=?',(session['user'],session.get('push_endpoint','')));db.commit()
        session.clear(); return jsonify(ok=True)
    @app.post('/api/password')
    @auth()
    def password_change():
        b=request.get_json(); new=str(b.get('new',''))
        if not 12<=len(new)<=256: return jsonify(error='新密碼需為 12～256 個字元'),400
        with lock:
            old=db.execute('SELECT password FROM users WHERE name=?',(session['user'],)).fetchone()[0]
            if not check_password_hash(old,str(b.get('current',''))): return jsonify(error='目前密碼不正確'),400
            db.execute('UPDATE users SET password=? WHERE name=?',(generate_password_hash(new),session['user'])); db.commit()
            db.execute('DELETE FROM push_subscriptions WHERE user=?',(session['user'],));db.commit()
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
    def source_sync():
        records=events()
        app.extensions['workflow_import_legacy'](records)
        app.extensions['notification_plan']()
        return len(records)
    app.extensions['guardian_source_sync']=source_sync
    @app.get('/api/events')
    @auth()
    def event_list():
        app.extensions['workflow_tick']()
        with lock:
            cleanup(); output=[]
            records=events()
            app.extensions['workflow_import_legacy'](records)
            workflows=app.extensions['workflow_snapshot']()
            for e in records:
                p=image_path(e); e.pop('snapshot_path',None); e['image_available']=bool(p and p.exists())
                e['actions']=[dict(r) for r in db.execute('SELECT user,status,note,at FROM actions WHERE event=? ORDER BY id',(e['id'],))]
                e['workflow']=workflows.pop(e['id'],None)
                output.append(e)
            for ident,w in workflows.items():
                output.append(dict(id=ident,kind=w['kind'],status=w['stage'],risk_level=w['risk'],
                    created_wall=w['created'],updated_wall=w['updated'],image_available=False,actions=[],workflow=w))
            return jsonify(events=output,server_time=time.time())
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
        return jsonify(error='請使用新版事件處理步驟，重新整理網頁後操作。'),409
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
            db.execute('DELETE FROM push_subscriptions WHERE user IN (SELECT name FROM users WHERE name=? AND role!=?)',(name,'admin'))
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
    ai_slots=threading.BoundedSemaphore(2)
    ai_attempts={}
    @app.get('/api/drug/config')
    @auth()
    def drug_config():
        return jsonify(gemini_available=bool(app.config['GEMINI_API_KEY']))

    @app.get('/api/drug/demo')
    @auth(('admin','caregiver'))
    def drug_demo():
        """Return a complete, deterministic medicine result for recorded demos.

        Prescription-only values are explicitly labelled as simulated so this
        result cannot be mistaken for advice extracted from the package photo.
        """
        product_name='普拿疼膜衣錠500毫克'
        english_name='PANADOL FILM COATED CAPLETS 500MG'
        indication='退燒、止痛（緩解頭痛、牙痛、咽喉痛、關節痛、神經痛、肌肉酸痛及月經痛）。'
        dosage='飯後一次 1 錠，每日 3 次；實際用法請依個人藥袋或藥師指示。'
        expiry='2027-12-31'
        license_number='衛署藥輸字第023624號'
        text='\n'.join((product_name,english_name,'Acetaminophen 500 mg',license_number))
        record={'中文品名':product_name,'英文品名':english_name,'許可證字號':license_number}
        details={
            'product_name':product_name,
            'english_name':english_name,
            'purpose_plain':indication,
            'indication':indication,
            'strength':'Acetaminophen（乙醯胺酚）500 毫克／錠',
            'form':'膜衣錠',
            'ingredients':'Acetaminophen（Paracetamol，乙醯胺酚）',
            'dosage_text':dosage,
            'expiry_text':expiry,
            'license':license_number,
            'manufacturer':'申請商：英商赫力昂股份有限公司台灣分公司；製造廠：STERLING DRUG (MALAYA) SDN. BHD.',
            'source':'衛生福利部食品藥物管理署公開資料',
            'warning':'辨識內容僅供核對，實際用藥請以原藥袋及藥師指示為準。'
        }
        ai={
            'text':text,'name':product_name,
            'strength':'Acetaminophen（乙醯胺酚）500 毫克／錠',
            'license':license_number,'dosage_text':dosage,'expiry_text':expiry,
            'uncertainty':'請人工核對原藥袋上的品名、含量、服用方式與有效期限。'
        }
        with lock: audit('drug_demo','panadol_500mg')
        return jsonify(
            text=text,matches=[[1.0,'demo',record]],details=details,ai=ai,
            provider='demo',demo=True,image_url='/static/demo_panadol_500mg.jpg',
            attribution_url='https://commons.wikimedia.org/wiki/File:Panadol_500_mg.jpg',
            needs_confirmation=True,
            message='Demo 固定範例已載入。模擬用法與效期僅供展示，不能作為用藥指示。')

    @app.post('/api/drug')
    @auth(('admin','caregiver'))
    def drug():
        nonlocal catalog,ocr
        from .drug.drug_reader import Catalog,recognize
        from PIL import Image,ImageOps
        from .gemini import extract_label, GeminiError
        from .medicine_info import medicine_info
        provider=request.form.get('provider','local')
        if provider not in ('local','gemini'): abort(400)
        text=request.form.get('text','').strip()
        if len(text)>4000: return jsonify(error='文字請限制在 4000 字以內。'),400
        if provider=='gemini' and request.form.get('cloud_consent')!='true':
            return jsonify(error='請先同意將本次圖片與文字傳送至 Google Gemini。'),400
        image_bytes=None
        if 'image' in request.files:
            try:
                with Image.open(request.files['image'].stream) as src:
                    if src.width*src.height>16000000: return jsonify(error='圖片超過 1600 萬像素，請縮小後再上傳。'),400
                    pic=ImageOps.exif_transpose(src).convert('RGB'); pic.thumbnail((1800,1800))
                    buf=io.BytesIO(); pic.save(buf,format='PNG'); image_bytes=buf.getvalue()
            except (OSError,ValueError,Image.DecompressionBombError):
                return jsonify(error='無法讀取圖片，請使用 JPG、PNG 或 WebP 圖片。'),400
        if not text and image_bytes is None: return jsonify(error='請上傳藥袋照片或輸入藥品文字。'),400
        ai=None
        if provider=='gemini':
            if not app.config['GEMINI_API_KEY']:
                return jsonify(error='尚未設定 Gemini API 金鑰，請改用本機辨識。'),503
            with lock:
                now=time.monotonic(); name=session['user']
                ai_attempts[name]=[t for t in ai_attempts.get(name,[]) if now-t<60]
                if len(ai_attempts[name])>=6: abort(429)
                ai_attempts[name].append(now)
            if not ai_slots.acquire(blocking=False):
                return jsonify(error='辨識服務忙碌中，請稍後再試。'),429
            try:
                ai=extract_label(text,image_bytes,app.config['GEMINI_API_KEY'],app.config['GEMINI_MODEL'])
                text=ai['text']
            except GeminiError as exc:
                return jsonify(error=str(exc)),502
            finally:
                ai_slots.release()
        with drug_lock:
            if catalog is None:
                base=Path(__file__).with_name('drug'); catalog=Catalog(base/'36_1.xml',next(base.glob('*.xlsx')))
            if provider=='local' and image_bytes:
                recognized,ocr=recognize(io.BytesIO(image_bytes),ocr)
                text='\n'.join(filter(None,[text,recognized]))
            query='\n'.join(ai[k] for k in ('name','strength','license')) if ai else text
            matches=catalog.search(query) if query.strip() else []
        details=medicine_info(text,matches)
        if ai:
            details['dosage_text']=ai['dosage_text'] or 'AI 未讀到明確用法，請核對原藥袋。'
            details['expiry_text']=ai['expiry_text'] or 'AI 未讀到明確包裝效期，請補拍效期標籤。'
        with lock: audit('drug_lookup',provider)
        return jsonify(text=text,matches=matches,details=details,ai=ai,provider=provider,
                       needs_confirmation=True,message='請核對完整品名、含量及許可證；AI 擷取與資料庫匹配都不代表已確認藥品，結果不是用藥指示。')
    return app
