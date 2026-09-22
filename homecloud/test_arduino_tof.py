import serial
import time
import sys

PORT_NAME = 'COM10'
BAUD_RATE = 9600

print(f"嘗試連接 {PORT_NAME} ...")

try:
    arduino = serial.Serial(PORT_NAME, BAUD_RATE, timeout=1)
    time.sleep(2)
    print(f"✅ 成功連接 {PORT_NAME}！開始監聽 TOF 數據...")
    print("按 Ctrl+C 停止測試\n")

    while True:
        if arduino.in_waiting > 0:
            line = arduino.readline().decode('utf-8').strip()

            # ✅ 只處理以 "TOF:" 開頭的數據
            if line.startswith("TOF:"):
                try:
                    # 截取 "TOF:" 後面的數字
                    dist_str = line.split(":")[1]
                    dist_mm = int(dist_str)

                    if dist_mm == -1:
                        print(f"\r📏 距離：無效數據 (超出範圍/錯誤)      ", end=" ")
                    else:
                        print(f"\r📏 距離：{dist_mm:4d} mm ({dist_mm / 1000:.2f} m)   ", end=" ")
                except ValueError:
                    # 如果轉換失敗（例如收到 TOF:abc），忽略
                    pass

            # ❌ 如果是其他數據（如 "---", "ACK:..."），選擇性打印除錯
            # else:
            #     print(f"收到雜訊：{line}")

except serial.SerialException as e:
    print(f"\n❌ 錯誤：{e}")
    sys.exit(1)
except KeyboardInterrupt:
    print("\n\n🛑 測試已手動停止。")
finally:
    if 'arduino' in locals() and arduino.is_open:
        arduino.close()