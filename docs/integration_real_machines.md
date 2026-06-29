# 真機台接入規範 (Real-Machine Integration)

本系統 demo 階段用 `machine_simulator` 產生假資料；接真機台時**不需改任何 scheduler / publisher 程式**——真機台只要把狀態送進 `data_collector`，下游一視同仁（SPEC §2.2）。

有兩種接入方式，擇一即可。

## 方式 A：MQTT（建議）

真機台直接連到 Mosquitto broker，發佈狀態到：

| Topic | 用途 | retained |
|---|---|---|
| `plant/machine/{machine_id}/state` | 狀態轉移（empty/start/working/done/error） | 是 |
| `plant/machine/{machine_id}/telemetry` | 加工中即時取樣（elapsed/remaining） | 否 |

payload 為 JSON（見下方 schema）。這與 `machine_simulator` 發的完全相同，零差異。

## 方式 B：TCP Socket Gateway

真機台連到 `data_collector` 的 TCP port（預設 `9000`，compose 已對外開），送**換行分隔的 JSON**（每行一個封包，UTF-8）。gateway 解析、正規化後，走與 MQTT 完全相同的處理路徑。

- 一條連線可回報多台機台（封包帶 `machine_id`）。
- 壞封包只記 warning 並跳過，連線不中斷。
- 連線可長連續流（streaming）。

範例（`\n` 為換行）：
```
{"machine_id":"Tray_00","state":"start","product_id":"P000001"}\n
{"machine_id":"Tray_00","state":"working","product_id":"P000001","elapsed_s":3.0,"remaining_s":17.0}\n
{"machine_id":"Tray_00","state":"done","product_id":"P000001"}\n
```

快速手測：
```bash
printf '{"machine_id":"Tray_00","state":"done","product_id":"P9"}\n' | nc localhost 9000
```

## 封包 / 訊息 Schema（兩種方式共用）

對應 `isaac_common.schemas.MachineStateEvent`（SPEC §6.2）：

| 欄位 | 型別 | 必填 | 說明 |
|---|---|---|---|
| `machine_id` | str | 是 | 機台代號，需對齊 config，例 `Tray_00` |
| `state` | str | 是 | `empty` / `start` / `working` / `done` / `error` |
| `product_id` | str \| null | 否 | 台上產品；error 時為被報廢的產品 |
| `elapsed_s` | float | 否 | 本狀態已歷時 |
| `remaining_s` | float | 否 | working 距完成剩餘時間 |
| `ts` | float | 否 | epoch 秒；省略則由 gateway 補當下時間 |

多餘欄位會被忽略；`state` 必須是合法列舉值，否則該封包被拒。

## 接入後會發生什麼

1. `data_collector` 正規化封包 → 寫入歷史事件紀錄表（§7.2）+ 更新即時狀態總表（§7.1）。
2. 重新發佈正規化、retained 的 `telemetry/machine/{id}/state` —— 這就是 `scheduler_engine` 的輸入。
3. scheduler 照常排程、`mqtt_publisher` 照常發手臂指令給 Isaac Sim。

> 切換假→真：把 `machine_simulator` 停掉，改由真機台往 MQTT 或 socket 送即可，其餘服務不動。

## 設定

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `SOCKET_ENABLED` | `true` | 是否啟用 socket gateway |
| `SOCKET_PORT` | `9000` | gateway 監聽 port |
| `SOCKET_HOST` | `0.0.0.0` | 監聽介面 |
