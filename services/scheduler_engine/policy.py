"""SchedulingPolicy — pure online dispatching logic (SPEC §5.2/§5.4).

Late-binding + pull: products sit in a single global FIFO intake unbound; a free
arm pulls work via try_act with priority `unload(done) > load(new)`. No I/O, no
clock -> fully unit-testable. LiveDriver (and the future SimDriver) feed it events
and execute the returned decisions.

World-model authority:
- machine done/empty/error  -> learned from telemetry (machine_simulator is truth)
- machine busy/unloading    -> reserved locally the instant a command is issued,
                               so the same slot is never double-assigned before
                               telemetry catches up.
- arm busy/free             -> tracked locally (no arm telemetry; open-loop timers)
"""

from collections import deque
from dataclasses import dataclass
from enum import Enum

PRODUCT_IN = "ProductIn"     # 進機台：load 從這裡夾起新產品
PRODUCT_OUT = "ProductOut"   # 出機台：unload 把完成品放到這裡


class MStatus(str, Enum):
    FREE = "free"            # empty & unreserved -> loadable
    BUSY = "busy"            # loaded / processing
    DONE = "done"            # finished, needs unload
    UNLOADING = "unloading"  # unload issued, awaiting empty telemetry
    DOWN = "down"            # error downtime


@dataclass
class MachineW:
    id: str
    status: MStatus = MStatus.FREE
    product: str | None = None


@dataclass
class ArmW:
    id: str
    reachable: list[str]
    busy: bool = False


# --- events fed to the policy ---
@dataclass
class ProductArrived:
    product_id: str


@dataclass
class MachineObserved:
    machine_id: str
    state: str            # empty|start|working|done|error
    product_id: str | None = None


@dataclass
class ArmFreed:
    arm_id: str


# --- decision emitted by the policy ---
@dataclass
class Decision:
    kind: str             # "load" | "unload"
    arm_id: str
    machine_id: str
    product_id: str | None
    frm: str
    to: str


class SchedulingPolicy:
    def __init__(self, arms: dict[str, ArmW], machines: dict[str, MachineW]):
        self.arms = arms
        self.machines = machines
        self.intake: deque[str] = deque()
        self.metrics = {"arrivals": 0, "completed": 0, "scrapped": 0}

    def reset(self) -> None:
        """Clear the world model back to all-empty for a fresh run."""
        for m in self.machines.values():
            m.status, m.product = MStatus.FREE, None
        for a in self.arms.values():
            a.busy = False
        self.intake.clear()
        self.metrics = {"arrivals": 0, "completed": 0, "scrapped": 0}

    def handle(self, event) -> list[Decision]:
        if isinstance(event, ProductArrived):
            self.intake.append(event.product_id)
            self.metrics["arrivals"] += 1
        elif isinstance(event, MachineObserved):
            self._observe(event)
        elif isinstance(event, ArmFreed):
            self.arms[event.arm_id].busy = False
        else:
            raise TypeError(f"unknown event: {event!r}")
        return self._try_act()

    def _observe(self, ev: MachineObserved) -> None:
        m = self.machines.get(ev.machine_id)
        if m is None:
            return
        if ev.state == "done":
            m.status = MStatus.DONE
        elif ev.state == "error":
            if m.product is not None:
                self.metrics["scrapped"] += 1      # 在製品報廢
            m.product = None
            m.status = MStatus.DOWN
        elif ev.state == "empty":
            m.product = None
            m.status = MStatus.FREE                 # unload done, or error recovered
        # start/working -> already reserved BUSY, ignore

    def _try_act(self) -> list[Decision]:
        decisions: list[Decision] = []
        for aid in sorted(self.arms):
            arm = self.arms[aid]
            if arm.busy:
                continue

            # priority 1: unload a finished machine (avoid done blocking the machine)
            done_m = next(
                (self.machines[mid] for mid in arm.reachable
                 if self.machines[mid].status == MStatus.DONE),
                None,
            )
            if done_m is not None:
                decisions.append(Decision("unload", aid, done_m.id, done_m.product, done_m.id, PRODUCT_OUT))
                done_m.status = MStatus.UNLOADING
                arm.busy = True
                self.metrics["completed"] += 1
                continue

            # priority 2: load the FIFO-head product onto a free reachable machine
            if self.intake:
                free_m = next(
                    (self.machines[mid] for mid in arm.reachable
                     if self.machines[mid].status == MStatus.FREE),
                    None,
                )
                if free_m is not None:
                    pid = self.intake.popleft()
                    decisions.append(Decision("load", aid, free_m.id, pid, PRODUCT_IN, free_m.id))
                    free_m.status = MStatus.BUSY
                    free_m.product = pid
                    arm.busy = True
        return decisions


def build_world(cfg: dict) -> tuple[dict[str, ArmW], dict[str, MachineW]]:
    """Build cells from config: explicit reachability_matrix, else round-robin partition."""
    matrix = cfg.get("reachability_matrix")
    if matrix:
        arms = {aid: ArmW(aid, list(ms)) for aid, ms in matrix.items()}
        machine_ids = sorted({m for ms in matrix.values() for m in ms})
    else:
        n_m = int(cfg["machines"]["count"])
        n_a = int(cfg["arms"]["count"])
        machine_ids = [f"M{i:02d}" for i in range(1, n_m + 1)]
        arms = {f"A{k + 1}": ArmW(f"A{k + 1}", machine_ids[k::n_a]) for k in range(n_a)}
    machines = {mid: MachineW(mid) for mid in machine_ids}
    return arms, machines
