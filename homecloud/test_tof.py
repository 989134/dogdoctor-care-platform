# 存成 test_tof.py
import smbus2
import time


class VL53L0X:
    def __init__(self, bus, address=0x29):
        self.bus = bus
        self.address = address

    def read_distance(self):
        try:
            # 啟動測距
            self.bus.write_byte(self.address, 0x00, 0x01)
            time.sleep(0.01)

            # 讀取距離
            distance = self.bus.read_byte_data(self.address, 0x14)
            return distance
        except:
            return None


# 測試
bus = smbus2.SMBus(1)
tof = VL53L0X(bus, 0x29)

print("開始測試 VL53L0X...")
print("按 Ctrl+C 停止")

try:
    while True:
        dist = tof.read_distance()
        if dist is not None:
            print(f"\r距離: {dist:3d} mm", end="")
        else:
            print("\r讀取失敗", end="")
        time.sleep(0.1)
except KeyboardInterrupt:
    print("\n測試結束")