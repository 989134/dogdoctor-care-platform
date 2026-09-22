"""Replay one recorded camera-height video through the production care service."""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'eldertest'))
from care_engine import Settings
from care_service import CareService


def main():
    parser = argparse.ArgumentParser(description='驗證裝車視角的跌倒／日常動作影片')
    parser.add_argument('video', type=Path)
    parser.add_argument('--expect', choices=('review', 'none'), required=True,
                        help='review=應提出人工確認；none=不應產生照護事件')
    parser.add_argument('--settings', type=Path, default=ROOT/'care_settings.json')
    parser.add_argument('--speed', type=float, default=1.0,
                        help='播放速度；1 為即時，2 為兩倍速')
    parser.add_argument('--output',type=Path,help='另存 JSON 驗證報告')
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error('--speed 必須大於 0')

    settings = Settings(**json.loads(args.settings.read_text(encoding='utf-8')))
    service = CareService(settings)
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f'無法開啟影片：{args.video}')
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    frames = 0
    service.start()
    if not service.ready.wait(10):
        service.close()
        raise SystemExit('姿態模型啟動逾時')
    began = time.monotonic()
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            service.submit(frame)
            frames += 1
            deadline = began + frames / fps / args.speed
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
        time.sleep(.25)
        state = service.snapshot()
    finally:
        capture.release()
        service.close()

    care_events = []
    for original in state['events']:
        if original['kind'] not in ('fall', 'tracking_review', 'immobile', 'wandering'):
            continue
        event = dict(original)
        event['video_seconds'] = round(max(0, (event['created_at']-began)*args.speed), 2)
        care_events.append(event)
    observed_review = any(event['kind'] in ('fall', 'tracking_review') for event in care_events)
    passed = observed_review if args.expect == 'review' else not care_events
    report = {
        'passed': passed,
        'video': str(args.video.resolve()),
        'frames': frames,
        'source_fps': round(fps, 2),
        'expected': args.expect,
        'events': care_events,
        'final_posture': state['posture'],
        'final_observation_mode': state['observation_mode'],
    }
    output=json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output,encoding='utf-8')
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
