# 實車 SLAM 資料蒐集清單

## 先量硬體資料

已於 2026-09-13 建立 `navigation/robot_config.json`，記錄目前提供的尺寸。鏡頭角度方向與超音波韌體讀值仍待確認。

所有尺寸使用公分，角度使用度：

| 資料 | 量測方式 |
|---|---|
| 鏡頭高度 | 地面到鏡頭中心 |
| 鏡頭俯仰角 | 水平為 0，朝地面記負值 |
| 車體總寬、總長 | 含最外側輪胎 |
| 左右輪中心距 | 左右驅動輪接地中心之間的距離 |
| 輪胎直徑 | 量實際接地輪胎外徑 |
| 超音波高度與朝向 | 地面到感測器中心，並記錄朝前或有偏角 |

另確認鏡頭仍是產生 `calibration_data.pkl` 的同一顆 icSpring 相機，且原始串流解析度沒有改變。

## 一、架空車輪資料

先啟動樹莓派的 `ranger_gateway.py`，在電腦 `homecloud` 執行：

```powershell
python verify_ranger_bench.py
```

通過後將全部車輪架空，再執行：

```powershell
python verify_ranger_bench.py --confirm-wheels-lifted
```

需要保留 `navigation/output/ranger_bench.json`，並人工記錄前進、後退、左轉、右轉的實際車輪方向是否正確。

## 二、地面里程計校正

在地面貼出 1 公尺直線，車身中心對準起點：

1. 記錄起點的 `left_pulse`、`right_pulse`。
2. 讓車直走到 1 公尺終點。
3. 記錄終點 pulse，計算左右輪差值。
4. 重複三次，保留每一次的起點、終點和車身最後偏左／偏右幾公分。

再貼出車頭方向線，原地左轉 360 度及右轉 360 度，各做三次。每次記錄轉動前後的左右 pulse 差值，以及最後角度誤差。

取得一組確認過的數值後執行：

```powershell
python navigation\calibrate_encoder.py --distance-m 1 `
  --straight-left-pulses 左輪差值 --straight-right-pulses 右輪差值 `
  --turn-deg 360 --turn-left-pulses 左轉時左輪差值 --turn-right-pulses 左轉時右輪差值
```

必須產生 `navigation/calibration.json`，實車巡檢介面才有公尺尺度與輪距。

## 三、同步錄製原始影像與遙測

不要錄網頁。使用 `record_robot_run.py` 直接讀取樹莓派 `/video_feed`，程式會同步保存 `/telemetry`。

每支影片開頭靜止 3 秒，結尾靜止 3 秒。移動速度保持慢且平順，轉彎時慢轉，不要讓人長時間遮住鏡頭。

### A. 一公尺直線

```powershell
python record_robot_run.py --name straight_1m --duration 20 `
  --camera-height-cm 實測高度 --camera-pitch-deg 實測角度 --route-length-m 1 --preview
```

### B. 原地左轉一圈

```powershell
python record_robot_run.py --name turn_left_360 --duration 20 `
  --camera-height-cm 實測高度 --camera-pitch-deg 實測角度 --preview
```

### C. 原地右轉一圈

```powershell
python record_robot_run.py --name turn_right_360 --duration 20 `
  --camera-height-cm 實測高度 --camera-pitch-deg 實測角度 --preview
```

### D. 房間閉環路線

在地面貼出起點和至少四個轉彎點，量出各段距離。從起點出發，繞過房間後回到相同位置及朝向：

```powershell
python record_robot_run.py --name room_loop_01 --duration 60 `
  --camera-height-cm 實測高度 --camera-pitch-deg 實測角度 `
  --route-length-m 各段距離總和 --preview
```

### E. 障礙繞行路線

使用紙箱等固定障礙，不使用真人當障礙。保留一條足夠寬的替代路線，記錄障礙物寬度、長度及其相對起點的位置：

```powershell
python record_robot_run.py --name obstacle_route_01 --duration 60 `
  --camera-height-cm 實測高度 --camera-pitch-deg 實測角度 --preview
```

每次會在 `navigation/real_runs/日期_場次名稱/` 產生：

- `camera.mp4`：無網頁邊框的攝影機影像。
- `frames.jsonl`：每張影格的電腦時間。
- `telemetry.jsonl`：同一時鐘上的 pulse、rpm、前方距離及控制板時間。
- `telemetry_errors.jsonl`：錄製期間的通訊失敗。
- `manifest.json`：鏡頭高度、角度、路線長度、解析度、FPS 與服務狀態。

## 要交回確認的資料

1. 六項硬體尺寸。
2. `navigation/output/ranger_bench.json`。
3. 直線三次與左右轉各三次的 pulse 差值和人工誤差。
4. `navigation/calibration.json`。
5. `straight_1m`、`turn_left_360`、`turn_right_360`、`room_loop_01` 四個錄製資料夾。
6. 障礙測試時紙箱尺寸與位置；這項可以等基本定位通過後再做。
