from isaac_common.arm_timing import ArmTimes


def test_additive_base_plus_delta():
    cfg = {"arm": {"arm_move_time_s": 5.0, "arm_to_tray_time_s": 4.0,
                   "per_machine": {"Tray_00": {"load_s": 2.7, "unload_s": 2.9}}}}
    at = ArmTimes(cfg)
    assert at.load("Tray_00") == 5.0 + 2.7      # base + delta
    assert at.unload("Tray_00") == 4.0 + 2.9
    assert at.load("Tray_09") == 5.0            # unlisted -> delta 0 -> just base
    assert at.unload("Tray_09") == 4.0


def test_averages_over_machines():
    cfg = {"arm": {"arm_move_time_s": 5.0, "arm_to_tray_time_s": 5.0,
                   "per_machine": {"A": {"load_s": 2.0, "unload_s": 4.0},
                                   "B": {"load_s": 4.0, "unload_s": 2.0}}}}
    # loads: 7,9 -> mean 8 ; unloads: 9,7 -> mean 8
    assert ArmTimes(cfg).averages(["A", "B"]) == (8.0, 8.0)


def test_missing_arm_block_uses_base_defaults():
    at = ArmTimes({})
    assert at.load("x") == 3.0 and at.unload("x") == 3.0
