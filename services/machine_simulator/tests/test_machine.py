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
        process_time_s=2.0, load_time_s=0.0, error_prob_per_job=0.0,
        telemetry_interval_s=0.5, autonomous=True, idle_before_load_s=0.5, done_hold_s=0.5,
    )
    states, _ = _run(cfg, t_end=6.0)
    assert states[:4] == ["start", "working", "done", "empty"]
    # autonomous mode keeps cycling
    assert states.count("working") >= 2


def test_error_scraps_product_and_recovers():
    cfg = MachineConfig(
        process_time_s=2.0, load_time_s=0.0, error_prob_per_job=1.0,  # always fault
        error_downtime_s=1.0, autonomous=True, idle_before_load_s=0.2, done_hold_s=0.2,
    )
    states, m = _run(cfg, seed=1, t_end=5.0)
    assert "error" in states
    # error must be followed by recovery to empty (downtime over)
    i = states.index("error")
    assert "empty" in states[i + 1:]
    # no "done" should occur for a job that always faults before its first completion
    assert states.index("error") < (states.index("done") if "done" in states else 10**9)


def test_reset_returns_to_empty():
    cfg = MachineConfig(process_time_s=2.0, load_time_s=0.0, error_prob_per_job=0.0, autonomous=False)
    m = Machine("Tray_00", cfg, Random(0), now=0.0, next_product_id=lambda: "P")
    m.request_load("P1")
    m.tick(0.1)        # -> start
    m.tick(0.2)        # -> working
    assert m.state.value in ("start", "working") and m.product_id == "P1"
    m.reset(5.0)
    assert m.state.value == "empty" and m.product_id is None
    assert m.tick(6.0).transitions == []   # idle (driven, no pending)


def test_driven_mode_waits_for_triggers():
    cfg = MachineConfig(process_time_s=1.0, load_time_s=0.0, error_prob_per_job=0.0, autonomous=False)
    rng = Random(0)
    m = Machine("M01", cfg, rng, now=0.0, next_product_id=lambda: "PX")

    # idle: no autonomous loading
    for t in (0.0, 1.0, 2.0):
        assert m.tick(t).transitions == []

    m.request_load("P777")
    assert m.tick(3.0).transitions[0].state.value == "start"
    m.tick(3.1)  # start -> working (load_time 0)
    out = m.tick(4.2)  # process done
    assert out.transitions[-1].state.value == "done"
    assert m.tick(4.3).transitions == []   # stays done until unload
    m.request_unload()
    assert m.tick(4.4).transitions[0].state.value == "empty"
