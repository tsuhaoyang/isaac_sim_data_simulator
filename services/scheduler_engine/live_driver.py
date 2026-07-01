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
                 arm_times, arrival_interval_s: float, jitter: str,
                 total_products: int, rng: Random, log):
        self.policy = policy
        self.client = client
        self.clock = clock
        self.arm_times = arm_times        # ArmTimes: per-machine load()/unload()
        self.arrival_interval_s = arrival_interval_s
        self.jitter = jitter
        self.total_products = total_products
        self.rng = rng
        self.log = log
        self._lock = threading.Lock()
        self._task_seq = 0
        self._made = 0
        self._gen = 0  # run generation: stale timers from a previous run are ignored

    def start(self) -> None:
        self.client.subscribe(topics.telemetry_state_filter(), self._on_telemetry)
        self._begin_run()

    # --- run control (sim/control) ---
    def restart(self) -> None:
        """Reset world + counters and begin a fresh run (no container restart)."""
        with self._lock:
            self.policy.reset()
            self._made = 0
            self._begin_run()
            self.log.info("=== run restarted (gen=%d) ===", self._gen)

    def stop(self) -> None:
        """Stop releasing new products; in-flight work drains."""
        with self._lock:
            self._gen += 1
            self.log.info("=== run stopped (releasing halted) ===")

    def _begin_run(self) -> None:
        self._gen += 1
        self._schedule_arrival(self._gen)

    # --- inbound events ---
    def _on_telemetry(self, topic: str, payload) -> None:
        try:
            ev = MachineStateEvent.model_validate(payload)
        except Exception:
            return
        self._process(MachineObserved(ev.machine_id, ev.state.value, ev.product_id))

    def _arrive(self, gen: int) -> None:
        if gen != self._gen:
            return  # stale arrival from a previous run
        self._made += 1
        self._process(ProductArrived(f"P{self._made:06d}"))
        self._schedule_arrival(gen)

    def _on_arm_free(self, arm_id: str, gen: int) -> None:
        if gen != self._gen:
            return  # stale arm-free timer from a previous run
        self._process(ArmFreed(arm_id))

    # --- scheduling ---
    def _schedule_arrival(self, gen: int) -> None:
        if self.total_products and self._made >= self.total_products:
            self.log.info("all %d products released", self.total_products)
            return
        delay = (self.rng.expovariate(1.0 / self.arrival_interval_s)
                 if self.jitter == "poisson" else self.arrival_interval_s)
        self.clock.call_later(delay, lambda g=gen: self._arrive(g))

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
        # open-loop: arm is busy for the nominal move time (per machine), then free
        move_s = self.arm_times.load(d.machine_id) if d.kind == "load" else self.arm_times.unload(d.machine_id)
        gen = self._gen
        self.clock.call_later(move_s, lambda a=d.arm_id, g=gen: self._on_arm_free(a, g))
