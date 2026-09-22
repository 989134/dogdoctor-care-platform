"""Start the passive Ranger live localization dashboard."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# OpenCV/FFmpeg reads proxy variables while importing on some Windows builds.
# HTTP MJPEG sources are read by our direct local-network reader, but clearing
# these variables also keeps local camera behavior predictable.
_PROXY_NAMES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)
for _proxy_name in _PROXY_NAMES:
    os.environ.pop(_proxy_name, None)

from navigation.local_network import discover_ranger
from navigation.localization_web import LiveLocalizationRuntime, create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Ranger 即時定位與超音波建圖（不控制馬達）")
    parser.add_argument("--robot-ip", default="auto",
                        help="樹莓派 IP；預設自動搜尋同一個 /24 區網")
    parser.add_argument("--source", help="覆寫攝影機來源")
    parser.add_argument("--gateway-url", help="覆寫 Ranger 閘道網址")
    parser.add_argument("--calibration", type=Path, default=Path("navigation/calibration.json"))
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--vision-step", type=int, default=5)
    args = parser.parse_args()
    robot_ip = args.robot_ip
    if not args.source or not args.gateway_url:
        if robot_ip == "auto":
            robot_ip, health = discover_ranger()
            print(f"已找到 Ranger：{robot_ip} ({health.get('board', 'unknown')})")
        args.source = args.source or f"http://{robot_ip}:5001/video_feed"
        args.gateway_url = args.gateway_url or f"http://{robot_ip}:5000"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost," + str(robot_ip)
    runtime = LiveLocalizationRuntime(
        args.source, args.gateway_url, args.calibration, vision_step=args.vision_step,
    )
    runtime.start()
    app = create_app(runtime)
    try:
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
