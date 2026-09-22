"""Interactive simulation dashboard for patrol missions."""

from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, render_template_string, request

from navigation.mission import PatrolMission
from navigation.patrol_simulator import DEFAULT_WAYPOINTS, PatrolSimulator


class PatrolRuntime:
    def __init__(self, tick_seconds=.1, start_thread=True):
        self.tick_seconds = tick_seconds
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.thread = None
        self._reset_unlocked()
        if start_thread:
            self.start_thread()

    def _reset_unlocked(self):
        self.simulator = PatrolSimulator.create()
        self.mission = PatrolMission(self.simulator.grid)
        self.command = "stop"

    def start_thread(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, daemon=True, name="PatrolSimulator")
        self.thread.start()

    def _loop(self):
        while not self.closed.wait(self.tick_seconds):
            with self.lock:
                self.simulator.step(self.command)
                self.command = self.mission.tick(self.simulator.snapshot())

    def advance(self, steps=1):
        """Deterministic test/manual advancement when no thread is running."""
        with self.lock:
            for _ in range(steps):
                self.simulator.step(self.command)
                self.command = self.mission.tick(self.simulator.snapshot())

    def reset(self):
        with self.lock:
            self._reset_unlocked()

    def start_mission(self, waypoints, route_mode):
        with self.lock:
            self._reset_unlocked()
            self.mission.start(waypoints, self.simulator.pose, route_mode, self.simulator.time)
            self.command = self.mission.tick(self.simulator.snapshot())

    def pause(self):
        with self.lock:
            self.mission.pause(self.simulator.time)
            self.command = "stop"

    def resume(self):
        with self.lock:
            self.mission.resume(
                self.simulator.pose, self.simulator.localization_available, self.simulator.time
            )
            self.command = self.mission.tick(self.simulator.snapshot())

    def cancel(self):
        with self.lock:
            self.mission.cancel(self.simulator.time)
            self.command = "stop"

    def inject_obstacle(self):
        with self.lock:
            self.simulator.inject_obstacle()

    def lose_localization(self):
        with self.lock:
            self.simulator.localization_available = False

    def restore_localization(self):
        with self.lock:
            self.simulator.localization_available = True

    def status(self):
        with self.lock:
            status = self.mission.status()
            occupied = []
            for y, row in enumerate(self.simulator.grid.cells):
                for x, value in enumerate(row):
                    if value > 0:
                        occupied.append(list(self.simulator.grid.world((x, y))))
            status.update({
                "mode": "SIMULATION", "hardware_commands_sent": 0,
                "pose": {"x": self.simulator.pose.x, "y": self.simulator.pose.y,
                         "yaw": self.simulator.pose.yaw},
                "time": self.simulator.time, "trace": list(self.simulator.trace[-1200:]),
                "occupied": occupied, "resolution": self.simulator.grid.resolution,
                "bounds": [-2.0, -2.0, 2.0, 2.0],
                "localization_available": self.simulator.localization_available,
                "default_waypoints": DEFAULT_WAYPOINTS,
            })
            return status

    def close(self):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout=1)


PAGE = r"""
<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>自主巡檢與路徑規劃</title><style>
:root{--ink:#18343d;--teal:#087f8c;--red:#bd3f4c;--bg:#edf4f5}*{box-sizing:border-box}body{margin:0;background:var(--bg);font-family:system-ui,"Microsoft JhengHei",sans-serif;color:var(--ink)}header{background:#173b47;color:white;padding:16px 24px}header h1{margin:0;font-size:24px}.layout{display:grid;grid-template-columns:minmax(480px,1.35fr) minmax(360px,.9fr);gap:16px;max-width:1320px;margin:18px auto;padding:0 16px}.card{background:white;border-radius:13px;padding:16px;box-shadow:0 3px 12px #173b4714}canvas{width:100%;aspect-ratio:1/1;background:#dfe9eb;border-radius:9px}.banner{background:#fff3cf;color:#704c00;padding:10px 16px;text-align:center;font-weight:700}.status{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.metric{padding:10px;background:#f1f6f7;border-radius:8px}.metric b{display:block;font-size:18px;margin-top:3px}button{border:0;border-radius:8px;padding:10px 13px;background:var(--teal);color:white;font-weight:700;cursor:pointer;margin:3px}button.secondary{background:#536d76}button.danger{background:var(--red)}button.warn{background:#b56b09}input,select{padding:7px;border:1px solid #afc2c7;border-radius:6px;width:100%}table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:6px;border-bottom:1px solid #dce6e8}th{text-align:left}.events{height:170px;overflow:auto;background:#f5f8f8;padding:8px;border-radius:8px;font-size:13px}.event{padding:4px;border-bottom:1px solid #e1e8ea}.row-actions{display:flex;flex-wrap:wrap;gap:3px;margin:10px 0}.legend span{display:inline-block;margin-right:14px;font-size:13px}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:4px}@media(max-width:900px){.layout{grid-template-columns:1fr}.status{grid-template-columns:1fr 1fr}}
</style></head><body><header><h1>自主巡檢與路徑規劃</h1></header><div class="banner">目前為電腦模擬模式：不會傳送任何實車指令</div>
<main class="layout"><section><div class="card"><canvas id="map" width="720" height="720"></canvas><div class="legend"><span><i class="dot" style="background:#18343d"></i>障礙</span><span><i class="dot" style="background:#008f9c"></i>規劃路徑</span><span><i class="dot" style="background:#e68a2e"></i>行走軌跡</span><span><i class="dot" style="background:#c83f4e"></i>機器狗</span></div></div>
<div class="card"><h2>任務控制</h2><div class="status"><div class="metric">狀態<b id="state">—</b></div><div class="metric">目前指令<b id="command">stop</b></div><div class="metric">目標<b id="target">—</b></div><div class="metric">已觀察<b id="observed">0</b></div><div class="metric">重規劃<b id="replans">0</b></div><div class="metric">定位<b id="localization">正常</b></div></div><p id="reason"></p>
<div class="row-actions"><button onclick="startMission()">開始巡檢</button><button class="secondary" onclick="post('/pause')">暫停</button><button onclick="post('/resume')">恢復</button><button class="danger" onclick="post('/cancel')">取消</button><button class="secondary" onclick="post('/reset')">重設</button></div>
<div class="row-actions"><button class="warn" onclick="post('/inject-obstacle')">模擬前方障礙</button><button class="danger" onclick="post('/lose-localization')">模擬定位失敗</button><button onclick="restore()">恢復定位並繼續</button></div></div></section>
<aside><div class="card"><h2>巡檢點</h2><label>路線安排<select id="routeMode"><option value="manual">依表格順序</option><option value="shortest">同優先級最短路線</option></select></label><table><thead><tr><th>名稱</th><th>X</th><th>Y</th><th>觀察秒</th><th>優先</th><th></th></tr></thead><tbody id="points"></tbody></table><button onclick="addPoint()">新增巡檢點</button></div>
<div class="card"><h2>事件紀錄</h2><div id="events" class="events"></div></div><div class="card"><h2>目前限制</h2><p>位置、距離和障礙都是模擬資料。接上實車前仍需輪編碼器校正、有效距離感測、地圖來源驗證與緊急停止測試。</p></div></aside></main>
<script>
let initialized=false;function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function addPoint(p={}){const tr=document.createElement('tr');tr.innerHTML=`<td><input data-k="name" value="${esc(p.name||'新巡檢點')}"></td><td><input data-k="x" type="number" step=".05" value="${p.x??0}"></td><td><input data-k="y" type="number" step=".05" value="${p.y??0}"></td><td><input data-k="observe_seconds" type="number" min="1" value="${p.observe_seconds??2}"></td><td><input data-k="priority" type="number" value="${p.priority??0}"></td><td><button class="danger" onclick="this.closest('tr').remove()">×</button></td>`;tr.dataset.id=p.id||crypto.randomUUID();document.querySelector('#points').appendChild(tr)}
function readPoints(){return [...document.querySelectorAll('#points tr')].map(tr=>{let o={id:tr.dataset.id};tr.querySelectorAll('input').forEach(i=>o[i.dataset.k]=i.type==='number'?Number(i.value):i.value);return o})}
async function call(path,body){const r=await fetch('/api/patrol'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const d=await r.json();if(!r.ok)alert(d.error||'操作失敗');return d}
async function post(path){await call(path)}async function startMission(){await call('/start',{waypoints:readPoints(),route_mode:document.querySelector('#routeMode').value})}async function restore(){await call('/restore-localization');await call('/resume')}
function draw(s){const c=document.querySelector('#map'),x=c.getContext('2d'),[x0,y0,x1,y1]=s.bounds,W=c.width,H=c.height,px=v=>(v-x0)/(x1-x0)*W,py=v=>H-(v-y0)/(y1-y0)*H;x.clearRect(0,0,W,H);x.fillStyle='#f8fbfb';x.fillRect(0,0,W,H);x.strokeStyle='#d7e2e4';for(let v=-2;v<=2;v+=.5){x.beginPath();x.moveTo(px(v),0);x.lineTo(px(v),H);x.stroke();x.beginPath();x.moveTo(0,py(v));x.lineTo(W,py(v));x.stroke()}x.fillStyle='#18343d';const cell=s.resolution*W/(x1-x0)+1;s.occupied.forEach(p=>x.fillRect(px(p[0])-cell/2,py(p[1])-cell/2,cell,cell));function line(points,color,w){if(points.length<2)return;x.strokeStyle=color;x.lineWidth=w;x.beginPath();points.forEach((p,i)=>i?x.lineTo(px(p[0]),py(p[1])):x.moveTo(px(p[0]),py(p[1])));x.stroke()}line(s.trace,'#e68a2e',4);line(s.path,'#008f9c',5);s.route.forEach((p,i)=>{x.fillStyle=i===s.current_index?'#7a39a0':'#16885a';x.beginPath();x.arc(px(p.x),py(p.y),9,0,Math.PI*2);x.fill();x.fillStyle='#17323b';x.font='14px sans-serif';x.fillText(p.name,px(p.x)+11,py(p.y)-8)});const p=s.pose;x.save();x.translate(px(p.x),py(p.y));x.rotate(-p.yaw);x.fillStyle='#c83f4e';x.beginPath();x.moveTo(16,0);x.lineTo(-10,-10);x.lineTo(-10,10);x.closePath();x.fill();x.restore()}
async function refresh(){try{const s=await fetch('/api/patrol/status').then(r=>r.json());if(!initialized){s.default_waypoints.forEach(addPoint);initialized=true}document.querySelector('#state').textContent=s.state;document.querySelector('#command').textContent=s.command;document.querySelector('#target').textContent=s.target?.name||'—';document.querySelector('#observed').textContent=s.completed_observations.length+'/'+s.route.length;document.querySelector('#replans').textContent=s.replans;document.querySelector('#localization').textContent=s.localization_available?'正常':'失敗';document.querySelector('#reason').textContent=s.reason||'';document.querySelector('#events').innerHTML=[...s.events].reverse().map(e=>`<div class="event">${e.time.toFixed(1)}s　${esc(e.message)}</div>`).join('');draw(s)}catch(e){document.querySelector('#reason').textContent='服務連線失敗'}}setInterval(refresh,250);refresh();
</script></body></html>
"""


def create_patrol_blueprint(runtime: PatrolRuntime):
    blueprint = Blueprint("patrol", __name__)

    @blueprint.get("/patrol")
    def page():
        return render_template_string(PAGE)

    @blueprint.get("/api/patrol/status")
    def status():
        return jsonify(runtime.status())

    def action(name, callback):
        try:
            callback()
            return jsonify(ok=True, action=name)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409

    @blueprint.post("/api/patrol/start")
    def start():
        body = request.get_json(silent=True) or {}
        return action("start", lambda: runtime.start_mission(body.get("waypoints", []), body.get("route_mode", "manual")))

    @blueprint.post("/api/patrol/pause")
    def pause(): return action("pause", runtime.pause)

    @blueprint.post("/api/patrol/resume")
    def resume(): return action("resume", runtime.resume)

    @blueprint.post("/api/patrol/cancel")
    def cancel(): return action("cancel", runtime.cancel)

    @blueprint.post("/api/patrol/reset")
    def reset(): return action("reset", runtime.reset)

    @blueprint.post("/api/patrol/inject-obstacle")
    def obstacle(): return action("inject_obstacle", runtime.inject_obstacle)

    @blueprint.post("/api/patrol/lose-localization")
    def lose(): return action("lose_localization", runtime.lose_localization)

    @blueprint.post("/api/patrol/restore-localization")
    def restore(): return action("restore_localization", runtime.restore_localization)

    return blueprint

