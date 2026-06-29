"""LiveDriver — wires the pure SchedulingPolicy to MQTT + a real clock (SPEC §5.4).

- product arrivals: clock timer (Poisson/fixed) -> ProductArrived
- machine telemetry: telemetry/machine/+/state    -> MachineObserved
- arm completion:  open-loop timer (arm_move_s)    -> ArmFreed   (no Isaac Sim ack)
Decisions become ArmCommand on scheduler/command. All event handling is serialized
by one lock so the policy stays logically single-threaded.
"""

import threading
from random import Random

from isaac_common import topics
from isaac_common.clock import Clock
from isaac_common.mqtt_client import MqttClient
from isaac_common.schemas import ArmAction, ArmCommand, MachineStateEvent

from policy import ArmFreed, MachineObserved, ProductArrived, SchedulingPolicy


class LiveDriver:
    def __init__(self, policy: SchedulingPolicy, client: MqttClient, clock: Clock, *,
                 arm_load_s: float, arm_unload_s: float, arrival_interval_s: float, jitter: str,
                 total_products: int, rng: Random, log):
        self.policy = policy
        self.client = client
        self.clock = clock
        self.arm_load_s = arm_load_s      # ProductIn -> machine
        self.arm_unload_s = arm_unload_s  # machine -> ProductOut (至盤)
        self.arrival_interval_s = arrival_interval_s
        self.jitter = jitter
        self.total_products = total_products
        self.rng = rng
        self.log = log
        self._lock = threading.Lock()
        self._task_seq = 0
        self._made = 0

    def start(self) -> None:
        self.client.subscribe(topics.telemetry_state_filter(), self._on_telemetry)
        self._schedule_arrival()

    # --- inbound events ---
    def _on_telemetry(self, topic: str, payload) -> None:
        try:
            ev = MachineStateEvent.model_validate(payload)
        except Exception:
            return
        self._process(MachineObserved(ev.machine_id, ev.state.value, ev.product_id))

    def _arrive(self) -> None:
        self._made += 1
        self._process(ProductArrived(f"P{self._made:06d}"))
        self._schedule_arrival()

    def _on_arm_free(self, arm_id: str) -> None:
        self._process(ArmFreed(arm_id))

    # --- scheduling ---
    def _schedule_arrival(self) -> None:
        if self.total_products and self._made >= self.total_products:
            self.log.info("all %d products released", self.total_products)
            return
        delay = (self.rng.expovariate(1.0 / self.arrival_interval_s)
                 if self.jitter == "poisson" else self.arrival_interval_s)
        self.clock.call_later(delay, self._arrive)

    def _process(self, event) -> None:
        with self._lock:
            for d in self.policy.handle(event):
                self._emit(d)

    def _emit(self, d) -> None:
        self._task_seq += 1
        cmd = ArmCommand(
            task_id=f"T{self._task_seq:06d}", arm_id=d.arm_id, action=ArmAction(d.kind),
            from_=d.frm, to=d.to, product_id=d.product_id,
        )
        self.client.publish_json(topics.SCHEDULER_COMMAND, cmd)
        self.log.info("%s %s: %s %s->%s product=%s",
                      cmd.task_id, d.arm_id, d.kind, d.frm, d.to, d.product_id)
        # open-loop: arm is busy for the nominal move time, then free
        move_s = self.arm_load_s if d.kind == "load" else self.arm_unload_s
        self.clock.call_later(move_s, lambda a=d.arm_id: self._on_arm_free(a))
