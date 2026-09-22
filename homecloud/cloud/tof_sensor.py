import time
from collections import deque
# === 修改 tof_sensor.py 開頭 ===
import time
from collections import deque
import random  # 新增

print("[TOF] 開始載入模組...")
try:
    import board
    import busio
    import adafruit_vl53l0x
    print("[TOF] board / busio / adafruit_vl53l0x 載入成功")
    HARDWARE_AVAILABLE = True
except Exception as e:
    print(f"[TOF] 模組載入失敗: {e}")
    board = None
    busio = None
    adafruit_vl53l0x = None
    HARDWARE_AVAILABLE = False  # 標記為不可用

class TOFSensor:
    def __init__(self, window_size=5):
        self.window_size = window_size
        self.history = deque(maxlen=window_size)
        self.sensor = None
        self.available = False

        if not HARDWARE_AVAILABLE:
            print("[TOF] 目前平台不支援或套件不可用，啟用模擬模式")
            self.available = True  # 改為可用，但使用模擬數據
            return

        try:
            print("[TOF] 嘗試建立 I2C... ")
            i2c = busio.I2C(board.SCL, board.SDA)
            print("[TOF] 嘗試初始化 VL53L0X... ")
            self.sensor = adafruit_vl53l0x.VL53L0X(i2c)
            self.available = True
            print("[TOF] VL53L0X 初始化成功 ")
        except Exception as e:
            print(f"[TOF] 初始化失敗: {e} ")
            self.available = False

    def get_distance_mm(self):
        if not self.available:
            return None

        if not HARDWARE_AVAILABLE:
            # === 模擬模式：隨機產生 200~800mm 的距離 ===
            return random.randint(200, 800)

        try:
            return self.sensor.range
        except Exception as e:
            print(f"[TOF] 讀取失敗: {e} ")
            return None

    def get_distance_m(self):
        distance_mm = self.get_distance_mm()
        if distance_mm is None:
            return None
        return distance_mm / 1000.0

    def get_smoothed_distance_m(self):
        distance_m = self.get_distance_m()
        if distance_m is None:
            return None
        self.history.append(distance_m)
        return sum(self.history) / len(self.history)
print("[TOF] 開始載入模組...")

try:
    import board
    import busio
    import adafruit_vl53l0x
    print("[TOF] board / busio / adafruit_vl53l0x 載入成功")
except Exception as e:
    print(f"[TOF] 模組載入失敗: {e}")
    board = None
    busio = None
    adafruit_vl53l0x = None


class TOFSensor:
    def __init__(self, window_size=5):
        self.window_size = window_size
        self.history = deque(maxlen=window_size)
        self.sensor = None
        self.available = False

        if board is None or busio is None or adafruit_vl53l0x is None:
            print("[TOF] 目前平台不支援或套件不可用，TOF 功能停用")
            return

        try:
            print("[TOF] 嘗試建立 I2C...")
            i2c = busio.I2C(board.SCL, board.SDA)

            print("[TOF] 嘗試初始化 VL53L0X...")
            self.sensor = adafruit_vl53l0x.VL53L0X(i2c)

            self.available = True
            print("[TOF] VL53L0X 初始化成功")
        except Exception as e:
            print(f"[TOF] 初始化失敗: {e}")
            self.available = False

    def get_distance_mm(self):
        if not self.available or self.sensor is None:
            return None

        try:
            return self.sensor.range
        except Exception as e:
            print(f"[TOF] 讀取失敗: {e}")
            return None

    def get_distance_m(self):
        distance_mm = self.get_distance_mm()
        if distance_mm is None:
            return None
        return distance_mm / 1000.0

    def get_smoothed_distance_m(self):
        distance_m = self.get_distance_m()
        if distance_m is None:
            return None

        self.history.append(distance_m)
        return sum(self.history) / len(self.history)


if __name__ == "__main__":
    tof = TOFSensor(window_size=5)

    if not tof.available:
        print("[TOF] 感測器不可用，程式結束")
    else:
        while True:
            d_raw = tof.get_distance_m()
            d_avg = tof.get_smoothed_distance_m()
            print(f"原始距離: {d_raw} m, 平滑距離: {d_avg} m")
            time.sleep(0.5)