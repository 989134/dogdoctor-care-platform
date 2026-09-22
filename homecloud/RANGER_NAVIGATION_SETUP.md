# Ranger 導航感測升級與實測

這套檔案提供左右輪編碼器遙測、前向超音波與可靠的 `DONE` 交易。超音波已確認接在 `PORT_10`。目前 V2 韌體把長達一秒的開迴路動作改成編碼器固定小步，準備供實車巡檢使用。

## 檔案

- `firmware/ranger_navigation_telemetry/ranger_navigation_telemetry.ino`：Me Auriga 候選韌體。
- `ranger_gateway.py`：樹莓派唯一的序列埠擁有者，提供 `/health`、`/telemetry`、`/control`。
- `verify_ranger_bench.py`：電腦端唯讀檢查及架空車輪測試。
- `navigation/calibrate_encoder.py`：把脈衝換算為公尺及估計輪距。

## 1. 編譯與燒錄

Arduino IDE 選擇 `Arduino Mega or Mega 2560`、處理器 `ATmega2560`，確認已安裝 Makeblock 函式庫，再開啟候選韌體編譯及上傳。序列監控視窗設為 9600 baud，正常啟動應看到：

本專案已用 Arduino Mega 與 Makeblock 官方函式庫完成編譯驗證；V2 使用 16,100 bytes 程式空間及 1,759 bytes 動態記憶體。這只證明程式可編譯，實際步距仍需上傳後驗證。

```text
BOARD:MBOT_RANGER_AURIGA_TELEMETRY_V2_ENCODER_STEPS
TEL:t_ms=...,left_pulse=...,right_pulse=...,...
```

不可同時開著 Arduino 序列監控與樹莓派服務，兩者會搶同一序列埠。

## 2. 在樹莓派啟動唯一閘道

把最新版 `ranger_gateway.py` 與 `navigation` 資料夾放在樹莓派同一個 `homecloud` 資料夾。V2 最長等待 2 秒，因此必須使用目前 `DONE` 等待 2.7 秒的閘道版本。先停止舊服務，再執行：

```bash
cd ~/homecloud
~/dogdoctor-venv/bin/python ranger_gateway.py --port /dev/ttyUSB0 --baud 9600
```

若重新插拔後埠名改變，先用 `ls -l /dev/serial/by-id/` 找穩定裝置路徑，再把該路徑交給 `--port`。

## 3. 電腦端唯讀確認

在 Windows 的 `homecloud` 資料夾執行：

```powershell
python verify_ranger_bench.py
```

通過條件：`serial_open=true`、板名正確、`telemetry_fresh=true`，而且 `left_pulse`、`right_pulse` 欄位存在。這一步不會轉動馬達。

## 4. 架空車輪測試

確認四個輪子完全離地、周圍沒有人或纏繞物，再執行：

```powershell
python verify_ranger_bench.py --confirm-wheels-lifted
```

程式依序送出前進、後退、左轉、右轉，並將結果寫入 `navigation/output/ranger_bench.json`。V2 正常完成時 `DONE` 原因應是 `encoder_target`；若出現 `motion_timeout`，表示至少一側沒有達到目標脈衝。每個方向都應收到相符 `DONE`，左右輪脈衝差也都不可為零。

V2 直接單一請求已量得前進每步約 10.5 cm；左右轉目標約 20°，仍待新版韌體地面驗證。實車驗證前應將車放在空曠地面，並準備立即按停止。

## 5. 地面里程校正

在空曠平整地面標出 1 公尺。記下直走前後兩輪的累積脈衝差，再原地旋轉一圈並記下兩輪脈衝差：

```powershell
python navigation\calibrate_encoder.py --distance-m 1 `
  --straight-left-pulses 直走左輪差值 --straight-right-pulses 直走右輪差值 `
  --turn-deg 360 --turn-left-pulses 旋轉左輪差值 --turn-right-pulses 旋轉右輪差值
```

輸出的 `navigation/calibration.json` 才能用於實車里程計。輪子架空的轉數不能用於地面校正，因為它沒有包含輪胎打滑和實際負載。

## 目前驗證範圍

- 單一前向超音波只能緊急避障，不能產生目前模擬器使用的 360 度距離射線。
- Depth Anything V2 提供相對深度；未經尺度校正及實景驗證前不能直接當公尺距離建圖。
- 已完成實車同步資料重播、輪編碼器定位、單目前景追蹤與起點視覺迴環；這仍是 2D 原型，尚未達到成熟 SLAM 套件的全域姿態圖優化能力。
- 實車自主巡檢輸出仍需 V2 固定小步韌體、緊急停止與第一條短距離路線測試通過後才能啟用。
