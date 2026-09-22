# GMDCSA24 初步測試片段

來源：https://github.com/ekramalam/GMDCSA24-A-Dataset-for-Human-Fall-Detection-in-Videos
作者：Ekram Alam 等。來源檔案 URL 見 sources.json。

選取 Subject 1 的 Fall/01、Fall/02、ADL/01、ADL/02，保留原始影片。Fall 為來源資料夾的跌倒標籤，ADL 為日常動作。已確認四支均可由 OpenCV 開啟，30 FPS、1280×720。以 5 FPS 診斷回放時，兩支 Fall 都建立 `tracking_review`，兩支 ADL 都沒有事件；報告保存在 `mec_output/subject1_*_pose_diagnostic.json`。仍未確認每支影片的精確鏡頭高度，不應稱為機器狗視角，也不能用四支影片宣稱整體準確率。

片段約 6.3–8.2 秒，短於 20 秒回應倒數。因此只驗證是否建立待確認事件，不把片尾重複成 20 秒，也不以是否升級高風險作為判準。完整倒數流程使用較長的裝車視角影片及時間控制測試。

Omobot 補充文件是平面 Homography 推導，使用相機內外參、平面假設及人物距離；其範例矩陣不能直接視為目前 USB 鏡頭的校正結果。此次未修改正式演算法與門檻。
