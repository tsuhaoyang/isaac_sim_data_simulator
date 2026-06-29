"""data_collector core (SPEC §2.2 / §7).

Transport-agnostic ingestion + normalization + persistence:
- current snapshot (in-memory, latest per machine) -> §7.1
- append-only event log via EventSink                -> §7.2

Kept free of MQTT so it is unit-testable; main.py wires sources to it.
"""

from isaac_common.event_sink import EventSink
from isaac_common.schemas import EventRecord, MachineState, MachineStateEvent


class Collector:
    def __init__(self, sink: EventSink):
        self.sink = sink
        self.snapshot: dict[str, MachineStateEvent] = {}
        self._last_state: dict[str, str] = {}

    def ingest_machine_state(self, ev: MachineStateEvent) -> EventRecord:
        """A state *transition* from a machine: update snapshot + append to log."""
        prev = self._last_state.get(ev.machine_id)
        self.snapshot[ev.machine_id] = ev
        self._last_state[ev.machine_id] = ev.state.value
        rec = EventRecord(
            ts=ev.ts,
            entity_type="machine",
            entity_id=ev.machine_id,
            event="error" if ev.state == MachineState.ERROR else "state_change",
            from_state=prev,
            to_state=ev.state.value,
            product_id=ev.product_id,
            detail={"elapsed_s": ev.elapsed_s, "remaining_s": ev.remaining_s},
        )
        self.sink.write(rec)
        return rec

    def update_telemetry(self, ev: MachineStateEvent) -> None:
        """A mid-working sample: refresh snapshot only (not a logged transition)."""
        self.snapshot[ev.machine_id] = ev

    def log_event(self, rec: EventRecord) -> EventRecord:
        """Generic passthrough for non-machine events (arm/scheduler commands)."""
        self.sink.write(rec)
        return rec

    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ev in self.snapshot.values():
            counts[ev.state.value] = counts.get(ev.state.value, 0) + 1
        return counts
