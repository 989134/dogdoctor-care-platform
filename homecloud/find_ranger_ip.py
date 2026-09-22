"""Find the Raspberry Pi Ranger gateway on the current Wi-Fi network."""

from __future__ import annotations

import argparse
import json

from navigation.local_network import active_ipv4, discover_ranger


def main():
    parser = argparse.ArgumentParser(description="自動尋找 Ranger 樹莓派 IP")
    parser.add_argument("--local-ip", help="指定電腦區網 IP；通常不需要填")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    local_ip = args.local_ip or active_ipv4()
    try:
        ip, health = discover_ranger(local_ip=local_ip, port=args.port)
    except RuntimeError as exc:
        print(json.dumps({
            "ok": False,
            "computer_ip": local_ip,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(2) from None
    print(json.dumps({
        "ok": True,
        "computer_ip": local_ip,
        "ranger_ip": ip,
        "gateway_url": f"http://{ip}:{args.port}",
        "camera_url": f"http://{ip}:5001/video_feed",
        "board": health.get("board"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
