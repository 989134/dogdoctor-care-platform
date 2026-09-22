# generate_safety_report.py
import pandas as pd
import matplotlib.pyplot as plt
import sys
import os
import datetime

# 設定 matplotlib 支援中文顯示
plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'Arial Unicode MS', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

CSV_FILE = "safety_session.csv"
CHART_FILE = "safety_report_chart.png"

try:
    df = pd.read_csv(CSV_FILE)
except FileNotFoundError:
    print("❌ 找不到 safety_session.csv，請先執行 main_safety.py")
    sys.exit(1)

if df.empty:
    print("⚠️ 暫無記錄數據")
    sys.exit(0)

print("=" * 60)
print("🏥【居家安全監控分析報告】")
print(f"用戶 ID: {df['patient_id'].iloc[0]}")
print(f"總記錄筆數：{len(df)}")
print("-" * 40)

# 1. 事件統計
events = df['event'].value_counts()
print("🔹 事件統計：")
for event, count in events.items():
    print(f"   {event}: {count} 次")

# 2. 繪製「時間軸事件圖」 (比賽亮點：可視化時間點)
if 'FALL_DETECTED' in events.index:
    fall_data = df[df['event'] == 'FALL_DETECTED'].copy()

    # 轉換時間格式以便繪圖
    fall_data['timestamp'] = pd.to_datetime(fall_data['timestamp'])
    fall_data['relative_time'] = (fall_data['timestamp'] - fall_data['timestamp'].iloc[0]).dt.total_seconds()

    avg_conf = fall_data['confidence'].mean()
    print(f"🔹 跌倒偵測平均可信度：{avg_conf:.2%}")

    # 繪圖
    plt.figure(figsize=(10, 5))
    # 使用散點圖標示跌倒發生時間
    plt.scatter(fall_data['relative_time'], fall_data['confidence'],
                color='red', s=150, marker='x', label='Fall Detected', zorder=5)

    # 畫一條基準線
    plt.axhline(y=0.8, color='orange', linestyle='--', label='Alert Threshold (0.8)', alpha=0.5)

    plt.title(f'跌倒事件時間軸分析 (共 {len(fall_data)} 次事件)')
    plt.xlabel('監測開始後經過時間 (秒)')
    plt.ylabel('偵測可信度 (Confidence)')
    plt.ylim(0, 1.1)
    plt.grid(True, alpha=0.3)
    plt.legend()

    # 在每個點旁邊標註時間
    for i, row in fall_data.iterrows():
        plt.annotate(f"{row['relative_time']:.1f}s",
                     (row['relative_time'], row['confidence']),
                     textcoords="offset points", xytext=(0, 10), ha='center')

    plt.savefig(CHART_FILE, dpi=300, bbox_inches='tight')
    print(f"📊 圖表已儲存為 {CHART_FILE}")

    # ✅ 自動打開圖片 (Windows)
    try:
        os.startfile(CHART_FILE)
        print("🖼️  已自動開啟圖表檢視")
    except:
        print("⚠️  請手動打開 safety_report_chart.png 查看")

else:
    print("🔹 暫無跌倒事件記錄")

print("=" * 60)
# 生成摘要
summary = {
    "patient_id": df['patient_id'].iloc[0],
    "total_events": len(df),
    "fall_events": events.get('FALL_DETECTED', 0),
    "avg_confidence": float(avg_conf) if 'FALL_DETECTED' in events.index else 0.0
}
pd.DataFrame([summary]).to_csv("safety_summary.csv", index=False)
print("✅ 摘要已儲存至 safety_summary.csv")