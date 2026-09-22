"""Fail-closed display policy. Unknown/stale location renders skeleton only."""
import json
import time
from pathlib import Path
POLICY=Path(__file__).resolve().parents[1]/'guardian_data/privacy.json'
def skeleton_required(now=None):
    now=time.time() if now is None else now
    try:
        p=json.loads(POLICY.read_text(encoding='utf-8'))
        if p.get('mode')=='skeleton':return True
        if p.get('mode')=='manual_video':return False
        if p.get('mode')!='auto':return True
        # Only explicitly configured, fresh public-zone evidence may show video.
        return not (p.get('private') is False and p.get('location_verified') is True
                    and 0<=now-float(p.get('updated',0))<=5)
    except (OSError,ValueError,TypeError):return True
