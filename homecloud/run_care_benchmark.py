"""Sequential replay of labelled development videos; never writes production events."""
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
if __name__=='__main__':
    manifest=json.loads((ROOT/'validation_output/video_manifest.json').read_text(encoding='utf-8'))
    for index,video in enumerate(manifest['videos']):
        if video.get('expected') not in ('review','none'):
            print('Skip unlabelled video:',video['file']);continue
        result=subprocess.run([sys.executable,str(ROOT/'validate_care_video.py'),str(ROOT/video['file']),'--expect',video['expected'],'--speed','1','--output',str(ROOT/f'validation_output/replay_{index}.json')],cwd=ROOT)
        if result.returncode not in (0,1):raise SystemExit(result.returncode)
    print('Reports: validation_output/replay_*.json. These are clip-level checks, not clinical accuracy or latency estimates.')
