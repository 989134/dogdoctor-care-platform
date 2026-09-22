# 銀髮守護犬（Doctor Dog）

以 mBot Ranger、樹莓派與電腦端 AI 組成的居家照護機器人系統。專案包含人體姿態與跌倒／久坐監測、環境風險辨識、自主巡檢與定位、Ranger 車體控制、MEC 通訊測試、語音互動，以及照護者手機端介面。

## 專案結構

- `homecloud/`：車端、電腦端 AI、照護儀表板、定位與巡檢、MEC、Ranger 韌體及測試工具。
- `guardian_mobile/`：照護者手機 PWA、事件與用藥管理、通知及 WebRTC 車端通話。

## 安全設定

本儲存庫不包含 API 金鑰、LINE 憑證、使用者資料、資料庫、影片、模型權重或測試紀錄。請從各模組的 `.env.example` 建立本機 `.env`，再填入自己的設定；不要提交 `.env`。

曾經寫入本機舊版程式的外部服務金鑰應在服務端撤銷並重新產生，再放入 `.env`。

## 大型依賴

YOLO、Depth Anything V2 等模型權重，以及 TFDA 藥品 XML 原始資料未納入 Git。請依各子專案文件下載或在本機產生。Makeblock Arduino 函式庫也以外部依賴方式安裝，不複製其 Git 儲存庫。

## 主要入口

- 照護與姿態監測：`homecloud/run_homecare.py`
- Ranger 控制閘道：`homecloud/ranger_gateway.py`
- 定位服務：`homecloud/run_localization.py`
- 實體巡檢：`homecloud/run_real_patrol.py`
- MEC 服務：`homecloud/mec_service.py`
- 手機端服務：`guardian_mobile/run_guardian.py`

各功能的啟動、驗證與硬體設定請參考兩個目錄內的 Markdown 文件。
