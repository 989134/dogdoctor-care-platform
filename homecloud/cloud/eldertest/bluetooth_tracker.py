# bluetooth_tracker.py
import subprocess
import re
import time
import logging

logger = logging.getLogger(__name__)


class BluetoothTracker:
    def __init__(self, target_mac=None):
        """
        藍牙追蹤器
        target_mac: 手機的藍牙 MAC 地址（可選，如果不填會自動掃描）
        """
        self.target_mac = target_mac
        self.last_rssi = None
        self.last_seen_time = 0
        self.is_connected = False

    def scan_bluetooth_devices(self, duration=5):
        """掃描周邊藍牙設備"""
        try:
            # 使用 hcitool 掃描（樹莓派內建）
            result = subprocess.run(
                ['sudo', 'hcitool', 'lescan', '--duplicates'],
                capture_output=True,
                text=True,
                timeout=duration
            )

            devices = []
            for line in result.stdout.split('\n'):
                # 解析 MAC 地址和 RSSI
                match = re.match(r'([0-9A-Fa-f:]+)\s+(.*)', line)
                if match:
                    mac = match.group(1)
                    name = match.group(2).strip()
                    devices.append({'mac': mac, 'name': name})

            logger.info(f"掃描到 {len(devices)} 個藍牙設備")
            return devices

        except Exception as e:
            logger.error(f"藍牙掃描失敗：{e}")
            return []

    def get_rssi(self, mac_address, duration=3):
        """獲取特定設備的 RSSI 信號強度"""
        try:
            # 使用 hcitool 獲取 RSSI
            result = subprocess.run(
                ['sudo', 'hcitool', 'rssi', mac_address],
                capture_output=True,
                text=True,
                timeout=duration
            )

            # 解析 RSSI 值
            rssi_match = re.search(r'RSSI return code: (-?\d+)', result.stdout)
            if rssi_match:
                rssi = int(rssi_match.group(1))
                self.last_rssi = rssi
                self.last_seen_time = time.time()
                self.is_connected = True
                logger.info(f"設備 {mac_address} RSSI: {rssi}")
                return rssi

            return None

        except Exception as e:
            logger.error(f"獲取 RSSI 失敗：{e}")
            return None

    def estimate_distance(self, rssi):
        """
        根據 RSSI 估算距離（米）
        RSSI 越接近 0 = 距離越近
        RSSI 越負 = 距離越遠
        """
        if rssi is None:
            return None

        # 簡化距離估算公式
        if rssi >= -50:
            return "非常近 (<1m)"
        elif rssi >= -60:
            return "近 (1-3m)"
        elif rssi >= -70:
            return "中 (3-5m)"
        elif rssi >= -80:
            return "遠 (5-10m)"
        else:
            return "非常遠 (>10m)"

    def get_phone_distance(self):
        """獲取手機距離（主要介面）"""
        if self.target_mac:
            rssi = self.get_rssi(self.target_mac)
            if rssi:
                return self.estimate_distance(rssi), rssi
        return None, None

    def start_tracking(self):
        """啟動藍牙服務"""
        try:
            # 啟動藍牙服務
            subprocess.run(['sudo', 'systemctl', 'start', 'bluetooth'], check=True)
            logger.info("✅ 藍牙服務已啟動")
            return True
        except Exception as e:
            logger.error(f"啟動藍牙服務失敗：{e}")
            return False


# 全域實例
bluetooth_tracker = BluetoothTracker()