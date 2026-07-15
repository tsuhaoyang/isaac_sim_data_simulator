from random import Random

from isaac_common.test_report import TestItem, path_to_tree

from machine import Machine, MachineConfig


def rows(*specs):
    out = []
    for i, r in enumerate(specs, 1):
        if r == "FAIL":
            p = ["BoardX.J1.A1", "BoardX.R5.2", "BoardY.J7.B3"]
            out.append(TestItem("B", f"net{i}", "FAIL", "Power", p, path_to_tree(p)))
        else:
            out.append(TestItem("B", f"net{i}", "PASS"))
    return out


def _drive(cfg, t_end, dt=0.1, load=None):
    m = Machine("Tray_00", cfg, Random(0), now=0.0, next_product_id=lambda: "PX")
    if load:
        m.request_load(load)
    states, tests = [], []   # tests = (t, ev)
    t = 0.0
    while t <= t_end:
        o = m.tick(round(t, 3))
        states += [e.state.value for e in o.transitions]
        tests += [(round(t, 3), ev) for ev in o.test_items]
        t += dt
    return states, tests, m


def test_full_cycle_streams_all_items():
    cfg = MachineConfig(test_rows=rows("PASS", "PASS", "PASS"), row_interval_s=0.5, fail_recovery_s=5.0,
                        check_in_time_s=0.0, check_out_time_s=0.0,
                        autonomous=True, idle_before_load_s=0.3, done_hold_s=0.3)
    states, tests, _ = _drive(cfg, t_end=4.0)
    assert states[:5] == ["check_in", "working", "check_out", "done", "empty"]
    # 3 個測項全串出（第一輪）
    assert [ev.result for _, ev in tests][:3] == ["PASS", "PASS", "PASS"]
    assert [ev.index for _, ev in tests][:3] == [1, 2, 3]
    assert tests[0][1].total == 3


def test_fail_adds_recovery_and_carries_path():
    cfg = MachineConfig(test_rows=rows("PASS", "FAIL", "PASS"), row_interval_s=0.5, fail_recovery_s=3.0,
                        check_in_time_s=0.0, check_out_time_s=0.0, autonomous=False)
    states, tests, _ = _drive(cfg, t_end=10.0, load="P1")
    by_idx = {ev.index: (t, ev) for t, ev in tests}
    assert {1, 2, 3} <= set(by_idx)
    # row2 是 FAIL → 進 error 復原 → 回 working，row3 應在 ~fail_recovery(3.0) 後才出現
    assert by_idx[3][0] - by_idx[2][0] >= 2.9
    # FAIL 事件帶解析後 path + 階層 path_tree + fault，PASS 不帶
    fail_ev = by_idx[2][1]
    assert fail_ev.result == "FAIL" and fail_ev.fault == "Power"
    assert fail_ev.path == ["BoardX.J1.A1", "BoardX.R5.2", "BoardY.J7.B3"]
    assert [(s.board, s.nodes) for s in fail_ev.path_tree] == [
        ("BoardX", ["J1.A1", "R5.2"]), ("BoardY", ["J7.B3"]),
    ]
    assert by_idx[1][1].path == [] and by_idx[1][1].path_tree == []
    # FAIL 有把 state 送 error，復原後回 working，最後仍 done（不中止整板）
    assert "error" in states
    assert states.index("error") < states.index("done")
    assert "done" in states


def test_reset_returns_to_empty():
    cfg = MachineConfig(test_rows=rows("PASS", "PASS"), row_interval_s=0.5, autonomous=False)
    m = Machine("Tray_00", cfg, Random(0), now=0.0, next_product_id=lambda: "P")
    m.request_load("P1")
    m.tick(0.1)   # check_in
    m.tick(0.2)   # working
    assert m.state.value in ("check_in", "working") and m.product_id == "P1"
    m.reset(5.0)
    assert m.state.value == "empty" and m.product_id is None
    assert m.tick(6.0).transitions == []


def test_driven_mode_waits_for_triggers():
    cfg = MachineConfig(test_rows=rows("PASS"), row_interval_s=0.5, check_in_time_s=0.0,
                        check_out_time_s=0.0, autonomous=False)
    m = Machine("Tray_00", cfg, Random(0), now=0.0, next_product_id=lambda: "PX")
    for t in (0.0, 1.0, 2.0):
        assert m.tick(t).transitions == []
    m.request_load("P777")
    assert m.tick(3.0).transitions[0].state.value == "check_in"
    m.tick(3.1)                                # -> working
    assert m.tick(3.7).test_items[0].result == "PASS"   # 唯一測項串出 (working+0.5)
    out = m.tick(4.3)                            # 全測完 -> check_out
    assert out.transitions[-1].state.value == "check_out"
