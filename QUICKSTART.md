# Quickstart（5 分鐘上手）

前置：安裝 Docker 與 Docker Compose。所有東西都在容器內跑，本機不需裝 Python。

工作目錄都在專案根目錄執行。

---

## 1. 啟動全棧

```bash
docker compose up -d --build
docker compose ps          # 應看到 broker + 4 個服務都 running
```

第一次會 build 映像（約 1–2 分鐘）。

## 2. 看排程在運作

```bash
docker compose logs -f scheduler_engine
```

會看到排程持續發出指令與週期指標，例如：

```
T000001 A1: load ProductIn->Tray_00 product=P000001
T000007 A1: unload Tray_00->ProductOut product=P000001
metrics: arrivals=19 completed=10 scrapped=0 intake=5
```

- `load` = 從生產線盤夾起、放到機台；`unload` = 從機台夾起、放回盤。
- 手臂分區：依 `reachability_matrix`（目前 1 手臂 A1 服務 Tray_00–Tray_05）。
- `completed` 持續上升、`scrapped` 為隨機故障報廢、`intake` 為等待中的 FIFO 佇列。

`Ctrl-C` 離開 log（服務仍在背景跑）。

## 3. 監看 MQTT 訊息流（最直觀）

```bash
# 發給 Isaac Sim 的手臂指令（含解析後座標）
docker compose exec broker mosquitto_sub -t 'isaacsim/arm/+/command' -v

# 機台即時狀態
docker compose exec broker mosquitto_sub -t 'telemetry/machine/+/state' -v
```

## 4. 看狀態紀錄表（事件落地在 sqlite）

```bash
docker compose exec data_collector python -c "
import sqlite3; c=sqlite3.connect('/app/data/events.db')
print('by entity_type:', dict(c.execute('select entity_type,count(*) from event_log group by entity_type').fetchall()))
print('by to_state:', dict(c.execute('select to_state,count(*) from event_log group by to_state').fetchall()))
"
```

事件檔同時存在本機 `./data/events.db`。

## 5. 調參數重跑

編輯 [config/simulation.json](config/simulation.json)（機台數、手臂數、產品數、進線間隔、作業時間、故障率…）。`config/` 已掛載為 volume，改完只要 restart、**不用 rebuild**：

```bash
docker compose restart
```

## 6. 容量試算（不必起全棧）

依設定算「需要幾台機台、幾隻手臂」，並用模擬驗證：

```bash
docker compose run --rm scheduler_engine python capacity.py --config /app/config/simulation.json --seeds 5
```

輸出包含解析公式估值 + 模擬驗證的 mean wait / 利用率 / 建議配置。

## 7. 關閉

```bash
docker compose down
```

事件檔 `./data/events.db` 會保留。

---

## 進階：模擬「真機台」走 socket 介面

全棧運作中，往 `data_collector` 的 TCP gateway（port 9000）送一筆機台狀態：

```bash
printf '{"machine_id":"R01","state":"done","product_id":"RP1"}\n' | nc localhost 9000
docker compose logs data_collector | grep R01     # 應看到 [socket] 標記與落地
```

真機台接入細節見 [docs/integration_real_machines.md](docs/integration_real_machines.md)。

## 進階：Isaac Sim 端連線自測

確認 Isaac 端收得到手臂指令（全棧運作中、在另一終端）：

```bash
cd isaac_sim
pip install paho-mqtt          # 或在 Isaac Sim python：./python.sh -m pip install paho-mqtt
python mqtt_arm_bridge.py --selftest --host localhost --port 1883
```

會印出收到的手臂指令與座標。接進真正的 Isaac Sim 場景見 [isaac_sim/README.md](isaac_sim/README.md)。

---

## 疑難排解

| 症狀 | 處理 |
|---|---|
| 服務一直重啟 | `docker compose logs <service>` 看錯誤；多半是 `config/*.json` 格式問題 |
| 沒看到手臂指令 | 確認 `scheduler_engine` 有在發（步驟 2）、`machine_simulator` 為 `driven` 模式（compose 預設） |
| 改了 config 沒生效 | `config/` 已掛載 volume，改完 `docker compose restart` 即可（首次加掛載需先 `up -d` 重建一次） |
| 跑測試報 Python 版本錯 | 用容器跑測試（見 README「測試」段） |
| port 1883/9000 被占用 | 改 `docker-compose.yml` 的 `ports` 對應，或停掉占用程式 |
