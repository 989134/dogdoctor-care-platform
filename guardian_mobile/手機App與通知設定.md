# 手機 App 與通知設定

目前為可安裝至主畫面的 PWA，保留完整總覽、事件流程、相機／照片藥品辨識和設定。不是 Android APK 或 App Store 上架包。

## 管理員操作

1. 在「設定 → 帳號與影像管理」建立第二位照護者帳號。
2. 在「App 推播＋簡訊聯絡人」選兩個不同帳號，填各自手機號碼；台灣 09 開頭會轉為 +886。第二聯絡人等待時間預設 10 分鐘。
3. 填寫現場地址、回撥電話、119 專線，儲存通知設定。
4. 每位聯絡人用自己的手機登入，安裝 App 後按「啟用此裝置 App 推播」。選單會顯示帳號的推播裝置數量。
5. 事件頁分别顯示兩位聯絡人的 App 推播與簡訊狀態。裝置／電信確認送達才啟動倒數；點開通知不等於接手。

推播＋簡訊同時排入各自的持久佇列，由兩個背景工作程序独立發送，不會等推播失敗才發簡訊。相同指令／管道只建立一次。已被接手或升級的事件停止尚未送出的舊通知。

## 實際手機上線仍需設定

127.0.0.1 只供這台電腦使用，不能把此網址交給手機。需將完整 Flask 服務部署在可持續運作的主機，透過 HTTPS 反向代理提供同一個網域，保留 /api、/static、/sw.js 路徑，不可只上傳 HTML。

在啟動服務的環境中設定下列值，重新啟動 `run_guardian.py`。金鑰不得放入網頁、Git 或聊天：

| 環境變數 | 用途 |
| --- | --- |
| GUARDIAN_PUBLIC_URL | 手機可連線的 HTTPS 根網址，例如 https://care.example.com（不是實際已部署網址） |
| GUARDIAN_TWILIO_SID | Twilio Account SID |
| GUARDIAN_TWILIO_TOKEN | Twilio Auth Token |
| GUARDIAN_SMS_FROM | 該帳號授權的簡訊發送號碼，E.164 格式 |

此版已實作 Twilio REST 簡訊介面。帳號須具備向台灣手機發送簡訊的權限、可用發送號碼及額度，依服務商要求完成相關登記；測試帳號可能只允許已驗證收件人。其他簡訊商需更換 `guardian/sms.py` 傳送器。未設定服務時顯示「尚未設定」，不會假裝送達。這次修改沒有建立付費帳號或發送付費簡訊。

設定 HTTPS 網址後 Session Cookie 使用 Secure。請由 HTTPS 入口登入，不要繼續用 HTTP 本機入口登入。反向代理後仍以單一 `run_guardian.py` 服務程序啟動，以便每秒事件升級與通知工作程序持續運作；不能只啟動 Flask app 而漏掉背景程序。正式代管可再改用 WSGI 主機與獨立工作程序。

推播 VAPID 金鑰自動保存在 `guardian_data/push-private.pem`，需與資料庫一起備份，不可公開。登出解除此裝置綁定；變更密碼或撤銷帳號解除該帳號所有綁定。

## 手機安裝

- Android Chrome：開啟 HTTPS 網址 → 網站「設定」→「安裝到手機主畫面」，或瀏覽器選單的安裝選項。
- iPhone／iPad：Safari 開啟 HTTPS 網址 → 分享 → 加入主畫面 → 從主畫面開啟 → 登入並啟用推播。推播需要 iOS／iPadOS 16.4 以上。
- 背景推播由 Service Worker 接收；App 頁面可關閉。手機離線、通知權限關閉、系統限制或主機停機仍會影響送達。
- 離線頁只顯示離線提醒，不快取照護事件、照片、藥品資料或已登入頁面。

## 傳送狀態與故障處理

`pending` 待送、`sending` 傳送中、`accepted` 服務已接受但未確認送達、`delivered` 已確認送達、`blocked` 尚缺設定／推播裝置、`failed` 服務明確回報失敗、`unknown` 無法確認是否送出、`cancelled` 因事件進展停止尚未送出的通知。

推播裝置回報「已顯示」，不是「使用者已讀」。簡訊透過已取得的訊息編號約每 15 秒查詢，queued／sent 不會當成 delivered。第一次送達開始計時，另一管道晚到不重設倒數。第一及第二聯絡人皆未接手，各等待 10 分鐘後升級。

簡訊 POST 網路逾時可能已產生費用，系統將標示 unknown，不盲目重送；請管理員檢查服務商紀錄。已有訊息編號則持續查詢。推播暫時失敗約 60 秒重試，已失效訂閱自動移除。

119 仍使用原有待通報指令與人工撥號入口。本次手機通知改動沒有接上自動撥打救護專線的電信系統。待通報不代表已叫到救護車。

## 驗證

`.\.venv\Scripts\python.exe -m unittest discover -p 'test_*.py'`

測試隔離資料庫、模擬推播與簡訊，不發送真實簡訊、不撥打 119。HTTPS 部署與服務憑證就緒後，仍需以本人同意的兩支測試手機確認安裝、背景推播、簡訊收件及手動接手。

參考：[Apple Web Push 說明](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)、[Twilio Message API](https://www.twilio.com/docs/messaging/api/message-resource)。
