# MEC 逐步測試紀錄

## 步驟 1：本機 MEC 基準（已通過）

實際筆電鏡頭結果：50/50 張成功、0 張失敗，RTT p50 78 ms、p95 94 ms、p99 109 ms；服務處理 p50 78 ms、p95 79 ms、p99 94 ms。此結果是同一台 Windows 電腦的本機基準，不代表 5G 或正式 MEC 延遲。

健康頁在傳送前顯示 `devices: 0` 屬正常；第一個有效裝置傳送後顯示 `devices: 1`。

## 步驟 2：非阻塞 Edge Agent（已通過）

正式執行使用 `mec_edge_agent.py`。本地安全判斷與 MEC 上傳分別在不同流程運作，因此 MEC 超時不會卡住每一張本地影像。

本機迴路實測（2026-09-12，錄影檔、5 FPS）：

- 線上：106/106 張完成本地安全推論，MEC 106 張成功、0 張失敗。
- 穩態 RTT：p50 63 ms、p95 79 ms、p99 79 ms。
- 本地安全結果可用時間：p50 62 ms、p95 63 ms。
- 上行 JPEG 平均估算：1.671 Mbps。
- 產生 1 筆事件、送達 1 筆、待補送 0 筆。

證據檔：`mec_output/edge_online_report.json`。這是同一台電腦的本機 MEC 模擬結果，不能當成 5G 或中華電信 MEC 的網路延遲。

## 步驟 3：MEC 中途斷線與自動恢復（已通過）

保持有驗證權杖的 MEC 服務運作，在第二個 PowerShell 執行：

```powershell
$env:MEC_API_TOKEN="<與服務端相同的測試權杖>"
$env:EVENT_SINK_URL="http://127.0.0.1:7000/v1/events"
$env:EVENT_SINK_TOKEN=$env:MEC_API_TOKEN
python mec_edge_agent.py --source ".\test_videos\care_fall_01.mp4" `
  --frames 60 --fps 5 --set-baseline `
  --outage-start-frame 16 --outage-frames 20 --timeout 0.5 `
  --report mec_output\edge_recovery_report.json
```

2026-09-12 實測結果：60/60 張持續完成本地安全推論；第 16 張起模擬中斷，MEC 連線進入離線退避；第 36 張恢復後立即重試成功，最後狀態回到 `online`。MEC 成功處理 40 張、失敗 4 張、離線時跳過 13 張、忙碌時淘汰 3 張舊影格，沒有把影像累積到磁碟。MEC 端環境基準同步成功。證據檔：`mec_output/edge_recovery_report.json`。

照護頁會讀取 `mec_output/edge_status.json` 並顯示 `MEC 線上`、`MEC 離線／本地備援`、`Edge Agent 已停止` 及待補送事件筆數。

## 舊版同步式測試（保留作比較）

保持 `mec_service.py` 在 port 7000 運作，在第二個 PowerShell 執行：

```powershell
cd "C:\Users\lin40\Desktop\居家照護\homecloud"
python mec_edge_client.py --source 0 --frames 60 --fps 5 --set-baseline `
  --outage-start-frame 21 --outage-frames 20 --timeout 0.3 `
  --output mec_output\laptop_outage.json
```

此命令的 1–20 與 41–60 張送到 MEC，第 21–40 張刻意送到不可用端點。通過條件：

```json
{
  "frames_attempted": 60,
  "frames_succeeded": 60,
  "frames_failed": 0,
  "success_rate": 1.0,
  "mec_frames": 40,
  "local_fallback_frames": 20,
  "mec_request_failures": 20
}
```

若電腦速度不足，影格比例仍應符合，但總執行時間可以超過 12 秒。

程式也已通過「啟動時 MEC 完全離線」測試：3/3 張皆由本地備援處理，沒有退出或遺失影格。

## 樹莓派串流狀態

`172.20.10.2` 可以 ping 到，但 port 5001 目前沒有服務監聽。重新啟動樹莓派串流後才使用以下純文字網址；命令中不要包含 Markdown 的方括號或括號：

```powershell
python mec_edge_client.py --source "http://172.20.10.2:5001/video_feed" --frames 50 --fps 5 --set-baseline
```
