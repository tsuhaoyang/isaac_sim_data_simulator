from random import Random

from machine import Machine, MachineConfig


def _run(cfg, *, seed=0, t_end=8.0, dt=0.1):
    """Drive one machine deterministically; return the ordered transition states."""
    rng = Random(seed)
    ids = iter(f"P{i:03d}" for i in range(1, 999))
    m = Machine("M01", cfg, rng, now=0.0, next_product_id=lambda: next(ids))
    states = []
    t = 0.0
    while t <= t_end:
        for ev in m.tick(t).transitions:
            states.append(ev.state.value)
        t += dt
    return states, m


def test_full_cycle_no_error():
    cfg = MachineConfig(
        process_time_s=2.0, check_in_time_s=0.0, check_out_time_s=0.0, error_prob_per_job=0.0,
        telemetry_interval_s=0.5, autonomous=True, idle_before_load_s=0.5, done_hold_s=0.5,
    )
    states, _ = _run(cfg, t_end=6.0)
    assert states[:5] == ["check_in", "working", "check_out", "done", "empty"]
    # autonomous mode keeps cycling
    assert states.count("working") >= 2


def test_check_in_out_durations():
    cfg = MachineConfig(
        process_time_s=2.0, check_in_time_s=1.0, check_out_time_s=1.0, error_prob_per_job=0.0,
        autonomous=False,
    )
    m = Machine("M01", cfg, Random(0), now=0.0, next_product_id=lambda: "P")
    m.request_load("P1")
    assert m.tick(0.0).transitions[0].state.value == "check_in"      # t0
    assert m.tick(0.9).transitions == []                             # still 收合 (<1s)
    assert m.tick(1.0).transitions[0].state.value == "working"       # 收合 1s done
    assert m.tick(2.9).transitions == []                             # still working (<2s)
    assert m.tick(3.0).transitions[0].state.value == "check_out"     # process 2s done -> 吐出
    assert m.tick(3.9).transitions == []                             # still 吐出 (<1s)
    assert m.tick(4.0).transitions[0].state.value == "done"          # 吐出 1s done


def test_error_scraps_product_and_recovers():
    cfg = MachineConfig(
        process_time_s=2.0, check_in_time_s=0.0, error_prob_per_job=1.0,  # always fault
        error_downtime_s=1.0, autonomous=True, idle_before_load_s=0.2, done_hold_s=0.2,
    )
    states, m = _run(cfg, seed=1, t_end=5.0)
    assert "error" in states
    i = states.index("error")
    assert "empty" in states[i + 1:]              # recovers to empty after downtime
    assert "done" not in states                   # faulted jobs never reach check_out/done


def test_reset_returns_to_empty():
    cfg = MachineConfig(process_time_s=2.0, check_in_time_s=0.0, error_prob_per_job=0.0, autonomous=False)
    m = Machine("Tray_00", cfg, Random(0), now=0.0, next_product_id=lambda: "P")
    m.request_load("P1")
    m.tick(0.1)        # -> check_in
    m.tick(0.2)        # -> working
    assert m.state.value in ("check_in", "working") and m.product_id == "P1"
    m.reset(5.0)
    assert m.state.value == "empty" and m.product_id is None
    assert m.tick(6.0).transitions == []   # idle (driven, no pending)


def test_driven_mode_waits_for_triggers():
    cfg = MachineConfig(process_time_s=1.0, check_in_time_s=0.0, check_out_time_s=0.0,
                        error_prob_per_job=0.0, autonomous=False)
    m = Machine("M01", cfg, Random(0), now=0.0, next_product_id=lambda: "PX")

    for t in (0.0, 1.0, 2.0):                      # idle: no autonomous loading
        assert m.tick(t).transitions == []

    m.request_load("P777")
    assert m.tick(3.0).transitions[0].state.value == "check_in"
    m.tick(3.1)                                    # check_in -> working
    assert m.tick(4.2).transitions[-1].state.value == "check_out"   # process done -> 吐出
    assert m.tick(4.3).transitions[-1].state.value == "done"        # 吐出 done
    assert m.tick(4.4).transitions == []           # stays done until unload
    m.request_unload()
    assert m.tick(4.5).transitions[0].state.value == "empty"
