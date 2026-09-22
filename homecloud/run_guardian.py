import argparse
import threading
import logging
from guardian.server import create_app
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=5006)
    p.add_argument('--host',default='127.0.0.1'); a=p.parse_args()
    app=create_app()
    def maintain():
        while True:
            try: app.extensions['guardian_maintenance']()
            except Exception: logging.exception('Guardian image retention failed; retry in one hour')
            threading.Event().wait(3600)
    threading.Thread(target=maintain,daemon=True).start()
    app.run(host=a.host,port=a.port,debug=False,use_reloader=False)
