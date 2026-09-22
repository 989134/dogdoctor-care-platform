# 中華電信 5G／MEC PoC 資源申請包

更新日期：2026-09-12

## 先採取的管道

優先聯絡「2026 中華電信智慧創新應用大賽」執行單位，詢問入圍前後是否有參賽團隊可申請的 5G、雲端、Edge Cloud 或 MEC 測試資源。

- 大賽官網：https://cht5g.com.tw/
- 智慧創新應用大賽客服電話：02-2325-7188 分機 987
- 聯絡時間：週一至週五 10:00–17:00

若賽事窗口沒有提供資源，再詢問中華電信企業專網產品窗口。企業專網官網列出的服務電話是 02-3316-6333；此管道採專案諮詢與報價，較適合由學校、指導老師、法人或合作長照機構名義接洽。

- 企業專網：https://www.cht.com.tw/home/enterprise/products-services/5g/5g-service/712
- 專人聯絡表單：https://eshop.cht.com.tw/ACE/AideEnterprise/Fill?identifier=4a57999a-dffc-4956-a15e-801f9d3d8184&productid=175&source=CMS&tn=5ad5d2be-1a4c-475b-b62c-6237ce90fdc3

## 電話詢問稿

```text
您好，我們準備參加 2026 中華電信智慧創新應用大賽智慧醫療主題，
作品是「銀髮守護犬」長照巡檢機器人。

目前已完成樹莓派攝影機、人體姿態、跌倒與異常行為判斷、
本地斷線備援，以及可用 Docker／Kubernetes 部署的 MEC 推論服務。
本機已完成影像上傳、結果回傳、延遲量測、事件補送與去重測試。

我們希望在決賽前完成真實的中華電信 5G 或 MEC PoC，請問參賽團隊是否能申請：
1. 測試用 5G SIM、熱點、CPE 或指定實證場域；
2. Edge Cloud／MEC 的 VM、Docker 或 Kubernetes 測試空間；
3. IoT 平台測試帳號與 API／MQTT 文件；
4. 可協助確認網路與容器部署的技術窗口？

如果目前沒有完整 MEC，是否能先安排 5G 公網＋Edge Cloud 的影像上傳測試？
我們可配合指定日期與場域，並提供完整測試規格。
```

## 文字申請稿

主旨：申請 2026 智慧創新應用大賽長照機器人 5G／MEC PoC 測試資源

```text
您好：

我們的參賽作品「銀髮守護犬」屬於智慧醫療／長照 3.0 應用，
以移動式機器人進行居家巡檢、人體姿態與跌倒辨識，並在異常時通知家屬。

現階段已完成：
- 樹莓派攝影機串流與電腦端影像推論；
- 上半身／全身人體姿態、跌倒、失去追蹤、久未移動與徘徊判斷；
- MEC HTTPS 推論 API、Bearer token、裝置隔離與事件去重；
- Edge Agent 本地安全推論、斷線切換、SQLite 事件補送；
- Docker Compose、Kubernetes 與 Linux systemd 部署檔；
- 本機 106 張影像往返與中途斷線自動恢復測試。

希望申請以下最小 PoC 資源：
1. 一組測試 5G 連線（SIM／CPE／指定場域皆可）；
2. 一個可部署容器的 Edge Cloud 或 MEC 端點；
3. HTTPS 入口、TLS、可用連接埠與防火牆規則；
4. 若可使用 IoT 平台，提供測試租戶、裝置帳號及 API／MQTT 文件；
5. 一位技術窗口協助進行一次部署與連線驗收。

我們預計量測 Wi-Fi、5G 公網與 5G＋MEC 的 RTT、端到端 p50／p95／p99、
影像上行頻寬、事件送達率，並測試 10 秒、60 秒及 5 分鐘斷線時的本地備援與恢復補送。

團隊可以提供容器映像、API 規格、測試影片、故障注入工具與驗收報告格式，
並配合指定場域及時段進行測試。敬請告知是否有適合參賽團隊使用的資源與申請流程。
```

## 對方回覆時必須取得的欄位

| 類別 | 需要的資料 |
| --- | --- |
| 連線 | 公網、企業 APN 或專網；SIM／CPE 型號；測試場域與日期 |
| MEC | VM、Docker 或 Kubernetes；CPU、RAM、GPU、磁碟配額 |
| 映像 | Registry 位址、登入方式、映像命名及允許的基底映像 |
| 網路 | MEC URL／私有 IP、Ingress、port、DNS、TLS、防火牆白名單 |
| 認證 | API key、OAuth、憑證或 PKI；密鑰交付方式 |
| 平台 | IoT API／MQTT broker、topic、payload schema、限流與重試規則 |
| 合規 | 影像保存期限、可用資料、存取角色與測試結束後清除方式 |
| 支援 | 技術窗口、可測時段、資源期限、費用及問題回報管道 |

## 可接受的分階段結果

1. **只有 5G SIM／場域：**部署到一般公開 VM，成果名稱寫「中華電信 5G 公網實測」。
2. **只有 Edge Cloud／MEC：**先用 Wi-Fi 連端點，證明部署與推論；不能寫成已完成 5G 專網。
3. **同時有 5G 與 MEC：**完成 Wi-Fi、5G 公網、5G＋MEC三組比較及斷線驗收。
4. **仍無資源：**競賽文件只描述已完成的 MEC-ready 軟體、本機故障注入證據及預計介接規格，不寫「已接入中華電信 MEC」。

## 已準備的技術附件

- `5G_MEC_實作規格.md`
- `MEC_部署與實網驗收.md`
- `MEC_逐步測試.md`
- `Dockerfile.mec`
- `docker-compose.mec.yml`
- `deploy/mec/kubernetes.yaml`
- `mec_output/edge_online_report.json`
- `mec_output/edge_recovery_report.json`
- `mec_output/local_network_comparison.md`
