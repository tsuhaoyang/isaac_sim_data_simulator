import math

from isaac_common.clock import VirtualClock

from capacity import analytical, validate
from sim_driver import run_one

BASE_CFG = {
    "machines": {"count": 6},
    "arms": {"count": 2},
    "products": {"total": 30, "arrival_interval_s": 4.0, "arrival_jitter": "fixed"},
    "process": {"machine_process_time_s": 20.0, "machine_load_time_s": 0.0},
    "arm": {"arm_move_time_s": 3.0, "arm_to_tray_time_s": 3.0},
    "error": {"error_prob_per_job": 0.0, "error_downtime_s": 30.0},
}


def test_virtualclock_orders_events_by_time():
    clk = VirtualClock()
    seen = []
    clk.call_later(2.0, lambda: seen.append("b"))
    clk.call_later(1.0, lambda: seen.append("a"))
    clk.call_later(1.0, lambda: seen.append("a2"))  # same time -> FIFO
    end = clk.run()
    assert seen == ["a", "a2", "b"]
    assert end == 2.0


def test_analytical_reduces_to_spec_when_no_errors():
    a = analytical(BASE_CFG)
    lam = 1 / 4.0
    assert a["n_machine"] == math.ceil(lam * 20.0)   # SPEC: lambda * t_proc
    assert a["n_arm"] == math.ceil(lam * 2 * 3.0)     # SPEC: lambda * 2 * t_move
    assert a["availability"] == 1.0


def test_analytical_uses_separate_load_unload_times():
    cfg = dict(BASE_CFG)
    cfg["arm"] = {"arm_move_time_s": 3.0, "arm_to_tray_time_s": 5.0}  # load != unload
    a = analytical(cfg)
    assert abs(a["t_arm_per_arrival"] - (3.0 + 5.0)) < 1e-9  # p=0 -> t_load + t_unload


def test_sim_consumes_all_products_when_provisioned():
    # plenty of capacity, no errors -> everything completes, queue stays tiny
    rep = run_one(
        dict(arm_load_s=3.0, arm_unload_s=3.0, process_time_s=20.0, load_time_s=0.0, error_prob=0.0,
             error_downtime_s=30.0, arrival_interval_s=4.0, jitter="fixed", total=30),
        n_machine=6, n_arm=2, seed=1,
    )
    assert rep.completed == 30
    assert rep.scrapped == 0
    assert rep.mean_wait < 20.0
    assert rep.makespan > 0


def test_sim_is_deterministic_with_seed():
    proc = dict(arm_load_s=3.0, arm_unload_s=3.0, process_time_s=20.0, load_time_s=0.0, error_prob=0.3,
                error_downtime_s=30.0, arrival_interval_s=4.0, jitter="poisson", total=30)
    r1 = run_one(proc, 6, 2, seed=42)
    r2 = run_one(proc, 6, 2, seed=42)
    assert (r1.completed, r1.scrapped, r1.makespan) == (r2.completed, r2.scrapped, r2.makespan)


def test_undercapacity_builds_a_large_queue():
    # one machine vs fast arrivals -> queue explodes, long waits
    rep = run_one(
        dict(arm_load_s=3.0, arm_unload_s=3.0, process_time_s=20.0, load_time_s=0.0, error_prob=0.0,
             error_downtime_s=30.0, arrival_interval_s=2.0, jitter="fixed", total=30),
        n_machine=1, n_arm=1, seed=1,
    )
    assert rep.peak_intake > 10
    assert rep.mean_wait > 50.0


def test_validate_search_recommends_feasible_config():
    # start from an under-provisioned guess; search must grow to a feasible plan
    rep = validate(BASE_CFG, n_machine=6, n_arm=2, seeds=3)
    assert rep.completed == 30
    assert rep.mean_wait < 40.0
