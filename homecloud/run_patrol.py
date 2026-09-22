"""Standalone autonomous patrol simulation dashboard."""

import argparse

from flask import Flask, redirect

from navigation.patrol_web import PatrolRuntime, create_patrol_blueprint


def main():
    parser = argparse.ArgumentParser(description="自主巡檢與路徑規劃模擬器")
    parser.add_argument("--port", type=int, default=5004)
    args = parser.parse_args()
    runtime = PatrolRuntime()
    app = Flask(__name__)
    app.register_blueprint(create_patrol_blueprint(runtime))
    app.add_url_rule("/", view_func=lambda: redirect("/patrol"))
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()

