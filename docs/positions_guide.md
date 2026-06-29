# 座標規範 (positions.json)

`config/positions.json` 定義每個「上下爪定點」的座標，`mqtt_publisher` 會把它填進發給 Isaac Sim 的指令 `pick.pos` / `place.pos`。這是 simulator 與 Isaac Sim 之間唯一的座標約定。

## 結構

```json
{
  "meta": { "frame": "world", "units": "meters", "up_axis": "Z" },
  "locations": {
    "ProductIn":  [-120.0,  10.0, 10.0],
    "ProductOut": [-120.0, -10.0, 10.0],
    "Tray_00": [-35.0, 110.0, 40.0]
  }
}
```

- `locations` 的每個 key 是一個定點代號，value 是 `[x, y, z]`。
- `meta` 純屬自我說明，程式不讀（`mqtt_publisher` 只讀 `locations`）。

## 慣例

| 項目 | 約定 |
|---|---|
| 參考座標系 | USD stage 的 **world frame** |
| 單位 | **公尺 (m)** —— Isaac Sim 預設 |
| 軸向 | **Z 向上** —— Isaac Sim 預設 world |
| 點的語意 | 產品「擺放/夾取點」＝夾爪 TCP 的目標位置（不是手臂底座） |

## 代號規則

- `ProductIn`：進機台點（load 從這裡夾起新產品）。
- `ProductOut`：出機台點（unload 把完成品放到這裡）。
- 機台點（例 `Tray_00`、`Tray_01`…）：每台機台的上下料點。**代號必須對齊** [config/simulation.json](../config/simulation.json) 的 `reachability_matrix`（機台名以此為準；`machine_simulator` 也讀同一份）。

## 不放進 positions.json 的東西（這些在 Isaac 端設定）

`pos` 只給「目標點」。下列屬於手臂如何接近的細節，留在 Isaac 端、依 `key` 查表（見 [../isaac_sim/isaac_arm_controller_example.py](../isaac_sim/isaac_arm_controller_example.py) 的 `LOCATION_CONFIG`），**不走 MQTT**：

- 接近高度 (approach offset / pre-grasp)
- 夾爪朝向 (orientation / quaternion)
- 夾爪開合寬度、速度、力道

這樣 MQTT 契約維持最小（符合「發給 Isaac Sim 只需位置」的設計）。

## 如何取得實際座標

1. 在 Isaac Sim 場景擺好 trade 盤與各機台的上下料治具。
2. 讀治具上「產品定點」prim 的 world translate（例如 `XformPrim.get_world_pose()` 取位置，或 GUI 屬性面板的 World Transform）。
3. 把 `[x, y, z]`（公尺）貼進對應 key。
4. 機台代號順序建議與 `reachability_matrix` 一致，方便對照。
