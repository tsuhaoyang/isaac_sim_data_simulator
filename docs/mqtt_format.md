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
| `check_in` | Tray 收合中（產品放上後、加工前） | 黃 |
| `working` | 加工中（可用 `remaining_s` 做進度） | 綠 |
| `check_out` | Tray 吐出中（加工後、可取料前） | 青 |
| `done` | 完成、待手臂取料 | 藍 |
| `error` | **測試 FAIL 復原中**（`fail_recovery_s` 後回 `working` 續測、不報廢）；真機台則為硬體故障 | 紅 |

> 整包是「目前全部機台的快照」。每當任一機台更新，會重發整包（retained）。Isaac 端直接以整包覆蓋自己的狀態表即可。

---

## 內部 topic（debug 用，Isaac 端不需處理）

| Topic | 說明 |
|---|---|
| `plant/machine/{id}/test` | 逐筆測項（測試機每 row_interval 發一筆，含 FAIL path）；data_collector 落地、**不轉 Isaac** |
| `plant/machine/{id}/state` `/telemetry` | machine_simulator / 真機台上行的原始狀態 |
| `telemetry/machine/{id}/state` | data_collector 正規化後（每台一個，retained）；機台聚合即由此而來 |
| `scheduler/command` | 排程原始手臂指令（翻譯成 isaacsim 前） |
| `scheduler/metrics` | 吞吐/完成/報廢/佇列指標 |
| `sim/control` | 執行控制（重跑/停止），payload `{"cmd":"start"\|"stop"}` |

## 執行控制（重跑，不需重啟 container）

往 `sim/control` 發訊息即可重置並重跑：

| payload | 效果 |
|---|---|
| `{"cmd":"start"}` | 重置所有機台/佇列/指標，產品從 P000001 重新進線（一輪跑完後可重複發） |
| `{"cmd":"stop"}` | 停止放新料，在製品做完後系統閒置 |

## 逐筆測項（`plant/machine/{id}/test`，內部，不轉 Isaac）

每台測試機在 `working` 期間每 `row_interval_s` 發一筆測項（來自測試報告）。遇 FAIL 照發、停 `fail_recovery_s` 再續（不中止整板）。data_collector 落地為歷史紀錄。

```json
{
  "machine_id": "Tray_00", "product_id": "P000001",
  "index": 120, "total": 265,
  "board": "BMC_Interposer", "item": "BMC_DP_AUX_DN",
  "result": "FAIL", "fault": "Power",
  "path": ["Riser Card.J1004.B32", "Riser Card.J22.A13", "…", "J1_Riser.PCIE1.A78"],
  "path_tree": [
    { "board": "Riser Card",      "nodes": ["J1004.B32", "J22.A13"] },
    { "board": "SPB_PCIE_Riser2", "nodes": ["J1.A13", "R5.1", "R5.2", "J3.A33"] },
    { "board": "BMC_Interposer",  "nodes": ["J7.A33", "U1.41", "U1.4", "J1.OB5"] },
    { "board": "J1_Riser",        "nodes": ["J1.OB5", "PCIE1.A78"] }
  ],
  "ts": 1784024655.4
}
```

| 欄位 | 說明 |
|---|---|
| `index` / `total` | 第幾筆 / 總測項數 |
| `board` / `item` | 板名 / 測項名（device 或 net） |
| `result` | `PASS` \| `FAIL` |
| `fault` | `Power` \| `GND` \| null（FAIL 才有） |
| `path` | FAIL 扁平路徑（`:` 之後以 `->` 切成 list；PASS 為空） |
| `path_tree` | 同一路徑的**階層版**：每段 `{board, nodes}`。節點格式為 `board.component.pin`，以第一個 `.` 切出 board 當父階層、其餘掛在 `nodes`（例 `SPB_PCIE_Riser2.J1.A13` → board `SPB_PCIE_Riser2`、node `J1.A13`）|

> ⚠️ `path_tree` 是**有序 segment**，不是 dict grouping：同一板子**連續**的節點歸一段；板子**重複經過**（走過去又繞回來）會是**獨立的一段**。例如 GND 那條 `I2C_3V3_5_SCL` 的 board 依序是
> `Riser Card → SPB_PCIE_Riser → BMC_Interposer → SPB_PCIE_Riser2 → Riser Card`（`Riser Card` 頭尾各一段）。
> 若合併成 dict 會毀掉往返順序，所以刻意保持 list。`path` 與 `path_tree` 內容等價，節點數守恆。

完整系統契約見 [SPEC.md §6](SPEC.md)。
