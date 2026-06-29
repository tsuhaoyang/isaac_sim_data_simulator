from isaac_common.schemas import ArmAction, ArmCommand

from translate import translate

POSITIONS = {"locations": {"ProductIn": [-120.0, 10.0, 10.0], "ProductOut": [-120.0, -10.0, 10.0],
                           "Tray_00": [-35.0, 110.0, 40.0]}}


def test_load_resolves_pick_and_place_positions():
    cmd = ArmCommand(task_id="T1", arm_id="A1", action=ArmAction.LOAD,
                     **{"from": "ProductIn"}, to="Tray_00", product_id="P1")
    ic = translate(cmd, POSITIONS)
    assert ic.arm_id == "A1" and ic.action == "move"
    assert ic.pick.key == "ProductIn" and ic.pick.pos == [-120.0, 10.0, 10.0]
    assert ic.place.key == "Tray_00" and ic.place.pos == [-35.0, 110.0, 40.0]
    assert ic.product_id == "P1"


def test_unload_swaps_direction():
    cmd = ArmCommand(task_id="T2", arm_id="A1", action=ArmAction.UNLOAD,
                     **{"from": "Tray_00"}, to="ProductOut", product_id="P1")
    ic = translate(cmd, POSITIONS)
    assert ic.pick.key == "Tray_00" and ic.place.key == "ProductOut"


def test_missing_position_is_none():
    cmd = ArmCommand(task_id="T3", arm_id="A1", action=ArmAction.LOAD,
                     **{"from": "ProductIn"}, to="Tray_99", product_id="P1")
    ic = translate(cmd, POSITIONS)
    assert ic.place.pos is None
