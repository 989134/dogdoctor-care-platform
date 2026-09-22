# safety_logger.py
import csv
import datetime
import os
import safety_config

LOG_FILE = "safety_session.csv"

def init_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "patient_id", "mode", "event",
                "confidence", "details", "action_taken"
            ])

def log_event(event, confidence=0.0, details="", action_taken="None"):
    try:
        now = datetime.datetime.now().isoformat()
        with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                now, safety_config.patient_id, safety_config.mode, event,
                f"{confidence:.2f}", details, action_taken
            ])
        print(f"[LOG] ✅ {event} | Conf: {confidence:.2f} | Action: {action_taken}")
    except Exception as e:
        print(f"[LOG ERROR] {e}")