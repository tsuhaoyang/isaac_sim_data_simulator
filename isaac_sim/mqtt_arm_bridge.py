"""Isaac Sim-side MQTT bridge (M5 part 1).

Subscribes `isaacsim/arm/+/command` and drives the robot arms in the Isaac Sim
stage. This is the ONLY piece that runs inside the Isaac Sim Python environment;
everything upstream (scheduler/publisher) is unchanged.

Threading model (important):
  paho runs callbacks on its own network thread, but Isaac Sim USD/physics APIs
  MUST be touched only from the main app/update thread. So the MQTT callback only
  enqueues commands; `pump()` (called every frame from the Isaac update loop)
  dequeues and dispatches them to the ArmController.

Two ways to run:
  - inside Isaac Sim:  build an IsaacArmController, create IsaacArmBridge, call
    bridge.connect() once and bridge.pump() every frame (see README.md).
  - connectivity check (no Isaac):  `python mqtt_arm_bridge.py --selftest`

Dependency: paho-mqtt only (install into Isaac's python, see README.md).
"""

from __future__ import annotations

import argparse
import json
import queue
import time
from typing import Protocol

import paho.mqtt.client as mqtt

ARM_CMD_TOPIC = "isaacsim/arm/+/command"


class ArmController(Protocol):
    """Plug your Isaac Sim arm control here. Coordinates are [x, y, z] in meters,
    world frame (see docs/positions_guide.md). `key` (e.g. 'Tray_00', 'ProductIn')
    lets you look up per-location approach/orientation config on the Isaac side."""

    def pick_place(self, arm_id: str, pick: dict, place: dict, product_id: str | None) -> None:
        """Start moving arm_id: grasp at pick['pos'], release at place['pos']."""


class LoggingController:
    """Default no-op controller: prints commands. Use for the connectivity check
    and as the shape your real IsaacArmController must implement."""

    def pick_place(self, arm_id: str, pick: dict, place: dict, product_id: str | None) -> None:
        print(f"[arm {arm_id}] pick {pick.get('key')} {pick.get('pos')} -> "
              f"place {place.get('key')} {place.get('pos')}  (product={product_id})")


class IsaacArmBridge:
    def __init__(self, controller: ArmController, *, host: str = "localhost", port: int = 1883,
                 topic: str = ARM_CMD_TOPIC, client_id: str = "isaacsim-arm-bridge"):
        self.controller = controller
        self.host = host
        self.port = port
        self.topic = topic
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._client = mqtt.Client(client_id=client_id,
                                   callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # --- mqtt (network thread) ---
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"[bridge] connected (rc={reason_code}); subscribing {self.topic}")
        client.subscribe(self.topic)

    def _on_message(self, client, userdata, msg):
        try:
            cmd = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            print(f"[bridge] bad payload on {msg.topic}")
            return
        self._q.put(cmd)  # hand off to the main thread; do NOT touch Isaac here

    # --- lifecycle ---
    def connect(self, retries: int = 30, delay_s: float = 2.0) -> None:
        for attempt in range(1, retries + 1):
            try:
                self._client.connect(self.host, self.port, keepalive=30)
                self._client.loop_start()
                return
            except OSError as exc:
                print(f"[bridge] broker not ready ({attempt}/{retries}): {exc}")
                time.sleep(delay_s)
        raise RuntimeError(f"cannot connect to broker {self.host}:{self.port}")

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # --- main thread: call this every Isaac Sim frame ---
    def pump(self, max_items: int = 8) -> int:
        """Dispatch up to max_items queued commands to the controller. Returns count."""
        n = 0
        for _ in range(max_items):
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                break
            self._dispatch(cmd)
            n += 1
        return n

    def _dispatch(self, cmd: dict) -> None:
        arm_id = cmd.get("arm_id")
        if not arm_id:
            return
        self.controller.pick_place(arm_id, cmd.get("pick", {}), cmd.get("place", {}),
                                   cmd.get("product_id"))


def _selftest(host: str, port: int) -> None:
    """Connect to the broker and print arm commands as they arrive (no Isaac Sim)."""
    bridge = IsaacArmBridge(LoggingController(), host=host, port=port)
    bridge.connect()
    print(f"[selftest] listening on {host}:{port} topic={ARM_CMD_TOPIC}; Ctrl-C to stop")
    try:
        while True:
            bridge.pump()        # here we pump in a loop; inside Isaac you'd pump per-frame
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="print commands without Isaac Sim")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()
    if args.selftest:
        _selftest(args.host, args.port)
    else:
        print("Import IsaacArmBridge into your Isaac Sim script (see README.md), "
              "or run with --selftest for a connectivity check.")
