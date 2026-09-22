# generate_report.py
import pandas as pd
import sys
import matplotlib.pyplot as plt

CSV_FILE = "safety_session.csv"

try:
    df = pd.read_csv(CSV_FILE)
except FileNotFoundError:
    print("❌ 找不到 safety_session.csv，請先執行 main_safety.py")
    sys.exit(1)

if df.empty:
    print("⚠️ 暫無記錄數據")
    sys.exit(0)

print("=" * 60)
print("🏥【居家安全監控報告】")
print(f"用戶 ID: {df['patient_id'].iloc[0]}")
print(f"監測模式：{df['mode'].iloc[0]}")
print("-" * 40)

# 1. 安全事件統計
events = df['event'].value_counts()
print("🔹 事件統計：")
for event, count in events.items():
    print(f"   {event}: {count} 次")

# 2. 警報可信度分析
if 'FALL_DETECTED' in events.index:
    fall_data = df[df['event'] == 'FALL_DETECTED']
    avg_conf = fall_data['confidence'].mean()
    print(f"🔹 跌倒偵測平均可信度：{avg_conf:.2%}")

    # 簡單繪圖 (儲存為圖片方便比賽展示)
    try:
        plt.figure(figsize=(6, 4))
        plt.plot(fall_data.index, fall_data['confidence'], marker='o')
        plt.title('Fall Detection Confidence Over Time')
        plt.xlabel('Event Index')
        plt.ylabel('Confidence')
        plt.ylim(0, 1)
        plt.grid(True)
        plt.savefig('safety_report_chart.png')
        print("📊 圖表已儲存為 safety_report_chart.png")
    except Exception as e:
        print(f"⚠️ 繪圖失敗：{e}")

print("=" * 60)
print("💡 系統建議：")
if len(df[df['event'] == 'FALL_DETECTED']) > 0:
    print("→ 偵測到潛在跌倒風險，建議家屬確認長輩狀況。")
else:
    print("→ 監測期間未发现異常，長輩活動穩定。")