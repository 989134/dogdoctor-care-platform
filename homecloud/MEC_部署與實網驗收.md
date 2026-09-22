# MEC 部署與實網驗收

這份文件接續已完成的本機模擬。只有使用實際 5G 網路與中華電信提供的 MEC 資源後，才能將對應項目標示為完成。

## 1. 在可公開連線的 Linux 主機部署

主機需有 Docker 與 Docker Compose。將 `homecloud` 複製到主機後：

```bash
cd homecloud
cp .env.mec.example .env.mec
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

把輸出的隨機值填入 `.env.mec` 的 `MEC_API_TOKEN`，再執行：

```bash
docker compose --env-file .env.mec -f docker-compose.mec.yml up -d --build
docker compose --env-file .env.mec -f docker-compose.mec.yml ps
curl http://127.0.0.1:7000/health
```

主機防火牆只開放 HTTPS 對外入口。正式環境由反向代理或平台 Ingress 終止 TLS，再轉送到容器 port 7000；不要把未加密的 port 7000 直接暴露到網際網路。

如果中華電信提供 Kubernetes，先把 `deploy/mec/kubernetes.yaml` 中的 `REPLACE_WITH_REGISTRY` 換成實際 Registry，建立 `homecare-mec-secrets`，再套用清單。映像版本與測試版本都使用 `1.1.0`。

若對方提供一般 Linux VM、不使用 Docker，可以安裝 `deploy/mec/homecare-mec.service`。建立系統使用者 `homecare`、將程式放到 `/opt/homecare-mec`，把權杖寫到 `/etc/homecare-mec.env` 並設定檔案權限為 `600`。systemd 會建立僅供服務寫入的 `/var/lib/homecare-mec` 事件資料目錄。

## 2. 樹莓派／Edge 端連線

先在樹莓派設定環境變數，避免把密鑰留在命令列歷程：

```bash
export MEC_API_TOKEN='<測試權杖>'
export EVENT_SINK_URL='https://<正式端點>/v1/events'
export EVENT_SINK_TOKEN="$MEC_API_TOKEN"
python3 mec_edge_agent.py \
  --source 'http://127.0.0.1:5001/video_feed' \
  --mec-url 'https://<正式端點>' --device-id ranger-001 \
  --fps 5 --set-baseline
```

瀏覽器照護頁會顯示 MEC 線上、本地備援或 Edge Agent 已停止，以及 SQLite 待補送事件筆數。

樹莓派需長期運作時，可安裝 `deploy/edge/homecare-edge.service`，把範例中的 MEC 端點換成正式 HTTPS 位址，並將 `deploy/edge/homecare-edge.env.example` 複製成 `/etc/homecare-edge.env`、填入權杖後設為 `600` 權限。

## 3. 5G 公網 A/B 測試

保持主機、影像檔、解析度、JPEG 品質、FPS 與取樣張數相同，各做一次：

1. 樹莓派連 Wi-Fi，執行 300 張測試並保存 `wifi_report.json`。
2. 樹莓派改連中華電信 5G SIM 熱點／路由器，執行相同測試並保存 `cht_5g_report.json`。
3. 比較 RTT、端到端延遲 p50/p95/p99、成功率、上行 Mbps 與事件送達率。
4. 記錄測試時間、地點、訊號型態、裝置型號與伺服器區域。

完成兩次測試後產生比較表：

```bash
python3 scripts/mec_compare_reports.py \
  --report 'Wi-Fi=mec_output/wifi_report.json' \
  --report '中華電信5G=mec_output/cht_5g_report.json' \
  --output-prefix mec_output/network_comparison
```

會同時產生 JSON、CSV 與 Markdown，可直接引用到測試紀錄或簡報。

這一階段可稱為「中華電信 5G 公網實測」，不能稱為 5G 專網或 MEC。

## 4. 斷線驗收

依序中斷 10 秒、60 秒與 5 分鐘：

1. 確認照護頁顯示「MEC 離線／本地備援」。
2. 確認本地跌倒與環境警報仍持續。
3. 觸發一筆測試事件，確認 `outbox.pending` 增加。
4. 恢復網路，確認狀態回到「MEC 線上」。
5. 確認待補送歸零，平台只有一筆相同 `event_id`。

可先用故障注入完成可重現測試。例如測試第 10 秒起中斷 60 秒，總共執行 90 秒：

```bash
python3 mec_edge_agent.py --source test_videos/care_fall_01.mp4 --loop-source \
  --duration-seconds 90 --fps 5 --mec-url 'https://<正式端點>' \
  --outage-start-seconds 10 --outage-seconds 60 \
  --report mec_output/outage_60s_report.json
```

將 `--outage-seconds` 依序改成 10、60、300，並讓總執行時間至少比中斷時間多 20 秒，以確認恢復後確實重新連線。最後還要進行一次真的拔除網路或停用網路介面的測試。

## 5. 專網／MEC 最後需要的資料

向主辦或中華電信窗口取得：MEC URL 或 VM/Kubernetes 登入方式、容器 Registry、測試 SIM、APN、可連線的私有網段、防火牆規則、TLS 憑證、CPU/RAM/GPU 配額、IoT 平台 API/MQTT 文件、裝置帳號及憑證。

拿到資料後只需替換映像位置、端點與密鑰，再重跑 smoke test、300 張延遲測試及三種斷線測試，不需重寫姿態或跌倒演算法。
