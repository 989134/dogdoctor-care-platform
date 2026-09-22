# test_pose_simple.py
import cv2
import mediapipe as mp

# ==================== 初始化 MediaPipe Pose ====================
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(
    static_image_mode=False,
    model_complexity=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
mp_drawing = mp.solutions.drawing_utils

# ==================== 開啟攝影機 ====================
cap = cv2.VideoCapture(0)

# 設定解析度（降低可提升速度）
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print("❌ 無法開啟攝影機！")
    print("   請檢查：")
    print("   1. 攝影機是否連接正常")
    print("   2. 是否有其他程式占用攝影機（Zoom、Teams 等）")
    print("   3. 攝影機權限是否已允許")
    exit()

print("=" * 60)
print("🦴 MediaPipe Pose 骨架追蹤測試")
print("=" * 60)
print("📌 操作說明：")
print("  1. 站在鏡頭前，確保全身在畫面中")
print("  2. 光線要充足，避免背光")
print("  3. 按 'q' 鍵退出")
print("  4. 按 's' 鍵截圖")
print("=" * 60)

# ==================== 主循環 ====================
frame_count = 0
detect_count = 0

while True:
    ret, frame = cap.read()
    if not ret:
        print("⚠️ 無法讀取攝影機畫面")
        break

    frame_count += 1

    # 轉換為 RGB（MediaPipe 需要）
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # 進行姿勢偵測
    results = pose.process(rgb)

    # 如果偵測到骨架
    if results.pose_landmarks:
        detect_count += 1

        # 繪製骨架
        mp_drawing.draw_landmarks(
            frame,
            results.pose_landmarks,
            mp_pose.POSE_CONNECTIONS,
            mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=2),
            mp_drawing.DrawingSpec(color=(255, 0, 0), thickness=2, circle_radius=2)
        )

        # 顯示偵測狀態
        cv2.putText(frame, "✅ Person Detected", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        # 顯示關鍵點數量
        landmark_count = len(results.pose_landmarks.landmark)
        cv2.putText(frame, f"Landmarks: {landmark_count}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    else:
        cv2.putText(frame, "❌ No Person", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

    # 顯示 FPS
    fps = detect_count / frame_count * 30 if frame_count > 0 else 0
    cv2.putText(frame, f"FPS: {fps:.1f}", (10, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # 顯示偵測率
    detect_rate = (detect_count / frame_count * 100) if frame_count > 0 else 0
    cv2.putText(frame, f"Detect Rate: {detect_rate:.1f}%", (10, 115),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # 顯示畫面
    cv2.imshow("MediaPipe Pose Test", frame)

    # 按鍵處理
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('s'):
        # 截圖
        cv2.imwrite(f"pose_snapshot_{frame_count}.jpg", frame)
        print(f"📸 已截圖：pose_snapshot_{frame_count}.jpg")

# ==================== 清理 ====================
cap.release()
cv2.destroyAllWindows()

print("=" * 60)
print("📊 測試結果：")
print(f"  總幀數：{frame_count}")
print(f"  偵測到骨架：{detect_count} 幀")
print(f"  偵測率：{detect_count / frame_count * 100:.1f}%")
print("=" * 60)