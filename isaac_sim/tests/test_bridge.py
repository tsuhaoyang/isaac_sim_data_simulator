from isaac_arm_controller_example import IsaacArmController, Phase
from mqtt_arm_bridge import IsaacArmBridge


class FakeController:
    def __init__(self):
        self.calls = []

    def pick_place(self, arm_id, pick, place, product_id):
        self.calls.append((arm_id, pick.get("key"), place.get("key"), product_id))


def test_pump_dispatches_queued_commands_to_controller():
    ctrl = FakeController()
    bridge = IsaacArmBridge(ctrl, host="localhost")  # no connect() -> no network
    bridge._q.put({"arm_id": "A1", "pick": {"key": "ProductIn", "pos": [0, 0, 0.1]},
                   "place": {"key": "Tray_00", "pos": [0.5, 0.2, 0.1]}, "product_id": "P1"})
    bridge._q.put({"arm_id": "A2", "pick": {"key": "Tray_04"}, "place": {"key": "ProductOut"},
                   "product_id": "P2"})
    n = bridge.pump()
    assert n == 2
    assert ctrl.calls == [("A1", "ProductIn", "Tray_00", "P1"), ("A2", "Tray_04", "ProductOut", "P2")]
    assert bridge.pump() == 0  # queue drained


def test_command_without_arm_id_is_ignored():
    ctrl = FakeController()
    bridge = IsaacArmBridge(ctrl)
    bridge._q.put({"pick": {"key": "x"}, "place": {"key": "y"}})
    bridge.pump()
    assert ctrl.calls == []


def test_controller_sequence_completes_to_idle():
    ctrl = IsaacArmController(arms={"A1": object()})
    ctrl.pick_place("A1", {"key": "ProductIn", "pos": [0, 0, 0.1]},
                    {"key": "Tray_00", "pos": [0.5, 0.2, 0.1]}, "P1")
    assert ctrl._jobs["A1"].phase is Phase.APPROACH_PICK
    # _reached() returns True in the skeleton, so the sequence advances each update()
    for _ in range(20):
        ctrl.update(1 / 60)
        if ctrl._jobs["A1"].phase is Phase.IDLE:
            break
    assert ctrl._jobs["A1"].phase is Phase.IDLE


def test_command_while_busy_is_queued_not_dropped():
    ctrl = IsaacArmController(arms={"A1": object()})
    pick = {"key": "ProductIn", "pos": [0, 0, 0.1]}
    ctrl.pick_place("A1", pick, {"key": "Tray_00", "pos": [0.5, 0.2, 0.1]}, "P1")
    ctrl.pick_place("A1", pick, {"key": "Tray_01", "pos": [0.5, 0.4, 0.1]}, "P2")  # arm busy -> queued
    assert len(ctrl._pending["A1"]) == 1            # not dropped
    for _ in range(60):                              # drain both jobs
        ctrl.update(1 / 60)
    assert not ctrl._pending["A1"]                  # queue emptied
    assert ctrl._jobs["A1"].phase is Phase.IDLE
