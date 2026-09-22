"""Manually reviewed location evidence; never equates robot pose with patient pose."""
import time
import uuid
from flask import request, session, jsonify, abort

DEMO_POINTS=[('A','客廳沙發旁','起身、坐下及久未活動',20),('B','浴室門外','門口通行與疑似跌倒',15),('C','臥室門口','出入動態與異常姿態',20),('D','餐廳走道','通道障礙與人員活動',15)]
DEMO_VERSION='guardian-demo-home-v1'

def register_areas(app, db, lock, auth, audit, events):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS areas(id INTEGER PRIMARY KEY,name TEXT,floor TEXT,map_version TEXT);
    CREATE TABLE IF NOT EXISTS event_reviews(event TEXT PRIMARY KEY,area INTEGER,verdict TEXT,context TEXT,note TEXT,reviewer TEXT,updated REAL);
    CREATE TABLE IF NOT EXISTS area_followups(id INTEGER PRIMARY KEY,area INTEGER,observation TEXT,measure TEXT,owner TEXT,state TEXT,result TEXT,author TEXT,at REAL);
    CREATE TABLE IF NOT EXISTS area_visits(id INTEGER PRIMARY KEY,area INTEGER,context TEXT,author TEXT,at REAL);
    CREATE TABLE IF NOT EXISTS demo_events(id TEXT PRIMARY KEY,created REAL,label TEXT);
    '''); db.commit()
    with lock:
        for code,name,focus,seconds in DEMO_POINTS:
            if not db.execute('SELECT 1 FROM areas WHERE name=? AND map_version=?',(name,DEMO_VERSION)).fetchone():
                db.execute('INSERT INTO areas(name,floor,map_version) VALUES(?,?,?)',(name,'示範配置',DEMO_VERSION))
        db.commit()
    @app.get('/api/demo-patrol')
    @auth()
    def demo_patrol():
        with lock:
            points=[]
            for code,name,focus,seconds in DEMO_POINTS:
                ident=db.execute('SELECT id FROM areas WHERE name=? AND map_version=?',(name,DEMO_VERSION)).fetchone()[0]
                points.append(dict(code=code,name=name,focus=focus,observe_seconds=seconds,area=ident,location_label=f'疑似跌倒｜觀察點 {code}：{name}',location_note='示範區域標記；長者實際位置待影像確認。'))
            return jsonify(points=points,route=['A','D','B','C','A'],demo=True,robot_control=False)
    @app.post('/api/demo-event')
    @auth(('admin','caregiver'))
    def demo_event():
        with lock:
            ident='guardian-demo:'+uuid.uuid4().hex; now=time.time()
            area=db.execute('SELECT id FROM areas WHERE name=? AND map_version=?',('浴室門外',DEMO_VERSION)).fetchone()[0]
            label='疑似跌倒｜觀察點 B：浴室門外。示範區域標記；長者實際位置待影像確認。'
            db.execute('INSERT INTO demo_events VALUES(?,?,?)',(ident,now,label))
            db.execute('INSERT INTO event_reviews VALUES(?,?,?,?,?,?,?)',(ident,area,'suspected','test','人工建立的流程示範，沒有真實跌倒影像',session['user'],now));db.commit();audit('demo_event_create',ident)
        return jsonify(ok=True,event=ident)
    def area_exists(ident):
        if not isinstance(ident,int) or not db.execute('SELECT 1 FROM areas WHERE id=?',(ident,)).fetchone(): abort(400)
    def short(b,k,limit=1000): return str(b.get(k,'')).strip()[:limit]
    def summaries():
        cutoff=time.time()-30*86400
        current={e['id']:e for e in events()}
        result=[]
        for row in db.execute('SELECT * FROM areas ORDER BY id'):
            a=dict(row); a.update(confirmed=0,suspected=0,false_alarm=0,uncertain=0,test_events=0)
            for r in db.execute('SELECT * FROM event_reviews WHERE area=?',(a['id'],)):
                e=current.get(r['event'])
                if not e or e['created_wall']<cutoff: continue
                if r['context']!='real': a['test_events']+=1; continue
                a[r['verdict']]+=1
            a['visits_30d']=db.execute("SELECT COUNT(*) FROM area_visits WHERE area=? AND context='real' AND at>=?",(a['id'],cutoff)).fetchone()[0]
            a['followups']=[dict(x) for x in db.execute('SELECT * FROM area_followups WHERE area=? ORDER BY id DESC',(a['id'],))]
            latest=a['followups'][0] if a['followups'] else None
            a['needs_review']=bool(a['confirmed'] or a['suspected'] or (latest and latest['state']!='verified'))
            result.append(a)
        return result
    @app.route('/api/areas',methods=['GET','POST'])
    @auth()
    def areas():
        with lock:
            if request.method=='POST':
                role=db.execute('SELECT role FROM users WHERE name=?',(session['user'],)).fetchone()[0]
                if role not in ('admin','caregiver'): abort(403)
                b=request.get_json(); name=short(b,'name',100)
                if not name: abort(400)
                db.execute('INSERT INTO areas(name,floor,map_version) VALUES(?,?,?)',(name,short(b,'floor',50),short(b,'map_version',100))); db.commit(); audit('area_create',name)
            return jsonify(areas=summaries(),window_days=30,location_source='人工區域標記',exposure_source='人工到點紀錄，非完整觀察時長')
    @app.post('/api/event-review')
    @auth(('admin','caregiver'))
    def review():
        b=request.get_json(); event=short(b,'event',500)
        if not any(e['id']==event for e in events()): abort(400)
        if b.get('verdict') not in ('confirmed','suspected','false_alarm','uncertain') or b.get('context') not in ('real','test'): abort(400)
        if event.startswith('guardian-demo:') and b['context']!='test': return jsonify(error='示範事件不能改為正式事件。'),400
        with lock:
            area_exists(b.get('area'))
            if b['context']=='real' and db.execute('SELECT map_version FROM areas WHERE id=?',(b['area'],)).fetchone()[0]==DEMO_VERSION:
                return jsonify(error='示範區域只能標記測試事件，請先建立實際區域。'),400
            values=(event,b['area'],b['verdict'],b['context'],short(b,'note'),session['user'],time.time())
            db.execute('INSERT OR REPLACE INTO event_reviews VALUES(?,?,?,?,?,?,?)',values);db.commit()
            audit('event_review',str(values))
        return jsonify(ok=True)
    @app.post('/api/area-followup')
    @auth(('admin','caregiver'))
    def followup():
        b=request.get_json()
        if b.get('state') not in ('open','awaiting_review','verified'): abort(400)
        if not short(b,'observation') or (b['state']=='verified' and not short(b,'result')): abort(400)
        with lock:
            area_exists(b.get('area'))
            db.execute('INSERT INTO area_followups(area,observation,measure,owner,state,result,author,at) VALUES(?,?,?,?,?,?,?,?)',(b['area'],short(b,'observation'),short(b,'measure'),short(b,'owner',100),b['state'],short(b,'result'),session['user'],time.time()));db.commit();audit('area_followup',str(b['area']))
        return jsonify(ok=True)
    @app.post('/api/area-visit')
    @auth(('admin','caregiver'))
    def visit():
        b=request.get_json()
        if b.get('context') not in ('real','test'): abort(400)
        with lock:
            area_exists(b.get('area'))
            now=time.time(); last=db.execute('SELECT at FROM area_visits WHERE area=? AND author=? ORDER BY id DESC LIMIT 1',(b['area'],session['user'])).fetchone()
            if b['context']=='real' and db.execute('SELECT map_version FROM areas WHERE id=?',(b['area'],)).fetchone()[0]==DEMO_VERSION: abort(400)
            if last and now-last[0]<60:return jsonify(ok=True,reminder=False,message='同區域 60 秒內不重複記錄。')
            db.execute('INSERT INTO area_visits(area,context,author,at) VALUES(?,?,?,?)',(b['area'],b['context'],session['user'],now));db.commit();audit('manual_area_visit',str(b['area']))
            a=next(a for a in summaries() if a['id']==b['area'])
            return jsonify(ok=True,reminder=a['needs_review'],message='此區域有歷史事件或待複查事項，請查看紀錄並停留觀察。' if a['needs_review'] else '已記錄人工到點；沒有歷史風險提醒不代表環境安全。')
