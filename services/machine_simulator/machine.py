"""Per-machine state machine + timing + random fault (SPEC §4).

Transport-agnostic and deterministic: `tick(now)` is driven by an injected clock
value and an injected RNG, so it is unit-testable without MQTT or wall time.

State flow:  empty -> start -> working -> done -> empty
             working --hazard--> error --downtime--> empty   (在製品報廢)

Two drive modes:
- autonomous: machine self-loads synthetic products when empty (M1 fake-data / a.1).
- driven:     external load()/unload() triggers it (used from M2 via arm commands).
"""

from dataclasses import dataclass, field
from random import Random

from isaac_common.schemas import MachineState, MachineStateEvent


@dataclass
class MachineConfig:
    process_time_s: float
    load_time_s: float = 0.0
    error_prob_per_job: float = 0.0
    error_downtime_s: float = 30.0
    telemetry_interval_s: float = 1.0
    autonomous: bool = True
    idle_before_load_s: float = 1.0   # autonomous: empty -> next load
    done_hold_s: float = 1.0          # autonomous: done -> auto unload


@dataclass
class TickOutput:
    """What changed this tick. main() turns these into MQTT publishes."""
    transitions: list[MachineStateEvent] = field(default_factory=list)  # -> plant/.../state (retained)
    telemetry: MachineStateEvent | None = None                          # -> plant/.../telemetry


class Machine:
    def __init__(self, machine_id: str, cfg: MachineConfig, rng: Random, now: float, next_product_id):
        self.id = machine_id
        self.cfg = cfg
        self.rng = rng
        self._next_product_id = next_product_id  # callable() -> str
        self.state = MachineState.EMPTY
        self.product_id: str | None = None
        self._entered = now
        self._deadline = now + cfg.idle_before_load_s  # next scheduled transition (per-state meaning)
        self._error_at: float | None = None
        self._last_tele = now
        self._pending_load: str | None = None   # driven mode
        self._pending_unload = False            # driven mode

    def reset(self, now: float) -> None:
        """Back to empty for a fresh run (cancels any in-progress job)."""
        self.state = MachineState.EMPTY
        self.product_id = None
        self._entered = now
        self._deadline = now + self.cfg.idle_before_load_s
        self._error_at = None
        self._last_tele = now
        self._pending_load = None
        self._pending_unload = False

    # ---- external triggers (driven mode) ----
    def request_load(self, product_id: str) -> None:
        self._pending_load = product_id

    def request_unload(self) -> None:
        self._pending_unload = True

    # ---- helpers ----
    def _enter(self, state: MachineState, now: float) -> MachineStateEvent:
        self.state = state
        self._entered = now
        self._last_tele = now
        return self._event(now)

    def _event(self, now: float) -> MachineStateEvent:
        remaining = max(self._deadline - now, 0.0) if self.state == MachineState.WORKING else 0.0
        return MachineStateEvent(
            machine_id=self.id,
            state=self.state,
            product_id=self.product_id,
            elapsed_s=round(now - self._entered, 3),
            remaining_s=round(remaining, 3),
        )

    def _begin_job(self, product_id: str, now: float, out: TickOutput) -> None:
        self.product_id = product_id
        out.transitions.append(self._enter(MachineState.START, now))
        self._deadline = now + self.cfg.load_time_s

    def _begin_working(self, now: float, out: TickOutput) -> None:
        self._deadline = now + self.cfg.process_time_s
        # decide up-front whether (and when) this job faults
        if self.rng.random() < self.cfg.error_prob_per_job:
            self._error_at = now + self.rng.uniform(0.0, self.cfg.process_time_s)
        else:
            self._error_at = None
        out.transitions.append(self._enter(MachineState.WORKING, now))

    # ---- main tick ----
    def tick(self, now: float) -> TickOutput:
        out = TickOutput()
        st = self.state

        if st == MachineState.EMPTY:
            if self._pending_load is not None:
                self._begin_job(self._pending_load, now, out)
                self._pending_load = None
            elif self.cfg.autonomous and now >= self._deadline:
                self._begin_job(self._next_product_id(), now, out)

        elif st == MachineState.START:
            if now >= self._deadline:
                self._begin_working(now, out)

        elif st == MachineState.WORKING:
            if self._error_at is not None and now >= self._error_at:
                out.transitions.append(self._enter(MachineState.ERROR, now))  # event carries scrapped product
                self.product_id = None                                        # 在製品報廢
                self._error_at = None
                self._deadline = now + self.cfg.error_downtime_s
            elif now >= self._deadline:
                out.transitions.append(self._enter(MachineState.DONE, now))
                if self.cfg.autonomous:
                    self._deadline = now + self.cfg.done_hold_s
            elif now - self._last_tele >= self.cfg.telemetry_interval_s:
                self._last_tele = now
                out.telemetry = self._event(now)

        elif st == MachineState.DONE:
            if self._pending_unload or (self.cfg.autonomous and now >= self._deadline):
                self._pending_unload = False
                self.product_id = None
                out.transitions.append(self._enter(MachineState.EMPTY, now))
                self._deadline = now + self.cfg.idle_before_load_s

        elif st == MachineState.ERROR:
            if now >= self._deadline:
                out.transitions.append(self._enter(MachineState.EMPTY, now))
                self._deadline = now + self.cfg.idle_before_load_s

        return out
