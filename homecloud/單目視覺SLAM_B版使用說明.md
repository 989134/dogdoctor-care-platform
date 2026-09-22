# 單目視覺定位 B 版

這個版本用機器狗高度的移動影片驗證單目視覺定位是否可行。它會輸出相對軌跡、追蹤中斷、特徵品質與起終點候選迴環。它不是實車公尺尺度 SLAM，也不會控制馬達。

## 錄影要求

1. 將鏡頭放在未來裝車的高度與角度；沒有車時可以手持，但不要上下晃動。
2. 從有紋理、容易辨識的位置開始，例如門框、櫃子或海報旁。
3. 緩慢前進，每秒約 10～20 公分；轉彎時原地慢轉。
4. 拍攝牆角、門框、家具等靜態環境，避免讓人體長時間遮住大部分畫面。
5. 繞一圈後回到原本位置，鏡頭朝向也要與開始時相近。
6. 建議 30～90 秒、光線穩定、不要數位變焦或暫停錄影。

跌倒測試影片通常是固定鏡頭和移動人體，只適合照護辨識；它無法證明載具定位。

## 前端畫面錄影

如果只有包含網頁的螢幕錄影，先用 `crop_slam_video.py` 裁出攝影機區域。分析時加入 `--frontend-recording --no-calibration`，程式會排除左上固定狀態字與下方車體，並跳過近似重複的串流畫面：

```powershell
python validate_visual_slam.py "test_videos\slam_frontend\loop2_crop.mp4" --frontend-recording --no-calibration
```

前端錄影只適合初步追蹤與候選迴環測試，不能取代原始串流。若確認前端顯示的影像尚未去畸變，可以移除 `--no-calibration`，再比較幾何內點。

## 第一次測試

在 `homecloud` 執行：

```powershell
python validate_visual_slam.py "test_videos\你的繞行影片.mp4"
```

完成後開啟：

```text
navigation/output/visual_slam/report.html
```

輸出包括：

- `report.html`：可閱讀的驗證頁面。
- `report.json`：判定與限制。
- `trace.csv`：每個分析影格的姿態、特徵數、匹配數及失敗原因。
- `trajectory.png`：相對尺度軌跡。
- `annotated.mp4`：追蹤狀態疊圖影片。

## 已知路線長度

若錄影路線沿地面膠帶行走，而且已量到總長，例如 6.4 公尺：

```powershell
python validate_visual_slam.py "test_videos\robot_route_01.mp4" --route-length-m 6.4
```

這只會按總長對整條軌跡做近似縮放，不會取代輪編碼器尺度，也不能當作實車定位精度證明。

## Depth Anything V2 比較

先跑純視覺，保留預設輸出。再指定另一個輸出資料夾執行 Depth 版本：

```powershell
python validate_visual_slam.py "test_videos\robot_route_01.mp4" --depth --output "navigation\output\visual_slam_depth"
```

Depth 模式使用原始相對深度的影格內排序，排除最靠近鏡頭的一部分特徵。它不會把 `0–255` 當公尺。CPU 推論會明顯較慢。

比較兩份 `report.json` 的：

- `tracking_accept_rate`
- `tracking_loss_events`
- `median_inliers_on_tracked`
- `loop_check.candidate`
- `loop_check.inliers`

只有在同一段影片中，Depth 版本穩定降低追蹤中斷或提高幾何內點，才能說 Depth 對該場景有幫助。

## 判定標準

- `有效相機移動不足`：通常是固定鏡頭影片，不能測載具定位。
- `追蹤率偏低`：常見原因是移動太快、模糊、光線太暗、牆面沒有紋理或人體遮擋。
- `候選迴環`：結尾與起點有足夠相同的靜態特徵；目前只是地點辨識，尚未做全域姿態圖最佳化。
- `相對單位`：單目相機尚無公尺尺度，必須等輪編碼器、IMU 或可靠的已知距離加入。
