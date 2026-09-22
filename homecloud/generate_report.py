
import pandas as pd
import sys

CSV_FILE = "rehab_session.csv"

try:
    df = pd.read_csv(CSV_FILE)
except FileNotFoundError:
    print("❌ 找不到 rehab_session.csv，請先執行 local_test_rehab.py")
    sys.exit(1)

# 過濾有效事件
touches = df[df['event'] == 'patient_touch']
reaches = df[df['event'] == 'reach_start']
targets = df[df['event'] == 'target_selected']

print("=" * 60)
print("🏥【復健訓練報告】")
print(f"患者ID: {df['patient_id'].iloc[0] if not df.empty else 'N/A'}")
print(f"日期: {df['timestamp'].iloc[0][:10] if not df.empty else 'N/A'}")
print(f"總嘗試次數: {len(targets)}")
print("-" * 40)

# 1. 主動參與指標
if not touches.empty:
    success_rate = len(touches) / len(reaches) if len(reaches) > 0 else 0
    avg_delay = touches['reach_delay_s'].mean()
    avg_error = touches['touch_error_px'].mean()

    print(f"🔹 觸碰成功率: {success_rate:.1%}")
    print(f"🔹 平均伸手延遲: {avg_delay:.2f} 秒")
    print(f"🔹 平均觸碰誤差: {avg_error:.1f} px（≈ {avg_error * 0.5:.1f} cm）")

# 2. 距離穩定性
if not touches.empty:
    depth_std = touches['depth_m'].std()
    print(f"🔹 距離標準差: {depth_std:.2f} m → 值越小越穩定")

# 3. 時間趨勢（前後 5 次比較）
if len(touches) >= 10:
    first5 = touches.iloc[:5]['reach_delay_s'].mean()
    last5 = touches.iloc[-5:]['reach_delay_s'].mean()
    print(f"🔹 前5次 vs 後5次延遲: {first5:.2f}s → {last5:.2f}s （↓{first5 - last5:.2f}s）")

print("=" * 60)
print("💡 建議：")
if success_rate > 0.8 and avg_delay < 2.0:
    print("→ 患者主動參與度高，建議提升難度（如增加距離/加入干擾物）")
elif avg_delay > 3.0:
    print("→ 運動計劃時間偏長，建議加入視覺提示訓練模組")
else:
    print("→ 穩定進步中，持續訓練即可")

# 可選：輸出為 CSV summary
summary = {
    "patient_id": df['patient_id'].iloc[0],
    "session_date": df['timestamp'].iloc[0][:10],
    "total_trials": len(targets),
    "success_rate": float(success_rate) if 'success_rate' in locals() else 0,
    "avg_reach_delay": float(avg_delay) if 'avg_delay' in locals() else 0,
    "avg_touch_error_px": float(avg_error) if 'avg_error' in locals() else 0,
    "depth_std_m": float(depth_std) if 'depth_std' in locals() else 0
}
pd.DataFrame([summary]).to_csv("rehab_summary.csv", index=False)
print("\n✅ 摘要已儲存至 rehab_summary.csv")