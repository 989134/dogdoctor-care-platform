"""Start the supervised real Ranger patrol dashboard."""

import argparse
from pathlib import Path

from navigation.local_network import discover_ranger
from navigation.real_patrol import RealPatrolRuntime
from navigation.real_patrol_web import create_app


def main():
    parser = argparse.ArgumentParser(description="Ranger 實車自主巡檢")
    parser.add_argument("--localization-url", default="http://127.0.0.1:5005")
    parser.add_argument("--robot-ip", default="auto",
                        help="樹莓派 IP；預設自動搜尋同一個 /24 區網")
    parser.add_argument("--gateway-url", help="覆寫 Ranger 閘道網址")
    parser.add_argument("--calibration", type=Path, default=Path("navigation/calibration.json"))
    parser.add_argument("--port", type=int, default=5007)
    parser.add_argument("--enable-motion", action="store_true",
                        help="允許將巡檢狀態機輸出送到實車；省略時為唯讀預演")
    args = parser.parse_args()
    if not args.gateway_url:
        robot_ip = args.robot_ip
        if robot_ip == "auto":
            robot_ip, health = discover_ranger()
            print(f"已找到 Ranger：{robot_ip} ({health.get('board', 'unknown')})")
        args.gateway_url = f"http://{robot_ip}:5000"
    runtime = RealPatrolRuntime(
        args.localization_url, args.gateway_url, args.calibration,
        motion_enabled=args.enable_motion,
    )
    app = create_app(runtime)
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False,
                use_reloader=False, threaded=True)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
