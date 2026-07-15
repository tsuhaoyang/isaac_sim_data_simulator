# CLAUDE.md — 專案開發守則

> Isaac Sim 機器手臂與機台排程資料模擬器。完整設計見 [docs/SPEC.md](docs/SPEC.md)，**SPEC 是唯一事實來源**；任何設計變更先改 SPEC 再改碼。

## 專案本質（30 秒理解）
- 由 **simulator 全權驅動**的工廠排程模擬。Isaac Sim **只訂閱 MQTT 做演出、不回傳**。
- 四個解耦服務透過 **MQTT broker** 溝通：`machine_simulator`（=假資料/機台狀態機）→ `data_collector`（擷取正規化）→ `scheduler_engine`（排程+容量試算）→ `mqtt_publisher`（翻成 Isaac Sim 指令）。
- 「假資料」= machine_simulator 往 MQTT 發；「真資料」= 真機台走**相同** MQTT 或 socket。data_collector 對兩者一視同仁。

## 鐵則
1. **模組化/解耦**：service 之間**只**靠 MQTT topic 溝通，**禁止**互相 import。共用邏輯一律進 `libs/common`。
2. **訊息契約**：所有 MQTT payload 用 `libs/common` 的 pydantic schema 定義與驗證，不手刻 dict。topic 與 schema 以 SPEC §6 為準。
3. **設定驅動**：使用者參數只能來自 `config/simulation.json`；Isaac Sim 座標只能來自 `config/positions.json`。不可寫死。
4. **真實時間**：real-time 推進；手臂動作用 config 標稱時間開環計時，**不等 Isaac Sim 回報**。
5. **docker 驅動**：每個 service 自帶 Dockerfile，全棧用 `docker-compose up` 起。

## 排程演算法（SPEC §5，不可偏離）
- **線上事件驅動派工**，非離線最佳化（離線只用於 §5.5 容量試算）。
- **晚綁定 + 拉動式**：產品待在單一全域 FIFO intake，不預綁機台；手臂變空時 `try_act`，**unload(done) 優先於 load**。
- **Policy / Driver / Clock 分層**：`SchedulingPolicy` 純邏輯無 I/O；`LiveDriver`(RealClock+MQTT) 跑 demo、`SimDriver`(VirtualClock+內嵌模型) 跑容量驗證，**共用同一份 policy**。RNG 可注入種子。

## 機台狀態機（不可偏離）
`empty → check_in(Tray收合) → working → check_out(Tray吐出) → done → empty`。每台 Tray 是**測試機**：`working` = 逐筆串流測項（來自 `test_data` 報告，每 `row_interval_s` 一筆），遇 FAIL → 進 `error` 停 `fail_recovery_s` → 回 `working` 續測（不中止、不報廢、verdict=FAIL），測項細節（含解析後 FAIL `path` 扁平版 + `path_tree` 階層版，以節點第一個 `.` 切 board 分段、板子重複經過保留為獨立段）走 `plant/machine/{id}/test`、**不轉 Isaac**。scheduler 把工作中的 `error` 當「還在忙、復原中」不 scrap（policy._observe）。

## 狀態紀錄表（SPEC §7）
- **即時狀態總表**：MQTT retained 訊息 + collector 記憶體快照。
- **歷史事件紀錄表**：append-only event log（由 collector 訂閱 plant/scheduler/isaacsim 落地），是所有指標與 replay 的唯一依據；產品生命週期由它衍生、不另存。
- 排程 world model（記憶體）與紀錄表（對外留存）**分離**：紀錄表故障不影響排程正確性。

## 開發慣例
- Python 3.11+、pydantic v2、paho-mqtt、pytest。
- 每個 service：`main.py` + `Dockerfile` + `requirements.txt` + `tests/`。
- 新增功能先確認對應里程碑（SPEC §11）與是否需更新 SPEC / config schema。
