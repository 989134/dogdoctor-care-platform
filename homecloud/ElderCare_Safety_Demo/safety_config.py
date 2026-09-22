# safety_config.py

# 模式選項："Privacy", "Monitor"
mode = "Privacy"

# 輸入源設定
# 設為 0 使用攝影機
# 設為 "test_fall.mp4" 使用影片檔
video_source = "test_fall.mp4"

patient_id = "ELDER_001"

# 跌倒判定閾值
fall_threshold_ratio = 0.6
# 連續偵測到跌倒多少幀才確認 (防止誤報)
fall_frame_threshold = 15