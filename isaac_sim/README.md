# Isaac Sim 端 MQTT Bridge（M5）

把 `scheduler_engine → mqtt_publisher` 發出的手臂指令（topic `isaacsim/arm/{arm_id}/command`）轉成 Isaac Sim 裡的手臂動作。**只有這個資料夾的程式跑在 Isaac Sim 環境裡**，上游服務完全不用動。

## 指令格式（你會收到的）

```json
{
  "arm_id": "A1",
  "action": "move",
  "pick":  {"key": "ProductIn", "pos": [-120.0, 10.0, 10.0]},
  "place": {"key": "Tray_00",   "pos": [-35.0, 110.0, 40.0]},
  "product_id": "P000123",
  "ts": 1782376605.1
}
```

- `pos` 是 [x, y, z]（公尺、world frame、Z 向上）。座標來源與慣例見 [../docs/positions_guide.md](../docs/positions_guide.md)。
- `key` 讓你在 Isaac 端用同一組代號查「靠近高度 / 夾爪開合 / 朝向」等本地參數（這些**不走 MQTT**）。
- `load`：pick=`ProductIn`、place=機台；`unload`：pick=機台、place=`ProductOut`。

## 機台狀態（你會收到的，用於狀態演出）

除了手臂指令，全部機台狀態也推到 Isaac 命名空間（**單一 topic**），可拿來做亮燈/變色/進度條：

- Topic：`isaacsim/machine/state`（**retained**，一連上就收到全部機台現況）
- Payload：以**機台代號為 key** 的聚合物件

```json
{
  "Tray_00": { "state": "working", "product_id": "P000015", "elapsed_s": 12.4, "remaining_s": 37.6, "ts": 1782376605.1 },
  "Tray_01": { "state": "empty",   "product_id": null,       "elapsed_s": 0.0,  "remaining_s": 0.0,  "ts": 1782376604.9 }
}
```

- 顯示顏色/燈號用 `state`（`empty`/`start`/`working`/`done`/`error`）；進度條用 `elapsed_s` / `remaining_s`。
- `error` = 故障停機（台上產品報廢），一段時間後變回 `empty`。
- 每次任一機台更新都重發整包；Isaac 端直接以整包覆蓋自己的狀態表即可。

> 完整欄位與列舉值見 [../docs/mqtt_format.md](../docs/mqtt_format.md)。訂閱可寫在你的 viz 程式，或擴充本資料夾的 bridge（`client.subscribe("isaacsim/machine/state", ...)`）。

## 安裝

把 paho-mqtt 裝進 Isaac Sim 內建 python（在 Isaac Sim 安裝目錄）：

```bash
./python.sh -m pip install paho-mqtt
```

## 1) 連線自測（不需 Isaac Sim）

先確認收得到指令（broker 要在跑、且有 scheduler 在發）：

```bash
python mqtt_arm_bridge.py --selftest --host <broker_host> --port 1883
```

`<broker_host>` = 跑 docker 那台機器的 IP（同機就 `localhost`）。會把每筆手臂指令印出來。

## 2) 接進 Isaac Sim

`pick_place` 是非阻塞的：MQTT 回呼只把指令丟進 queue，主迴圈每幀 `pump()` 取出、`update(dt)` 推進動作狀態機。骨架在 [isaac_arm_controller_example.py](isaac_arm_controller_example.py)，把其中 `_move / _reached / _gripper` 的 TODO 換成你的 motion API（RMPflow/Lula、ArticulationController、夾爪）。

standalone script 範例：

```python
from omni.isaac.kit import SimulationApp
sim = SimulationApp({"headless": False})

from omni.isaac.core import World
from mqtt_arm_bridge import IsaacArmBridge
from isaac_arm_controller_example import IsaacArmController

world = World()
# ... 載入場景、建立每隻手臂的 articulation / motion-gen，放進 controller.arms ...
controller = IsaacArmController(arms={...})

bridge = IsaacArmBridge(controller, host="localhost", port=1883)
bridge.connect()

world.reset()
while sim.is_running():
    bridge.pump()                 # 主執行緒：把收到的指令派給 controller
    controller.update(1.0 / 60)   # 推進手臂動作
    world.step(render=True)

bridge.close()
sim.close()
```

> 同步說明：simulator 端是**開環**，假設手臂動作在設定的 `arm_move_time_s` 內完成（不等 Isaac 回報）。讓 Isaac 動作大致在這個時間內完成，畫面與排程就會對得上；不需精準 ack。

## 並行與緩衝（重要）

Isaac Sim 本身不會把同一隻手臂的多個指令排隊；重疊指令會亂掉。本系統用兩層保護：

1. **服務端 per-arm 序列化（已內建）**：scheduler 發指令即把該手臂標 busy，要等 `arm_move_time_s`（開環）才釋放、才會發下一個。所以同一手臂兩指令最少間隔 = `arm_move_time_s`。
2. **Isaac 端 per-arm FIFO 佇列（本範本）**：`IsaacArmController` 對忙碌中的手臂**排隊依序執行、不丟棄**（見 `pick_place`/`update`）。

> ⚠️ **關鍵**：第 1 層是「靠時間猜」的開環保護，正確性取決於設定值 ≥ Isaac 裡實際耗時。設太小 → scheduler 太早發下一個指令。請抓保守值。load 與 unload **分別計時**：
> - `arm_move_time_s` = 上料（ProductIn → 機台）單次 pick-place 的時間
> - `arm_to_tray_time_s` = 下料（機台 → ProductOut）單次 pick-place 的時間
>
> 兩者都指**單一次**動作、不是來回。

## 檔案

- `mqtt_arm_bridge.py` — MQTT→queue→pump 橋接（穩定，通常不用改）。
- `isaac_arm_controller_example.py` — 手臂動作狀態機骨架（填 TODO）。
- `requirements.txt` — paho-mqtt。
