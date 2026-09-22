import cv2

RTSP_URL = "rtsp://10.121.187.243:8554/mystream"

print(f"🔗 嘗試連接：{RTSP_URL}")
cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("❌ 無法開啟 RTSP 串流！")
    print("💡 請確認：")
    print("   1. 樹莓派上的 MediaMTX 有在運行")
    print("   2. 有程式正在發布串流到 'mystream'")
    print("   3. 防火牆允許 8554 端口")
    exit()

print("✅ RTSP 串流連接成功！")

while True:
    ret, frame = cap.read()
    if not ret:
        print("⚠️ 讀取失敗，重連中...")
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        continue

    cv2.imshow("RTSP Test", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()