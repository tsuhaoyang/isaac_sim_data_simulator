# Isaac Sim 機器手臂與機台排程資料模擬器 — 規格書 (SPEC)

> 版本 0.1 — 規劃定稿，待實作。
> 本文件為單一事實來源 (single source of truth)。任何設計變更先改本文件再改程式。

---

## 0. 一句話定位

一個由 **simulator 全權驅動** 的工廠排程模擬系統：模擬機台狀態與計時、執行排程演算法決定「哪個產品上哪台機台 / 由哪隻手臂搬運」，並透過 MQTT 把搬運指令發給 **Isaac Sim 純演出**。Isaac Sim 不回傳任何資訊。

---

## 1. 核心前提與約束

| 編號 | 約束 | 影響 |
|---|---|---|
| C1 | Isaac Sim **無法回傳**，只訂閱 MQTT 指令做動畫 | 模擬器是世界唯一事實來源；所有時間用「標稱動作時間」開環推進 |
| C2 | demo 階段沒有真機台、沒有真資料 | 真資料路徑只做**介面**（MQTT / socket），用假機台驗證 |
| C3 | 時間採真實牆鐘 (real-time)，不加速 | 與 Isaac Sim 畫面同步 |
| C4 | 手臂分區：每隻手臂有專屬機台、不共用 | 排程 = 先選 cell（手臂）再選 cell 內機台；以可達性矩陣通用化 |
| C5 | 設計原則：模組化、解耦、可維護、docker 驅動 | 各服務獨立容器，僅靠 MQTT topic 溝通 |

---

## 2. 系統架構

### 2.1 元件圖

```
                          ┌─────────────────────────────────────────┐
                          │            MQTT Broker (Mosquitto)        │
                          │              訊息骨幹 / 解耦點             │
                          └─────────────────────────────────────────┘
   plant/machine/+/*  ▲  │ telemetry/*        scheduler/command ▲ │ isaacsim/arm/+/command
                      │  ▼                                      │ ▼
   ┌──────────────────┴──────┐   ┌──────────────────┐   ┌──────┴───────────┐   ┌──────────────┐
   │  machine_simulator      │   │  data_collector  │   │ scheduler_engine │   │ mqtt_publisher│
   │  (= 假資料來源)          │──▶│  (擷取/正規化層) │──▶│  (排程+容量試算) │──▶│ (指令翻譯)    │──▶ Isaac Sim
   │  N 台機台狀態機+計時+故障│   │  source: mqtt/sock│   │  world model+queue│   │ 解析固定座標  │
   └─────────────────────────┘   └──────────────────┘   └──────────────────┘   └──────────────┘
            ▲ (真機台走相同 MQTT / 或 socket gateway 進 data_collector)
```

### 2.2 各服務職責（單一職責、可獨立部署）

| 服務 | 職責 | 不負責 |
|---|---|---|
| **machine_simulator** | 每台機台一個狀態機，掌管 start/working/done/error/empty 轉移、即時執行時間、隨機故障 downtime；對外發佈遙測。**就是你猶豫的「處理器/機台模擬器」與「假資料產生器」的合體** | 不做排程、不知道產品從哪來 |
| **data_collector** | 統一擷取層：source plugin = `mqtt`（假機台與真機台共用）/ `socket`（真機台 TCP gateway）；把各來源正規化成統一事件 schema 再轉發；**持久化狀態紀錄表（current snapshot + 歷史 event log，見 §7）** | 不做排程業務邏輯 |
| **scheduler_engine** | 維護 world model、進料 FIFO queue、手臂任務 queue、排程決策、error 重導、**容量試算（公式+模擬）** | 不直接碰 Isaac Sim topic |
| **mqtt_publisher** | 把 scheduler 的抽象指令（arm/action/from/to）翻成 Isaac Sim 的 topic 與固定座標 | 不做排程決策 |
| **broker** | Mosquitto，純訊息傳遞 | — |
| **libs/common** | 共用：訊息 schema (pydantic)、MQTT client wrapper、config loader、logging | — |

> **為何 machine_simulator 與 data_collector 分開？** machine_simulator 是「會動的世界」；data_collector 是「不管資料哪來、都長一樣」的介面層。分開後，把 machine_simulator 換成真機台時，data_collector 一行不用改。

---

## 3. 資料流（一個產品的生命週期）

```
1. product_generator 依「進線頻率」生成產品 → 進料 FIFO queue (intake)
2. scheduler(拉動式): 某手臂 A 變空且有 empty 機台 M → 拉 intake 隊頭產品(晚綁定)
3. scheduler 發 scheduler/command: {arm A, action=load, from=ProductIn, to=M, product}
4. publisher → isaacsim/arm/A/command（解析固定座標）→ Isaac Sim 演手臂搬運
5. machine_simulator: M  empty→start→working(作業時間計時)→done
       (working 期間以 hazard 機率 → error → downtime → empty，台上產品報廢)
6. data_collector 收 M 的狀態流 → 正規化 → telemetry/machine/M/state
7. scheduler 看到 M=done → 發 unload 指令(優先)：{arm A, action=unload, from=M, to=ProductOut}
8. publisher → Isaac Sim 演下料 → M 回 empty、產品離線、throughput++
```

開環說明：步驟 4、8 的手臂動作時間，scheduler 用 config 的標稱時間 (`arm_move_time`, `arm_to_tray_time`) 自行計時推進，**不等 Isaac Sim 回報**。

---

## 4. 機台狀態機

```
        arm 放料            載入完成              作業時間到
 empty ─────────▶ start ──────────▶ working ──────────────▶ done
   ▲                                   │                       │
   │                                   │ hazard(隨機)          │ arm 取料
   │              downtime 結束         ▼                       │
   └──────────────────────────────── error ◀──┘  ←(台上產品報廢)
   └──────────────────────────────────────────────────────────┘  (done→empty by arm 取料)
```

| 狀態 | 意義 | 進入條件 | 離開條件 |
|---|---|---|---|
| `empty` | 無產品、可用 | 初始 / arm 取走 done / error downtime 結束 | arm 放料 → `start` |
| `start` | 載入中 | arm 放料完成 | 經 `load_time`(可為0) → `working` |
| `working` | 加工中（回報 elapsed/remaining） | start 完成 | 作業時間到 → `done`；或 hazard 觸發 → `error` |
| `done` | 完成、待取料（**會阻塞機台**） | working 完成 | arm 取料 → `empty` |
| `error` | 故障停機 | working 中隨機觸發 | 經 `error_downtime` → `empty`（產品報廢） |

**隨機故障模型**：working 期間，每台機台以參數 `error_prob_per_job`（每件觸發機率）或 `mtbf_s`（平均故障間隔，指數分佈）擇一決定是否進 error。spec 預設用 `error_prob_per_job`，實作保留 mtbf 選項。

---

## 5. 排程演算法

### 5.1 資源模型
- **Cell**：一隻手臂 + 其可達機台（由可達性矩陣定義；分區即每台機台只屬一個 cell）。
- **手臂**：一次只能執行一個搬運 task（不可分割），佔用時間 = 該動作標稱時間。
- **機台**：一次一件產品。

### 5.2 決策模式：晚綁定 + 拉動式 (late-binding pull)

**選型**：採**線上事件驅動派工**（dispatching rules），**不做**離線最佳化（MILP/CP）。原因：持續進線 + 隨機故障會讓任何預排程立即失效；線上局部決策對擾動天然強韌、且即時、易與 Isaac Sim 同步。離線最佳化僅出現在 §5.5 容量試算（規劃階段）。

**核心模式（取代舊版 push「選最輕 cell」）**：產品**不在進線時綁定機台**，而是待在**單一全域 FIFO intake queue** 不綁定；由**有空檔的手臂主動拉取**。晚綁定對 error 強韌（不會預先綁到之後會壞的機台），且負載均衡自然發生（忙手臂不拉、閒手臂拉）。

每隻手臂變空時的決策優先序（演算法心臟）：
```
arm.try_act():
  1) 我可達機台中有任一台 done → 做 unload     # 最高優先，避免 done 阻塞機台
  2) 否則 有 empty 機台 且 intake 非空 → 拉 intake 隊頭產品，做 load
  3) 否則 idle
```

規則保證：
1. **FIFO**：永遠拉 intake 隊頭。
2. **不停滯**：`unload` 永遠優先於 `load`（done 會自我阻塞機台，不先清就卡死吞吐）；手臂只要可動就動。
3. **負載均衡**：多手臂拉動天然均衡；同時想拉隊頭時用決定性 tie-break（見 §5.4 可插拔規則）。load 選機台時**優先選 arm load 時間最短（最近）的空機台**（arm 時間可 per-machine，見 §7.1 `arm.per_machine`）。
4. **Error 強韌（架構自帶，不需特別「重導」邏輯）**：機台進 error → 非 empty → 自然不會被 `try_act` 選中；其產品報廢不重投；intake 持續流向存活機台；downtime 結束回 empty 自動重新可用。

### 5.3 觸發時機（事件）
`try_act` 在下列事件後對「現在有空的手臂」重跑：產品進線、機台 `done`、機台 `empty`（含 error 復原）、手臂搬運完成 (`load_complete`/`unload_complete`)。

### 5.4 引擎架構：Policy / Driver / Clock 分層（同一份碼跑 demo 也跑容量驗證）

把**決策邏輯**與**時間/傳輸**徹底分離：

```
SchedulingPolicy（純邏輯、無 I/O）
   in : world model + 一個事件      out : 決策(load/unload) + 更新後 world model
        ▲ 同一份 policy
   ┌────┴───────────────┬─────────────────────────────┐
 LiveDriver                              SimDriver
 - RealClock（牆鐘，同步 Isaac Sim）      - VirtualClock（事件間直接跳時間）
 - MQTT adapter：收 telemetry/* 餵事件     - 內嵌 machine 計時+故障、arrival 模型
   把決策→ scheduler/command 發出          - in-process、跑超快
 → 即時 demo                              → 容量試算(§5.5)
```

- **Clock 抽象**：`RealClock`（demo）/ `VirtualClock`（容量模擬，秒級跑完數小時）。
- **同一份 `SchedulingPolicy`** 同時驅動 demo 與容量驗證 → §5.5「模擬驗證」驗的就是真要上線的邏輯。
- **底層計算單元 = 事件佇列**：以時間戳排序的優先佇列，裝 `product_arrival / load_complete / process_complete(→done) / error_trigger / unload_complete / error_recovery`；引擎永遠取最早事件處理（Live 等到該牆鐘時刻、Sim 直接跳）。
- **RNG 可注入種子**（故障、進線抖動）→ 可重現、可測試。

**決策時參考的計算單元**：
- `Machine`：state、`remaining_s`(到 done)、台上產品、所屬 cell。
- `Arm`：busy/free、目前任務剩餘時間、待辦數、可達機台集合。
- `Cell`：empty / working / done-待取 機台數、arm 是否空。
- `intake` FIFO：長度、隊頭等待時間。
- 衍生估計量：手臂單件週期 `t_load + t_unload`（load=`arm_move_time_s`、unload=`arm_to_tray_time_s`）、機台有效產出率 `(1/t_proc)·A`、機台妥善率 `A`。

**可插拔派工規則 (Strategy)**，由 config 選，方便 demo 比較策略對吞吐/利用率的影響：
- intake 取件順序：`FIFO`(預設) / 其他。
- 多手臂搶隊頭 tie-break：`least-loaded` / `round-robin` / `lowest-id`(決定性，預設)。
- cell 內選 empty 機台：`any` / `fixed-order` / `least-recently-used`。

### 5.5 容量試算（b.4）

**輸入**：λ=進線速率(件/秒)=1/進線間隔、t_proc=機台作業時間、t_load/t_unload=手臂上/下料時間（per-machine 時取**各機台平均**——容量決定「幾台」而非「哪台」）、A=機台妥善率。

機台妥善率（由故障參數推得）：
```
A = uptime / (uptime + downtime)，downtime 由 error_downtime 與故障頻率推算
```

**解析公式（理論最小值）**：
```
需求機台數  N_machine ≥ ceil( λ · t_proc / A )
需求手臂數  N_arm     ≥ ceil( λ · (t_load + (1-p)·t_unload) )  # 每件必有上料；下料僅完成品(故障報廢無下料)
```
（分區情境下再把 N_machine 平均分配到各 cell，每 cell 機台數 ≥ N_machine / N_arm。）

**模擬驗證**：用上述建議值實際跑模擬，檢查在「產品數量」全部進線完畢後是否消化得完（intake queue 不無限成長、平均等待時間有界）。不足則 +1 機台/手臂重跑，輸出建議配置與利用率報告。

---

## 6. MQTT Topic 與訊息 Schema

所有 payload 為 JSON、UTF-8；時間戳 `ts` 為 epoch 秒(float)。

### 6.1 Topic 表

| Topic | 方向 | 發佈者 | 訂閱者 | retained |
|---|---|---|---|---|
| `plant/machine/{id}/state` | 上行 | machine_simulator / 真機台 | data_collector | 是 |
| `plant/machine/{id}/telemetry` | 上行 | machine_simulator / 真機台 | data_collector | 否 |
| `telemetry/machine/{id}/state` | 內部 | data_collector | scheduler_engine | 是 |
| `scheduler/command` | 內部 | scheduler_engine | mqtt_publisher | 否 |
| `scheduler/metrics` | 內部 | scheduler_engine | (dashboard/log) | 否 |
| `sim/control` | 控制 | 使用者 / MQTTX | scheduler_engine, machine_simulator, data_collector | 否 |
| `isaacsim/arm/{arm_id}/command` | 下行 | mqtt_publisher | **Isaac Sim** | 否 |
| `isaacsim/machine/state` | 下行 | mqtt_publisher | **Isaac Sim** | 是 |

> Isaac Sim 端只需訂 `isaacsim/#`：手臂指令 (`isaacsim/arm/+/command`) 做動作演出；機台狀態 (`isaacsim/machine/state`，**單一 topic、retained**) 做狀態演出（亮燈/進度），payload 為聚合物件 `{machine_id: {state, product_id, elapsed_s, remaining_s, ts}, ...}`，由 mqtt_publisher 從 `telemetry/machine/+/state` 聚合。完整欄位見 [mqtt_format.md](mqtt_format.md)。

> data_collector 另訂閱 `plant/machine/+/state`、`scheduler/command`、`isaacsim/arm/+/command` 落地為歷史事件紀錄表（§7.2）；`telemetry/.../state` 的 retained 訊息即即時狀態總表（§7.1）。

真機台 socket：`data_collector` 內含 TCP gateway，接受機台連線、把封包正規化後等同 `plant/machine/{id}/*`。

### 6.2 機台狀態事件（`plant/.../state`、`telemetry/.../state`）
```json
{
  "machine_id": "Tray_00",
  "state": "working",
  "product_id": "P000123",
  "elapsed_s": 12.4,
  "remaining_s": 7.6,
  "ts": 1719300000.123
}
```

### 6.3 手臂搬運指令（`scheduler/command`）
```json
{
  "task_id": "T000045",
  "arm_id": "A1",
  "action": "load",
  "from": "ProductIn",
  "to": "Tray_00",
  "product_id": "P000123",
  "ts": 1719300000.5
}
```
`action` ∈ {`load`(ProductIn→機台), `unload`(機台→ProductOut)}；`from`/`to` 為 location key。

### 6.4 Isaac Sim 指令（`isaacsim/arm/{id}/command`）
```json
{
  "arm_id": "A1",
  "action": "move",
  "pick": {"key": "ProductIn", "pos": [-120.0, 10.0, 10.0]},
  "place": {"key": "Tray_00", "pos": [-35.0, 110.0, 40.0]},
  "product_id": "P000123",
  "ts": 1719300000.6
}
```
座標由 publisher 從 `config/positions.json` 解析填入（位置固定）。Isaac Sim 端也可只靠 `key` 自行查表——兩種都支援。

---

## 7. 狀態紀錄表與資料持久化

狀態紀錄分**兩種用途、兩張表**，由 **data_collector** 持有（它本就是「收資料」的角色）；SimDriver(§5.4) 在記憶體中產生**相同結構**，讓容量報告與 demo 用同一套分析。

### 7.1 即時狀態總表 (current snapshot)
「每台機台/手臂**現在**是什麼狀態」的最新快照，給儀表板與排程 world model 對照。
- **天然來源**：MQTT **retained** 訊息 —— `telemetry/machine/{id}/state` 每台 retain 一筆，訂閱即得全廠現況，等同一張免費的 current table。
- data_collector 另維護一份記憶體 dict（machine_id/arm_id → 最新狀態）並可 `GET` 匯出。

| machine_id | state | product_id | elapsed_s | remaining_s | updated_ts |
|---|---|---|---|---|---|
| Tray_00 | working | P000123 | 12.4 | 7.6 | … |

### 7.2 歷史事件紀錄表 (append-only event log)
**每一次狀態轉移與每一筆決策**的不可變流水帳，是吞吐/利用率/等待時間/error 統計與 demo replay 的唯一依據。data_collector 訂閱 `plant/machine/+/state`、`scheduler/command`、`isaacsim/arm/+/command`，全部落地成單一 event log。

| 欄位 | 說明 |
|---|---|
| `seq` | 自增序號 |
| `ts` | epoch 秒 |
| `entity_type` | machine / arm / product |
| `entity_id` | Tray_00 / A1 / P000123 |
| `event` | state_change / load / unload / error / recovery / arrival / scrap |
| `from_state` `to_state` | 機台狀態轉移用 |
| `product_id` | 關聯產品 |
| `detail` | JSON 附加欄（如 task_id、downtime） |

由 event log 可**衍生**產品生命週期表（每件的 arrival→load→done→unload→exit 時間與 outcome=completed/scrapped），不另存、避免重複事實來源。

### 7.3 儲存後端（可換、由 config 決定）
- demo 預設 **SQLite**（單檔、零維運）或 **JSONL** append（最簡、好 grep）。
- 真實大量遙測未來可換 **TimescaleDB / InfluxDB**，介面以 `EventSink` 抽象，service 不綁定實作。
- `config`：`storage.backend = jsonl|sqlite|timescale`、`storage.path/dsn`。

### 7.4 與排程的關係
排程的 world model 是**記憶體即時狀態**（為了快、為了決策）；狀態紀錄表是**對外的事實留存**（為了觀測、稽核、報告）。兩者分離：排程不依賴紀錄表運作，紀錄表壞掉不影響排程正確性。

---

## 8. 設定檔

### 8.1 `config/simulation.json`（使用者調整的全部參數）
```json
{
  "machines": { "count": 6 },
  "arms": { "count": 2 },
  "products": { "total": 50, "arrival_interval_s": 4.0, "arrival_jitter": "poisson" },
  "process": { "machine_process_time_s": 20.0, "machine_load_time_s": 0.0 },
  "arm": {
    "arm_move_time_s": 3.0, "arm_to_tray_time_s": 3.0,
    "per_machine": { "Tray_00": { "load_s": 2.7, "unload_s": 2.9 } }
  },
  "error": { "error_prob_per_job": 0.05, "error_downtime_s": 30.0 },
  "reachability": "partition",
  "reachability_matrix": {
    "A1": ["Tray_00", "Tray_01", "Tray_02"],
    "A2": ["Tray_03", "Tray_04", "Tray_05"]
  },
  "time": { "mode": "realtime" }
}
```
- `reachability="partition"` 時可由 count 自動均分；給 `reachability_matrix` 則精確指定（通用 any-to-any 亦由此表達）。

### 8.2 `config/positions.json`（Isaac Sim 固定上下爪座標）
```json
{
  "locations": {
    "ProductIn":  [-120.0,  10.0, 10.0],
    "ProductOut": [-120.0, -10.0, 10.0],
    "Tray_00": [-35.0, 110.0, 40.0],
    "Tray_01": [55.0, 110.0, 40.0]
  }
}
```

---

## 9. 專案結構

```
issac_sim_data_simulator/
├── docker-compose.yml
├── .env
├── config/
│   ├── simulation.json
│   └── positions.json
├── broker/mosquitto.conf
├── libs/common/            # schemas(pydantic) / mqtt client / config loader / logging
├── services/
│   ├── machine_simulator/  # 狀態機 + 計時 + 隨機故障 + 假資料發佈
│   ├── data_collector/     # source: mqtt / socket → 正規化
│   ├── scheduler_engine/   # 排程 + 容量試算
│   └── mqtt_publisher/     # 指令翻譯 + 座標解析
├── docs/SPEC.md
└── README.md
```
每個 service：自有 `Dockerfile`、`requirements.txt`/`pyproject.toml`、`main.py`、`tests/`。共用邏輯一律進 `libs/common`，禁止 service 間直接 import。

---

## 10. 技術決策

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.11+ | 與 Isaac Sim 一致、生態成熟 |
| MQTT client | paho-mqtt | 穩定通用 |
| Schema/驗證 | pydantic v2 | 統一訊息契約、防呆 |
| Broker | Eclipse Mosquitto | 輕量、docker 友善 |
| 部署 | docker-compose | 一鍵起全棧、解耦 |
| 設定 | JSON（非 .ini） | 巢狀結構表達力佳 |
| 測試 | pytest | — |

---

## 11. 開發里程碑

1. **M0 骨幹**：libs/common（schema + mqtt wrapper + config）、Mosquitto、docker-compose、空殼服務能互通 ping。
2. **M1 機台世界 + 紀錄表**：machine_simulator 狀態機 + 計時 + 故障，發遙測；data_collector(mqtt) 收得到並落地 current snapshot + event log（§7）。
3. **M2 排程閉環**：SchedulingPolicy（拉動式 + try_act 優先序）+ LiveDriver；進料 queue、手臂 task、error 強韌；publisher 發 Isaac Sim 指令（先用 mock subscriber 驗證）。
4. **M3 容量試算**：解析公式 + SimDriver/VirtualClock 跑同一 policy 驗證（多 seed）+ 報告輸出。
5. **M4 真資料介面**：data_collector socket gateway；文件化真機台接入規範。
6. **M5 串接 Isaac Sim**：positions.json 對齊真實場景座標，端到端 demo。

---

## 12. 待確認 / 已知簡化

- 一條生產線餵所有 cell；產品在線上「走到」目標 cell 的輸送時間目前**忽略**（可日後加 `line_travel_time_s`）。
- error 報廢的產品**不重投**，throughput 只計完成件數（如需重投，未來加 `rework_on_error` 開關）。
- 多產品型號 / 多站序列為**未來擴充**；目前單站平行，但可達性與作業時間皆已 config 化，擴充阻力低。
