"""Focused pose/fall entry point. Does not load or command motors, LINE, YOLO or Depth."""
import argparse
import json
import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'eldertest'))
from care_engine import Settings
from care_service import CareService
from care_web import create_blueprint
from care_speaker import CareSpeaker, RobotCareSpeaker, watch_service
from companion_assistant import CompanionAssistant
from robot_audio_client import RobotAudioClient
from care_follow import FollowRuntime
import config


def main():
    parser=argparse.ArgumentParser(description='居家姿態與異常活動監測')
    parser.add_argument('--source',default='http://172.20.10.2:5001/video_feed')
    parser.add_argument('--port',type=int,default=5002)
    parser.add_argument('--host',default=config.CARE_WEB_HOST,
                        help='手機要從同一區網連線時使用 0.0.0.0')
    parser.add_argument('--settings',type=Path,default=ROOT/'care_settings.json')
    parser.add_argument('--loop',action='store_true',help='本機影片播放完後循環播放')
    parser.add_argument('--local-tts',action='store_true',help='用這台電腦／樹莓派的喇叭播放跌倒詢問')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    source_candidate=Path(args.source)
    if not source_candidate.is_absolute():
        source_candidate=(ROOT/source_candidate).resolve()
    if source_candidate.is_file():
        source=str(source_candidate)
        recorded_video=True
    else:
        source=int(args.source) if args.source.isdigit() else args.source
        recorded_video=False
    settings=Settings(**json.loads(args.settings.read_text(encoding='utf-8')))
    demo_database=ROOT/'mec_output'/f'care_demo_{args.port}.sqlite3'
    demo_snapshots=ROOT/'mec_output'/f'care_demo_{args.port}_snapshots'
    # A recorded clip is a new validation session: keep its history/snapshots,
    # but do not revive an alert produced by a previous playback. Live camera
    # sessions restore unresolved events after a process restart.
    audio_client=(RobotAudioClient(config.ROBOT_AUDIO_URL,config.ROBOT_AUDIO_TOKEN,
                                   timeout=15.0)
                  if config.ROBOT_AUDIO_URL else None)
    robot_speaker=RobotCareSpeaker(audio_client) if audio_client else None
    if robot_speaker:
        robot_speaker.start()
    follow_runtime=None
    try:
        follow_runtime=FollowRuntime(ROOT/'models'/'best02.pt',config.ROBOT_CONTROL_URL,
                                     on_arrival=(robot_speaker.arrival if robot_speaker else None))
    except Exception as exc:
        logging.warning('自主跟隨初始化失敗，其他照護功能仍可使用：%s',exc)
    service=CareService(settings,args.settings,demo_database,demo_snapshots,
                        restore_events=not recorded_video,follow_runtime=follow_runtime)
    from flask import Flask, redirect
    from navigation.patrol_web import PatrolRuntime, create_patrol_blueprint
    app=Flask(__name__)
    assistant=CompanionAssistant(config.GEMINI_API_KEY,config.GEMINI_MODEL_NAME)
    app.register_blueprint(create_blueprint(service,assistant=assistant,
                                            audio_client=audio_client))
    patrol_runtime=PatrolRuntime()
    app.register_blueprint(create_patrol_blueprint(patrol_runtime))
    app.add_url_rule('/',view_func=lambda:redirect('/care'))

    def reader():
        try:
            import cv2
            import time
            while not service.stop_event.is_set():
                cap=cv2.VideoCapture(source)
                try:
                    if not cap.isOpened():
                        with service.lock: service.error='無法開啟影像來源，正在重試'
                        service.stop_event.wait(2)
                        continue
                    video_interval=(1/max(cap.get(cv2.CAP_PROP_FPS),1)
                                    if recorded_video else 0)
                    next_frame_at=time.monotonic()
                    last_video_frame=None
                    while not service.stop_event.is_set():
                        ok,frame=cap.read()
                        if not ok:
                            if recorded_video and args.loop:
                                cap.set(cv2.CAP_PROP_POS_FRAMES,0)
                                next_frame_at=time.monotonic()+1
                                service.stop_event.wait(1)
                                continue
                            if recorded_video and last_video_frame is not None:
                                service.submit(last_video_frame)
                                service.stop_event.wait(.2)
                                continue
                            with service.lock: service.error='影像讀取中斷，正在重連'
                            break
                        last_video_frame=frame
                        service.submit(frame)
                        # Recorded videos must retain their original time scale.
                        if recorded_video:
                            next_frame_at += video_interval
                            service.stop_event.wait(max(0,next_frame_at-time.monotonic()))
                finally:
                    cap.release()
                service.stop_event.wait(1)
        except Exception as exc:
            with service.lock: service.error=str(exc)
            logging.exception('影像讀取失敗')

    service.start()
    if not service.ready.wait(10):
        raise RuntimeError('姿態模型啟動逾時')
    threading.Thread(target=reader,name='CareCamera',daemon=True).start()
    if robot_speaker:
        threading.Thread(target=watch_service,
                         args=(service,robot_speaker,service.stop_event),
                         name='RobotCareVoiceWatch',daemon=True).start()
    speaker=CareSpeaker() if args.local_tts else None
    if speaker:
        if speaker.start():
            threading.Thread(target=watch_service,args=(service,speaker,service.stop_event),
                             name='CareVoiceWatch',daemon=True).start()
        else:
            logging.warning('未找到可用的本機語音引擎，網頁語音功能仍可使用')
    try:
        app.run(host=args.host,port=args.port,debug=False,use_reloader=False,
                threaded=True,load_dotenv=False)
    finally:
        patrol_runtime.close()
        if speaker:
            speaker.close()
        if robot_speaker:
            robot_speaker.close()
        service.close()


if __name__=='__main__':
    main()
