import time
import os
import json
import statistics
import urllib.request
from datetime import datetime, timezone, timedelta
from flask import jsonify

def build_report(db, events, now=None):
    now=time.time() if now is None else now
    records=[e for e in events if now-30*86400<=e['created_wall']<=now]
    reviews={r['event']:dict(r) for r in db.execute('SELECT * FROM event_reviews')}
    counts={'real':0,'test':0,'unreviewed':0}; verdicts={k:0 for k in ('confirmed','suspected','false_alarm','uncertain')}
    areas={r['id']:dict(r,confirmed=0) for r in db.execute('SELECT * FROM areas')}
    days={}; delays=[]; real_ids=[]
    for e in records:
        r=reviews.get(e['id']); context='test' if e.get('demo') else (r['context'] if r else 'unreviewed')
        counts[context]+=1
        day=datetime.fromtimestamp(e['created_wall'],timezone(timedelta(hours=8))).strftime('%m/%d')
        days.setdefault(day,dict(real=0,test=0,unreviewed=0))[context]+=1
        if context!='real': continue
        real_ids.append(e['id']);verdicts[r['verdict']]+=1
        if r['verdict']=='confirmed' and r['area'] in areas:areas[r['area']]['confirmed']+=1
        ack=db.execute("SELECT MIN(at) FROM actions WHERE event=? AND status='acknowledged'",(e['id'],)).fetchone()[0]
        if ack is not None and ack>=e['created_wall']:delays.append((ack-e['created_wall'])/60)
    return dict(window_days=30,generated_at=now,counts=counts,verdicts=verdicts,
        trend=[dict(day=k,**v) for k,v in sorted(days.items())],
        areas=[dict(name=a['name'],confirmed=a['confirmed']) for a in areas.values()],
        acknowledgement=dict(sample_size=len(delays),missing=len(real_ids)-len(delays),median_minutes=statistics.median(delays) if delays else None),
        comparison=dict(before=None,after=None,status='待驗證：尚無完整觀察時長及改善前後可比資料；不計算降低比例。'),
        limitations=['未標記事件不算正式事件；測試資料另列。','事件筆數不是跌倒機率；未偵測的跌倒不在此資料中。','接收時間從事件發生算起，含斷網與事後補登影響。'],
        plan=['建立正式區域並人工判定事件','記錄觀察時長與改善日期','在相同條件比較改善前後事件及回應時間','保留誤報與漏報驗證，再決定巡檢調整'])

def register_analytics(app,db,lock,auth,events):
    @app.get('/api/analytics')
    @auth()
    def analytics():
        with lock:return jsonify(build_report(db,events()))
    @app.post('/api/analytics/ai')
    @auth(('admin','caregiver'))
    def ai():
        key=os.environ.get('GEMINI_API_KEY','');model=os.environ.get('GEMINI_MODEL_NAME','gemini-2.5-flash')
        if not key:return jsonify(error='尚未設定 GEMINI_API_KEY；統計圖可使用，AI 分析尚未啟用。'),503
        with lock: report=build_report(db,events())
        # Only aggregate counts; omit names, notes, images, identifiers and precise dates.
        payload={k:report[k] for k in ('window_days','counts','verdicts','acknowledgement','comparison','limitations')}
        prompt='以繁體中文分析照護後台彙總。列出可證明的事實、資料缺口及三項待驗證改善方案。引用給定數字，禁止捏造降低比例、因果關係或診斷。測試不能當真實成效。沒有前後資料就說無法證明改善。資料：'+json.dumps(payload,ensure_ascii=False)
        req=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/'+urllib.parse.quote(model,safe='')+':generateContent',data=json.dumps({'contents':[{'parts':[{'text':prompt}]}]}).encode(),headers={'Content-Type':'application/json','x-goog-api-key':key})
        try:
            with urllib.request.urlopen(req,timeout=40) as response: data=json.load(response)
            text='\n'.join(p.get('text','') for p in data['candidates'][0]['content']['parts'])
            return jsonify(analysis=text,model=model,disclaimer='AI 建議，需人工覆核；不是實測改善結果。')
        except Exception:return jsonify(error='AI 服務未成功回應，請檢查伺服器的模型、金鑰及網路設定。'),502
