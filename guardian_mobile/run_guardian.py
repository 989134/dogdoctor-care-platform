import argparse
import threading
import logging
from pathlib import Path
from guardian.server import create_app
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=5006)
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--data-dir',type=Path,default=None,
                   help='手機監護者帳號、設定與稽核資料目錄')
    p.add_argument('--source-dir',type=Path,default=None,
                   help='唯讀讀取 homecloud care_events 的 mec_output 目錄')
    a=p.parse_args()
    app=create_app(a.data_dir,a.source_dir)
    def maintain():
        while True:
            try: app.extensions['guardian_maintenance']()
            except Exception: logging.exception('Guardian image retention failed; retry in one hour')
            threading.Event().wait(3600)
    threading.Thread(target=maintain,daemon=True).start()
    def workflow_worker():
        while True:
            try:
                app.extensions['guardian_source_sync']()
                app.extensions['workflow_tick']()
            except Exception: logging.exception('Care workflow worker failed; retrying')
            threading.Event().wait(1)
    threading.Thread(target=workflow_worker,daemon=True).start()
    def notification_worker():
        while True:
            try: app.extensions['notification_tick']()
            except Exception: logging.exception('Notification worker failed; retrying')
            threading.Event().wait(5)
    threading.Thread(target=notification_worker,daemon=True).start()
    def sms_worker():
        while True:
            try: app.extensions['sms_tick']()
            except Exception: logging.exception('SMS worker failed; retrying')
            threading.Event().wait(5)
    threading.Thread(target=sms_worker,daemon=True).start()
    app.run(host=a.host,port=a.port,debug=False,use_reloader=False)
