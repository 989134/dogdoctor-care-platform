import cv2
import numpy as np

# 建立一個黑色畫面
img = np.zeros((300, 300, 3), dtype=np.uint8)
cv2.putText(img, "OpenCV Works!", (50, 150),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

cv2.imshow("Test Window", img)
print("✅ 視窗已開啟，請按任意鍵關閉")
cv2.waitKey(0)  # 等待按鍵
cv2.destroyAllWindows()
print("✅ 測試通過！")