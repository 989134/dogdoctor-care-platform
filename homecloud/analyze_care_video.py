"""Offline time-correct pose diagnostics for a recorded test video."""
import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'eldertest'))
from care_engine import CareEngine
from mec_service import MediaPipeDetector


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('video',type=Path)
    parser.add_argument('--fps',type=float,default=5)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--annotated',type=Path,help='輸出含骨架、姿態與警報狀態的 MP4')
    args=parser.parse_args()
    capture=cv2.VideoCapture(str(args.video))
    if not capture.isOpened():raise SystemExit('cannot open video')
    source_fps=capture.get(cv2.CAP_PROP_FPS) or 30
    stride=max(1,round(source_fps/args.fps))
    detector=MediaPipeDetector();engine=CareEngine();rows=[];sample=0;writer=None
    while True:
        ok,frame=capture.read()
        if not ok:break
        sample+=1;t=(sample-1)/args.fps
        points,_=detector.process(frame)
        state=engine.update(points,frame.shape,t)
        visible={}
        if points:
            for name,ids in [('shoulders',(11,12)),('hips',(23,24)),
                             ('knees',(25,26)),('ankles',(27,28))]:
                visible[name]=round(min(points[i].visibility for i in ids),3)
            visible['shoulder_y']=round((points[11].y+points[12].y)/2,3)
            visible['hip_y']=round((points[23].y+points[24].y)/2,3)
        rows.append({'sample':sample,'video_time':round(t,2),'posture':state['posture'],
                     'mode':state['observation_mode'],'angle':state['angle'],
                     'visible':visible,'active_events':state['active_events']})
        if args.annotated:
            if writer is None:
                args.annotated.parent.mkdir(parents=True,exist_ok=True)
                h,w=frame.shape[:2]
                writer=cv2.VideoWriter(str(args.annotated),cv2.VideoWriter_fourcc(*'mp4v'),
                                       args.fps,(w,h))
                if not writer.isOpened():
                    raise SystemExit(f'cannot create annotated video: {args.annotated}')
            if points:
                h,w=frame.shape[:2]
                threshold=engine.settings.visibility
                for a,b in detector.mp.solutions.pose.POSE_CONNECTIONS:
                    if (points[a].visibility>=threshold and points[b].visibility>=threshold and
                            0<=points[a].x<=1 and 0<=points[a].y<=1 and
                            0<=points[b].x<=1 and 0<=points[b].y<=1):
                        cv2.line(frame,(int(points[a].x*w),int(points[a].y*h)),
                                 (int(points[b].x*w),int(points[b].y*h)),(80,240,80),2)
            active=[e for e in state['active_events'] if e['status'] in ('pending','review','alert')]
            color=(0,0,220) if any(e['status']=='alert' for e in active) else (0,160,255)
            cv2.rectangle(frame,(0,0),(frame.shape[1],78),(20,30,35),-1)
            cv2.putText(frame,f"Mode: {state['observation_mode']}  Posture: {state['posture']}",
                        (12,28),cv2.FONT_HERSHEY_SIMPLEX,.58,(255,255,255),2)
            cv2.putText(frame,f"Time: {t:.1f}s  Angle: {state['angle']}",
                        (12,55),cv2.FONT_HERSHEY_SIMPLEX,.58,(255,255,255),2)
            if active:
                event=active[-1]
                cv2.putText(frame,f"EVENT: {event['kind']} / {event['status']}",
                            (12,frame.shape[0]-18),cv2.FONT_HERSHEY_SIMPLEX,.65,color,2)
            writer.write(frame)
        for _ in range(stride-1):
            if not capture.grab():break
    capture.release()
    if writer is not None:writer.release()
    result={'video':str(args.video),'source_fps':source_fps,'sample_fps':args.fps,
            'rows':rows,'events':list(engine.events)}
    output=args.output or ROOT/'mec_output'/(args.video.stem+'_pose_diagnostic.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'samples':len(rows),'events':result['events'],'output':str(output.resolve()),
                      'annotated':str(args.annotated.resolve()) if args.annotated else None},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
