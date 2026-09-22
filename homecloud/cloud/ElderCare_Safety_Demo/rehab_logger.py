# rehab_logger.py
import csv
import datetime
import os
import sys

try:
    import rehab_config
    PATIENT_ID = getattr(rehab_config, 'patient_id', 'P001')
    MODE = getattr(rehab_config, 'mode', 'Manual')
except Exception as e:
    print(f"[LOGGER WARNING] 無法載入 rehab_config: {e}")
    PATIENT_ID = "UNKNOWN"
    MODE = "Manual"

LOG_FILE = "rehab_session.csv"

def init_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "patient_id", "mode", "event",
                "x_px", "y_px", "depth_m", "hand_open", "reach_delay_s",
                "touch_error_px", "success"
            ])

def log_event(event, x=None, y=None, depth_m=None, hand_open=None,
              reach_delay=None, touch_error=None, success=False):
    try:
        now = datetime.datetime.now().isoformat()
        with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                now, PATIENT_ID, MODE, event,
                x, y, depth_m, hand_open, reach_delay,
                touch_error, int(success)
            ])
        print(f"[LOG] ✅ {event} | px=({x},{y}) | depth={depth_m:.2f}m | success={success}")
    except Exception as e:
        print(f"[LOG ERROR] 寫入失敗：{e} | event={event}")