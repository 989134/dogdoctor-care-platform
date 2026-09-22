"""Helpers for direct HTTP communication with the Ranger on the local LAN."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import socket
import urllib.error
import urllib.request


DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def direct_urlopen(request, timeout=2.0):
    """Open a local URL without inheriting Windows/system HTTP proxies."""
    return DIRECT_OPENER.open(request, timeout=timeout)


def active_ipv4() -> str:
    """Return the IPv4 address used by the current default route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    finally:
        probe.close()


def _probe_ranger(ip: str, port: int, timeout: float):
    url = f"http://{ip}:{port}/health"
    try:
        request = urllib.request.Request(url, method="GET")
        with direct_urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        board = str(payload.get("board", ""))
        if payload.get("ok") and board.startswith("MBOT_RANGER_AURIGA"):
            return ip, payload
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        pass
    return None


def discover_ranger(local_ip: str | None = None, port=5000, timeout=.6,
                    workers=64):
    """Find a Ranger gateway on the caller's /24 LAN.

    The competition setup uses phone/hotspot networks with changing DHCP
    addresses.  A bounded concurrent scan keeps discovery below a few seconds
    while validating the gateway's board identity before returning it.
    """
    local_ip = local_ip or active_ipv4()
    network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    candidates = [str(ip) for ip in network.hosts() if str(ip) != local_ip]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_probe_ranger, ip, int(port), float(timeout))
                   for ip in candidates]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                for pending in futures:
                    pending.cancel()
                return result
    raise RuntimeError(
        f"在 {network} 找不到 Ranger；請確認樹莓派已開機且與電腦連到同一個 Wi-Fi"
    )
