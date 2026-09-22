# 5G、MEC 與中華電信平台實作規格

## 目前完成度（2026-09-12）

| 項目 | 狀態 | 證據或限制 |
| --- | --- | --- |
| 本機 MEC API、認證、裝置隔離 | 已完成 | `/health`、`/v1/infer`、`/metrics` 與 Bearer token 測試通過 |
| 影像傳送與推論結果回傳 | 已完成（本機模擬） | 106 張線上測試成功；`mec_output/edge_online_report.json` |
| 本地安全推論不受 MEC 超時阻塞 | 已完成（本機模擬） | MEC 全離線時 30/30 張仍執行本地推論 |
| 中途斷線、自動恢復 | 已完成（本機模擬） | 60 張中模擬中斷 20 張，最後自動回到 online |
| SQLite 事件佇列、重試及去重 | 已完成 | 固定 `event_id`、退避補送與平台端唯一鍵測試通過 |
| 照護頁顯示 MEC／本地備援 | 已完成程式 | Edge Agent 運作時讀取 `mec_output/edge_status.json` |
| 容器與 Kubernetes 部署檔 | 已備妥，未在本機建置 | 本機沒有 Docker；需在目標主機建置與部署 |
| 中華電信 5G 公網量測 | 尚未實測 | 需要 5G SIM／熱點或路由器及可公開存取的主機 |
| 中華電信專網／MEC | 尚未介接 | 需要主辦提供 MEC 位址、部署權限、APN／SIM 與防火牆資料 |
| 中華電信 IoT／健康平台 | 尚未介接 | 需要正式 API 文件、測試帳號與憑證 |

平台程式部分已補上 `ChtIotEventSink` 與官方 TAS callout 通知器；詳細完成度、申請方法與測試證據見 `中華電信平台介接.md`。在取得 API key 前可稱為「正式 API 相容程式與本機契約驗證完成」，不能稱為已將資料送進中華電信正式租戶或已撥出真實電話。

## 1. 作品採用的實際架構

```text
USB 攝影機
  ↓
樹莓派 Edge Agent
  ├─ 本地最低限度跌倒／障礙判斷（斷線仍運作）
  ├─ H.264 影像或事件截圖上傳
  ├─ SQLite 待傳事件佇列
  └─ 5G 路由器／5G USB modem＋中華電信 SIM
          ↓ 5G 公網、企業 APN 或 5G 專網
MEC 推論服務
  ├─ Pose、跌倒、異常活動及環境辨識
  ├─ /health、/v1/infer、/metrics
  └─ HTTPS JSON 回傳即時結果（目前版本）
          ↓
平台介接器
  ├─ 中華電信 IoT 平台 DMP／AEP（取得帳號後）
  ├─ 家屬／護理站後台
  └─ LINE 或平台正式通知管道
```

居家產品不適合每戶部署一台專屬 MEC。競賽與商業提案應分成：

- 一般家庭：機器狗本地備援＋5G 公網／共享 Edge Cloud。
- 長照機構或醫院：場域 5G 專網＋專屬 MEC，多台機器狗共用算力。

## 2. 現在就能完成的軟體

### Edge Agent

在樹莓派新增一個唯一的 Edge Agent，負責：

1. 讀取攝影機，不讓多個 Python 程式搶同一鏡頭。
2. 影像加上 `device_id`、遞增 `frame_seq` 與單調時間戳。
3. 正常連線時把影像送至推論服務。
4. 斷線時停止上傳影像，但保留本地安全判斷。
5. 將尚未送達的事件寫入 SQLite；恢復連線後依序補送。
6. 每個事件使用固定 `event_id`，平台重試時可去除重複資料。

第一版可用每秒 5 張 JPEG 的 HTTPS API 完成。展示版再改為 H.264＋SRT、WebRTC 或 RTSP，降低持續影像的頻寬。照護產品預設只上傳事件資料與事件截圖；持續影像需經使用者授權。

### MEC 推論服務

將目前模型拆成可部署的容器服務：

```text
GET  /health       容器、模型與 GPU/CPU 狀態
POST /v1/infer     一張影像＋device_id＋frame_seq
GET  /metrics      延遲、錯誤、重複影格與上傳量統計
POST /v1/events    接收事件並依 event_id 去重
GET  /v1/events    測試後台查詢事件
```

目前結果由 `/v1/infer` 同一個 HTTPS 回應傳回；WebSocket 是大量裝置或需要伺服器主動推送時的後續擴充，尚未實作。

`/v1/infer` 回傳範例：

```json
{
  "device_id": "ranger-001",
  "frame_seq": 8214,
  "model_version": "care-pose-1.0.0",
  "result": {
    "posture": "suspected_fall",
    "confidence": 0.91,
    "environment": ["path_obstacle"]
  },
  "server_processing_ms": 83,
  "event_id": "ranger-001-8214-fall"
}
```

容器映像不得放 API key、LINE token 或個資；密鑰由部署平台的 Secret／環境變數注入。健康影像需設定保存時間、存取角色與刪除機制。

## 3. 5G 的兩階段驗證

### 階段 A：可自行完成的 5G 公網展示

1. 準備中華電信 5G SIM 與支援 5G 的手機熱點、路由器或 USB modem。
2. 樹莓派透過該 5G 網路連上可公開存取的測試推論主機。
3. 用相同影像分別測試 Wi-Fi 與 5G，記錄而非宣稱預設低延遲。
4. 此階段只能稱為「中華電信 5G 公網實測」，不能稱為企業專網或 MEC。

### 階段 B：取得中華電信資源後的專網／MEC 展示

向中華電信確認採用共享 Edge Cloud 或企業專屬 MEC，取得：

- 測試場域、日期、基地台涵蓋及測試 SIM。
- APN、SIM 白名單、裝置能否直接存取 MEC 私有 IP。
- MEC 是 VM、Docker、Kubernetes 還是 ACM；CPU、RAM、GPU、磁碟配額。
- 容器映像上傳方式、Registry、Ingress、TLS 憑證及可開放的連接埠。
- 對外網路、DNS、防火牆與回呼限制。
- 技術窗口、使用期限、費用及資料清除方式。

拿到上述資訊後，將同一個已在電腦測過的容器映像部署到 MEC，只修改端點與密鑰，不修改推論邏輯。

## 4. 延遲與斷線驗收

每張影像記錄：

```text
T0 相機取像
T1 編碼完成
T2 MEC 收到
T3 推論完成
T4 樹莓派收到結果
T5 前端顯示／事件成立
```

同時報告：

- 端到端延遲 `T4-T0` 的 p50、p95、p99。
- 編碼、網路往返、MEC 排隊、模型推論各自耗時。
- 每秒處理影格、丟幀率、上行頻寬與事件送達率。
- Wi-Fi、5G 公網、5G＋MEC 三組結果；只報實測數字。

跨裝置單向時間需要 NTP／PTP 同步。若無法證明時鐘已同步，採用樹莓派單調時鐘量測往返時間，加上 MEC 回報的伺服器處理時間。

斷線測試至少包含 10 秒、60 秒及 5 分鐘：

1. 拔除網路或封鎖 MEC 端點。
2. 確認本地安全偵測仍運作，前端明確顯示「MEC 離線／本地備援」。
3. 確認事件寫入 SQLite，影像不得無限制堆積。
4. 恢復網路，確認事件依原 `event_id` 補送且平台沒有重複事件。
5. MEC 超時不得阻塞停止指令或本地警報。

## 5. 中華電信 IoT 平台介接

公開網站說明平台提供設備 SDK、標準／開放介面、DMP、CMP、PKI 雙向認證與 PaaS，但實際 API 端點、訊息格式與權限需由平台帳號或技術窗口提供。取得前先實作通用介面：

```python
class EventSink:
    def upload_event(self, event): ...
    def upload_heartbeat(self, heartbeat): ...
    def close(self): ...
```

開發時提供三個實作：

- `LocalEventSink`：寫入本機 JSONL／SQLite，離線測試。
- `WebhookEventSink`：送到自建測試後台。
- `ChtIotEventSink`：取得正式文件與帳號後才填入認證、URL／MQTT topic。

事件最少包含：

```json
{
  "schema_version": "1.0",
  "event_id": "ranger-001-8214-fall",
  "device_id": "ranger-001",
  "occurred_at": "2026-09-11T01:30:00+08:00",
  "type": "suspected_fall",
  "severity": "high",
  "confidence": 0.91,
  "mode": "mec",
  "snapshot_ref": null,
  "acknowledged": false
}
```

正式平台驗收順序：註冊測試裝置、上傳 heartbeat、上傳單一事件、後台看見事件、觸發通知、完成確認／取消、測試密鑰失效、限流、重試與去重。

## 6. 不能先寫成已完成的項目

- 未取得專網 SIM、APN 及 MEC 位址前，不能稱為「已接入 5G 專網」。
- 把服務放在一般雲端 VM，不能稱為 MEC。
- 尚未取得平台帳號與 API 文件時，只能寫「規劃介接中華電信 IoT 智慧聯網大平台」。
- 中華健康雲公開頁面介紹健康資料服務，不等於提供參賽團隊可直接使用的公開 API。未取得書面介接方式前，不寫成已串接。
- LINE 通知是現有獨立功能，不能寫成中華電信平台通知。

## 7. 寄給主辦／業務窗口的問題

```text
我們是 2026 智慧創新應用大賽的長照巡檢機器人團隊，現有樹莓派攝影機端、
姿態／跌倒與環境安全推論服務，預計驗證 5G 上行影像、MEC 推論及斷線本地備援。

請問競賽團隊是否可申請：
1. 測試用中華電信 5G SIM、企業 APN 或專網場域？
2. MEC／Edge Cloud 測試資源，以及 VM、Docker、Kubernetes 或 ACM 的部署規格？
3. IoT 智慧聯網大平台測試租戶、設備帳號、API／MQTT 文件與 PKI 憑證？
4. 可否介接健康雲或既有照護服務；若可以，資料欄位、同意流程與測試環境為何？
5. 資源申請期限、費用、技術窗口與場域驗證時段？
```

在收到回覆前，可完成 Edge Agent、MEC 容器、測試後台、離線佇列及量測工具；正式端點與認證不能自行猜測。

目前沒有 5G SIM、公開主機或 MEC 帳號時，使用 `中華電信_5G_MEC資源申請包.md` 的電話稿與文字稿向賽事執行單位申請 PoC 資源。此申請包同時列出對方回覆後需取得的部署欄位，避免只有宣傳性說明而缺少實際端點與權限。
