from isaac_common.event_sink import EventSink
from isaac_common.schemas import EventRecord, MachineState, MachineStateEvent

from collector import Collector


class FakeSink(EventSink):
    def __init__(self):
        self.records: list[EventRecord] = []

    def write(self, rec: EventRecord) -> None:
        rec.seq = len(self.records) + 1
        self.records.append(rec)


def _state(mid, st, product=None):
    return MachineStateEvent(machine_id=mid, state=st, product_id=product)


def test_ingest_tracks_transition_and_snapshot():
    sink = FakeSink()
    c = Collector(sink)

    r1 = c.ingest_machine_state(_state("M01", MachineState.EMPTY))
    r2 = c.ingest_machine_state(_state("M01", MachineState.CHECK_IN, "P1"))
    r3 = c.ingest_machine_state(_state("M01", MachineState.WORKING, "P1"))

    assert r1.from_state is None and r1.to_state == "empty"
    assert r2.from_state == "empty" and r2.to_state == "check_in"
    assert r3.from_state == "check_in" and r3.to_state == "working"
    assert [r.seq for r in sink.records] == [1, 2, 3]
    assert c.snapshot["M01"].state == MachineState.WORKING


def test_error_event_is_classified():
    sink = FakeSink()
    c = Collector(sink)
    c.ingest_machine_state(_state("M02", MachineState.WORKING, "P9"))
    rec = c.ingest_machine_state(_state("M02", MachineState.ERROR, "P9"))
    assert rec.event == "error"


def test_state_counts():
    sink = FakeSink()
    c = Collector(sink)
    c.ingest_machine_state(_state("M01", MachineState.WORKING, "P1"))
    c.ingest_machine_state(_state("M02", MachineState.WORKING, "P2"))
    c.ingest_machine_state(_state("M03", MachineState.EMPTY))
    assert c.state_counts() == {"working": 2, "empty": 1}
