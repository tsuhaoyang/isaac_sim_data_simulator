from isaac_common.arm_timing import ArmTimes


def test_per_machine_overrides_with_fallback():
    cfg = {"arm": {"arm_move_time_s": 3.0, "arm_to_tray_time_s": 4.0,
                   "per_machine": {"Tray_00": {"load_s": 2.0, "unload_s": 2.5}}}}
    at = ArmTimes(cfg)
    assert at.load("Tray_00") == 2.0 and at.unload("Tray_00") == 2.5
    assert at.load("Tray_09") == 3.0 and at.unload("Tray_09") == 4.0   # fallback to defaults


def test_averages_over_machines():
    cfg = {"arm": {"arm_move_time_s": 3.0, "arm_to_tray_time_s": 3.0,
                   "per_machine": {"A": {"load_s": 2.0, "unload_s": 4.0},
                                   "B": {"load_s": 4.0, "unload_s": 2.0}}}}
    assert ArmTimes(cfg).averages(["A", "B"]) == (3.0, 3.0)


def test_missing_arm_block_uses_defaults():
    at = ArmTimes({})
    assert at.load("x") == 3.0 and at.unload("x") == 3.0
