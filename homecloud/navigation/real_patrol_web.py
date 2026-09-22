"""Web/API surface for supervised real Ranger patrol."""

from flask import Flask, jsonify, render_template_string, request


PAGE = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ranger 實車自主巡檢</title><style>
:root{--ink:#17343d;--teal:#087f8c;--red:#bd3f4c;--bg:#edf4f5}*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:system-ui,"Microsoft JhengHei",sans-serif;color:var(--ink)}header{background:#173b47;color:#fff;padding:16px 24px}.banner{padding:11px;text-align:center;font-weight:800;background:#fff0c7;color:#704c00}.banner.armed{background:#ffe0e3;color:#951f2e}.layout{display:grid;grid-template-columns:minmax(480px,1.2fr) minmax(380px,.8fr);gap:16px;max-width:1320px;margin:18px auto;padding:0 16px}.card{background:#fff;border-radius:13px;padding:16px;box-shadow:0 3px 12px #173b4714}canvas{width:100%;aspect-ratio:1;background:#f8fbfb;border-radius:9px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.metric{background:#f0f6f7;border-radius:8px;padding:9px}.metric b{display:block;font-size:18px;margin-top:3px}button{border:0;border-radius:8px;padding:11px 14px;margin:3px;background:var(--teal);color:#fff;font-weight:800;cursor:pointer}button.secondary{background:#536d76}button.danger{background:var(--red)}textarea{width:100%;height:160px;padding:9px;border:1px solid #afc2c7;border-radius:7px;font-family:ui-monospace,monospace}.bad{color:var(--red);font-weight:700}.events{max-height:190px;overflow:auto;font-size:13px;background:#f5f8f8;padding:7px;border-radius:7px}@media(max-width:900px){.layout{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}}</style></head>
<body><header><h1>Ranger 實車自主巡檢</h1></header><div id="banner" class="banner">載入中</div><main class="layout"><section><div class="card"><canvas id="map" width="720" height="720"></canvas></div><div class="card"><div class="metrics"><div class="metric">任務狀態<b id="state">—</b></div><div class="metric">目前指令<b id="command">stop</b></div><div class="metric">定位<b id="localization">—</b></div><div class="metric">座標<b id="pose">—</b></div><div class="metric">前方距離<b id="front">—</b></div><div class="metric">已送指令<b id="count">0</b></div></div><p id="reason"></p><p id="error" class="bad"></p><p id="saved"></p><button onclick="preflight()">執行前置檢查</button><button onclick="startMission()">開始巡檢</button><button class="secondary" onclick="post('/pause')">暫停</button><button onclick="post('/resume')">恢復</button><button class="danger" onclick="post('/emergency-stop')">緊急停止</button><button class="secondary" onclick="post('/cancel')">取消</button><button class="secondary" onclick="saveEvidence()">保存測試紀錄</button></div></section><aside><div class="card"><h2>巡檢點 JSON</h2><p>座標以定位頁「重設原點」的位置為 (0,0)。選填 yaw_deg 可指定到點後朝向（左正、右負）；後續巡檢點可由前一點原地掃描後建立安全區。</p><button class="secondary" onclick="preset('straight')">直線範本</button><button class="secondary" onclick="preset('rightL')">右轉 L 路線</button><textarea id="points">[{"id":"test","name":"前方短距離測試","x":0.25,"y":0.0,"observe_seconds":3,"priority":1}]</textarea></div><div class="card"><h2>前置檢查</h2><pre id="preflight">尚未執行</pre></div><div class="card"><h2>事件</h2><div id="events" class="events"></div></div></aside></main>
<script>
async function call(path,body){const r=await fetch('/api/real-patrol'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)document.querySelector('#error').textContent=d.error||'操作失敗';return d}async function post(p){await call(p)}async function preflight(){const r=await fetch('/api/real-patrol/preflight');const d=await r.json();document.querySelector('#preflight').textContent=JSON.stringify({ok:d.ok,errors:d.errors,motion_enabled:d.motion_enabled},null,2)}async function startMission(){let points;try{points=JSON.parse(document.querySelector('#points').value)}catch(e){document.querySelector('#error').textContent='巡檢點 JSON 格式錯誤';return}await call('/start',{waypoints:points,route_mode:'manual'})}
async function saveEvidence(){const d=await call('/save-evidence');if(d.ok)document.querySelector('#saved').textContent='已保存：'+d.path}
function preset(name){const routes={straight:[{id:'straight',name:'前方短距離',x:.25,y:0,observe_seconds:3,priority:1}],rightL:[{id:'corner',name:'右轉觀察點',x:.25,y:0,yaw_deg:-90,observe_seconds:2,priority:1},{id:'finish',name:'右側終點',x:.25,y:-.25,yaw_deg:-90,observe_seconds:3,priority:1}]};document.querySelector('#points').value=JSON.stringify(routes[name],null,2)}
function draw(s){const c=document.querySelector('#map'),g=c.getContext('2d'),m=s.map||{},W=c.width,H=c.height,x0=(m.origin||[-4.5,-4.5])[0],y0=(m.origin||[-4.5,-4.5])[1],span=(m.size||180)*(m.resolution||.05),px=x=>(x-x0)/span*W,py=y=>H-(y-y0)/span*H;g.fillStyle='#f8fbfb';g.fillRect(0,0,W,H);g.fillStyle='#dceff1';(m.free||[]).forEach(p=>g.fillRect(px(p[0])-1,py(p[1])-1,2,2));g.fillStyle='#26383e';(m.occupied||[]).forEach(p=>g.fillRect(px(p[0])-3,py(p[1])-3,6,6));function line(a,color,w){if(a.length<2)return;g.beginPath();g.strokeStyle=color;g.lineWidth=w;a.forEach((p,i)=>i?g.lineTo(px(p[0]),py(p[1])):g.moveTo(px(p[0]),py(p[1])));g.stroke()}line(s.trace||[],'#df7e2d',3);line(s.path||[],'#087f8c',5);(s.route||[]).forEach(p=>{g.fillStyle='#7a39a0';g.beginPath();g.arc(px(p.x),py(p.y),8,0,Math.PI*2);g.fill()});const p=s.pose||{x:0,y:0,yaw:0};g.save();g.translate(px(p.x),py(p.y));g.rotate(-p.yaw);g.fillStyle='#c83f4e';g.beginPath();g.moveTo(15,0);g.lineTo(-9,-9);g.lineTo(-9,9);g.closePath();g.fill();g.restore()}
async function refresh(){try{const s=await fetch('/api/real-patrol/status').then(r=>r.json()),armed=s.motion_enabled,b=document.querySelector('#banner');b.textContent=armed?'實車輸出已啟用：按下開始會移動車子':'唯讀預演：不會傳送移動指令';b.className='banner'+(armed?' armed':'');document.querySelector('#state').textContent=s.state;document.querySelector('#command').textContent=s.command;document.querySelector('#localization').textContent=s.localization_available?'正常':'不可用';document.querySelector('#pose').textContent=`${(s.pose.x||0).toFixed(2)}, ${(s.pose.y||0).toFixed(2)}`;document.querySelector('#front').textContent=s.front_m==null?'—':s.front_m.toFixed(2)+' m';document.querySelector('#count').textContent=s.hardware_commands_sent;document.querySelector('#reason').textContent=s.reason||'';document.querySelector('#error').textContent=s.last_error||'';document.querySelector('#events').innerHTML=[...(s.events||[])].reverse().map(e=>`<div>${e.message}</div>`).join('');draw(s)}catch(e){document.querySelector('#error').textContent='實車巡檢服務連線失敗'}}setInterval(refresh,500);refresh();preflight();
</script></body></html>"""


def create_app(runtime):
    app = Flask(__name__)

    @app.get("/")
    @app.get("/real-patrol")
    def page():
        return render_template_string(PAGE)

    @app.get("/api/real-patrol/status")
    def status():
        return jsonify(runtime.status())

    @app.get("/api/real-patrol/preflight")
    def preflight():
        return jsonify(runtime.preflight())

    @app.get("/api/real-patrol/evidence")
    def evidence():
        return jsonify(runtime.evidence())

    def action(callback):
        try:
            callback()
            return jsonify(ok=True)
        except (ValueError, RuntimeError) as exc:
            return jsonify(ok=False, error=str(exc)), 409

    @app.post("/api/real-patrol/start")
    def start():
        body = request.get_json(silent=True) or {}
        return action(lambda: runtime.start_mission(
            body.get("waypoints", []), body.get("route_mode", "manual"),
            bool(body.get("hold_before_motion", False)),
        ))

    @app.post("/api/real-patrol/release")
    def release(): return action(runtime.release_hold)

    @app.post("/api/real-patrol/pause")
    def pause(): return action(runtime.pause)

    @app.post("/api/real-patrol/resume")
    def resume(): return action(runtime.resume)

    @app.post("/api/real-patrol/cancel")
    def cancel(): return action(runtime.cancel)

    @app.post("/api/real-patrol/emergency-stop")
    def emergency_stop(): return action(runtime.emergency_stop)

    @app.post("/api/real-patrol/save-evidence")
    def save_evidence():
        try:
            return jsonify(ok=True, path=str(runtime.save_evidence()))
        except OSError as exc:
            return jsonify(ok=False, error=f"無法保存測試紀錄：{exc}"), 500

    return app
