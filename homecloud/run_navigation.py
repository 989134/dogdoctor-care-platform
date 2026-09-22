"""Offline simulator or measured telemetry replay. Never sends motor commands."""
import argparse
import json
import math
from pathlib import Path
from navigation.core import Grid,Odometry,Patrol,LocalScanMatcher,Pose

ROOT=Path(__file__).resolve().parent


def room_scan(p):
    rays=[]
    for degrees in range(-180,180,2):
        a=math.radians(degrees);dx=math.cos(p.yaw+a);dy=math.sin(p.yaw+a)
        ds=[]
        if abs(dx)>1e-8:ds.append(((2 if dx>0 else -2)-p.x)/dx)
        if abs(dy)>1e-8:ds.append(((2 if dy>0 else -2)-p.y)/dy)
        d=min(ds);rays.append([a,min(4,d),d<4])
    return rays


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--replay',type=Path,help='JSONL measured telemetry, no actuation')
    ap.add_argument('--waypoints',type=Path,default=ROOT/'navigation/waypoints.json')
    args=ap.parse_args()
    points=json.loads(args.waypoints.read_text())
    grid=Grid();odom=Odometry();patrol=Patrol(grid);matcher=LocalScanMatcher()
    patrol.start(points)
    command='stop';left=right=0.;truth=Pose();rows=[]
    packets=(json.loads(line) for line in args.replay.read_text().splitlines() if line.strip()) if args.replay else None
    for i in range(2000):
        if packets:
            try:p=next(packets)
            except StopIteration:break
        else:
            v=.15 if command=='forward' else 0
            w=.6 if command=='left' else (-.6 if command=='right' else 0)
            truth.yaw+=w*.1;truth.x+=v*.1*math.cos(truth.yaw);truth.y+=v*.1*math.sin(truth.yaw)
            left+=(v-w*.18/2)*.1;right+=(v+w*.18/2)*.1
            rays=room_scan(truth)
            p=dict(t=i*.1,left_m=left,right_m=right,rays=rays,front_m=min(d for a,d,h in rays if abs(a)<.15),speed_mps=v)
        try:
            pose=odom.update(p['t'],p['left_m'],p['right_m'])
            corrected,accepted=matcher.match(grid,pose,p['rays'])
            if accepted:odom.pose=corrected;pose=corrected
            grid.integrate(pose,p['rays'])
            command=patrol.step(pose,p['t'],p['t'],odom.uncertainty,p['front_m'],p['speed_mps'])
        except (ValueError,KeyError,TypeError) as exc:
            command='stop';patrol.state='localization_lost';patrol.reason=str(exc)
            break
        rows.append(dict(t=p['t'],x=pose.x,y=pose.y,yaw=pose.yaw,state=patrol.state,command_proposal=command))
        if patrol.state in ('complete','blocked','localization_lost'):break
    out=ROOT/'navigation/output';out.mkdir(parents=True,exist_ok=True)
    grid.save(out/'map.json')
    report=dict(source='measured_replay' if args.replay else 'SIMULATION',hardware_commands_sent=0,
                status=patrol.state,reason=patrol.reason,waypoints=points,trace=rows,
                limitation='Local scan correction only; no loop closure. Replay cannot validate motor response or live timeout.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    rects=[]
    for y,row in enumerate(grid.cells):
        for x,value in enumerate(row):
            color='#153b47' if value>0 else ('#ffffff' if value<0 else '#ccd5d9')
            rects.append(f'<rect x="{x}" y="{grid.size-y-1}" width="1" height="1" fill="{color}"/>')
    path=' '.join(f'{grid.cell(r["x"],r["y"])[0]},{grid.size-grid.cell(r["x"],r["y"])[1]-1}' for r in rows)
    dots=''.join(f'<circle cx="{grid.cell(x,y)[0]}" cy="{grid.size-grid.cell(x,y)[1]-1}" r="1" fill="#e85d3f"/>' for x,y,_ in points)
    (out/'preview.html').write_text(f'''<!doctype html><meta charset="utf-8"><title>巡檢離線測試</title>
    <body style="font-family:system-ui;background:#edf4f5;max-width:850px;margin:30px auto"><h1>巡檢離線測試</h1>
    <p>資料來源：{report['source']} · 狀態：{patrol.state} · 未傳送任何實車指令</p>
    <p>白色已觀測空間，灰色未知，深色障礙，橘點巡檢位置，藍線估計軌跡。每格 0.1 公尺。</p>
    <svg viewBox="0 0 {grid.size} {grid.size}" style="width:650px;max-width:100%">{''.join(rects)}<polyline points="{path}" fill="none" stroke="#0088ee" stroke-width=".4"/>{dots}</svg>
    <p>模擬成功不代表實車定位或導航已通過。實車尚缺編碼器、距離掃描與安全停止驗證。</p></body>''',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='trace'},ensure_ascii=False))
    print(out/'preview.html')


if __name__=='__main__':main()
