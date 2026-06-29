# MQTT 資料格式與欄位說明（Isaac Sim 接口）

Isaac Sim 端只需處理**兩個 topic**：① 手臂指令、② 機台狀態。其餘為內部 topic（見文末）。

## 連線

| 項目 | 值 |
|---|---|
| Broker | Mosquitto |
| Host / Port | `localhost:1883`（不同機則填 docker 主機 IP） |
| 認證 | 無（允許匿名） |
| 編碼 | JSON、UTF-8 |
| 時間戳 `ts` | epoch 秒（float） |

> 訂閱 `isaacsim/#` 即同時收到下列兩種。拼字是 **`isaacsim`**（i-s-a-a-c），別打成 `issacsim`。

---

## ① 手臂指令

- **Topic**：`isaacsim/arm/{arm_id}/command`（每隻手臂一個；目前只有 `A1`）
- **retained**：否（只有派工時送出，需在動作發生時即時處理）
- **用途**：叫某手臂從 `pick` 夾起、移到 `place` 放下

```json
{
  "arm_id": "A1",
  "action": "move",
  "pick":  { "key": "ProductIn", "pos": [-120.0, 10.0, 10.0] },
  "place": { "key": "Tray_00",   "pos": [-35.0, 110.0, 40.0] },
  "product_id": "P000123",
  "ts": 1782376605.1
}
```

| 欄位 | 型別 | 說明 |
|---|---|---|
| `arm_id` | str | 手臂代號（`A1`…） |
| `action` | str | 固定 `"move"`（一次 pick→place 搬運） |
| `pick.key` | str | 夾取點代號（`ProductIn` / `ProductOut` / 機台 `Tray_00`…） |
| `pick.pos` | [float,float,float] | 夾取點座標 `[x,y,z]`，world frame、Z 向上 |
| `place.key` | str | 放置點代號 |
| `place.pos` | [float,float,float] | 放置點座標 |
| `product_id` | str \| null | 搬運的產品（demo 用，可忽略） |
| `ts` | float | 送出時間 |

- **上料 (load)**：`pick=ProductIn`（進機台）、`place=機台`
- **下料 (unload)**：`pick=機台`、`place=ProductOut`（出機台）
- 座標來自 [`config/positions.json`](../config/positions.json)（慣例見 [positions_guide.md](positions_guide.md)）。`key` 可在 Isaac 端查本地的接近高度/朝向（不走 MQTT）。

---

## ② 機台狀態（單一 topic、聚合所有機台）

- **Topic**：`isaacsim/machine/state`（**單一 topic**，含全部機台）
- **retained**：是（一連上就收到全部機台現況）
- **用途**：機台亮燈/變色/進度條
- **payload**：以**機台代號為 key**，值為該機台的狀態物件

```json
{
  "Tray_00": { "state": "working", "product_id": "P000015", "elapsed_s": 12.4, "remaining_s": 37.6, "ts": 1782376605.1 },
  "Tray_01": { "state": "empty",   "product_id": null,       "elapsed_s": 0.0,  "remaining_s": 0.0,  "ts": 1782376604.9 },
  "Tray_02": { "state": "error",   "product_id": null,       "elapsed_s": 5.0,  "remaining_s": 0.0,  "ts": 1782376603.2 }
}
```

每台機台（value）的欄位：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `state` | str | 機台狀態，見下表 |
| `product_id` | str \| null | 台上產品；`empty`/`error` 後為 `null` |
| `elapsed_s` | float | 目前狀態已歷時（秒） |
| `remaining_s` | float | `working` 距完成剩餘秒數（其他狀態為 0） |
| `ts` | float | 該機台最後更新時間 |

`state` 列舉值：

| state | 意義 | 建議顏色/燈號 |
|---|---|---|
| `empty` | 無產品、可用 | 灰 |
| `start` | 載入中（通常極短） | 黃 |
| `working` | 加工中（可用 `remaining_s` 做進度） | 綠 |
| `done` | 完成、待手臂取料 | 藍 |
| `error` | 故障停機（台上產品報廢；停機一段時間後回 `empty`） | 紅 |

> 整包是「目前全部機台的快照」。每當任一機台更新，會重發整包（retained）。Isaac 端直接以整包覆蓋自己的狀態表即可。

---

## 內部 topic（debug 用，Isaac 端不需處理）

| Topic | 說明 |
|---|---|
| `plant/machine/{id}/state` `/telemetry` | machine_simulator / 真機台上行的原始狀態 |
| `telemetry/machine/{id}/state` | data_collector 正規化後（每台一個，retained）；機台聚合即由此而來 |
| `scheduler/command` | 排程原始手臂指令（翻譯成 isaacsim 前） |
| `scheduler/metrics` | 吞吐/完成/報廢/佇列指標 |

完整系統契約見 [SPEC.md §6](SPEC.md)。
