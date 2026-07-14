"""Capacity planner (b.4 / SPEC §5.5): analytical estimate + simulation validation.

測試機版：working 時間由報告推算（測項數 × row_interval + FAIL 數 × fail_recovery），
不寫死。每顆板子機台佔用 = check_in + working + check_out。

    machine-time/arrival = check_in + working + check_out
    arm-time/arrival     = t_load + t_unload
    N_machine >= ceil(lambda * machine-time/arrival)
    N_arm     >= ceil(lambda * arm-time/arrival)

Then validate by running the real SchedulingPolicy under VirtualClock across seeds;
if mean wait exceeds the target, bump the bottleneck resource and re-run.
"""

import argparse
import math
import os
from pathlib import Path
from statistics import mean

from isaac_common.arm_timing import ArmTimes
from isaac_common.config import load_json, machine_ids
from isaac_common.test_report import report_stats

from sim_driver import SimReport, run_one


def _working_time(cfg: dict) -> float:
    """Machine 'working' time per board = 測項數 × interval + FAIL 數 × recovery，
    由代表性報告（test_data.default）推算——不寫死。"""
    proc = cfg["process"]
    interval = float(proc.get("row_interval_s", 2.0))
    recovery = float(proc.get("fail_recovery_s", 10.0))
    default_file = cfg.get("test_data", {}).get("default")
    if not default_file:
        return float(proc.get("machine_process_time_s", 0.0))   # 舊 config 相容
    rows, fails = report_stats(Path(os.getenv("TEST_DATA_DIR", "/app/data")) / default_file)
    return rows * interval + fails * recovery


def analytical(cfg: dict) -> dict:
    interval = float(cfg["products"]["arrival_interval_s"])
    lam = 1.0 / interval
    t_work = _working_time(cfg)
    t_load, t_unload = ArmTimes(cfg).averages(machine_ids(cfg))  # 決定「幾台」用平均 arm 時間
    cin = float(cfg["process"].get("tray_check_in_time_s", 0.0))
    cout = float(cfg["process"].get("tray_check_out_time_s", 0.0))
    t_machine = cin + t_work + cout            # 一顆板子機台佔用（含收合/吐出/測試/FAIL recovery）
    t_arm = t_load + t_unload
    return {
        "lambda": lam, "t_machine_per_arrival": t_machine, "t_arm_per_arrival": t_arm,
        "t_work": t_work,
        "n_machine": max(1, math.ceil(lam * t_machine)),
        "n_arm": max(1, math.ceil(lam * t_arm)),
        "availability": t_work / t_machine if t_machine else 1.0,
    }


def _proc_params(cfg: dict) -> dict:
    avg_load, avg_unload = ArmTimes(cfg).averages(machine_ids(cfg))  # representative for capacity
    return dict(
        arm_load_s=avg_load,
        arm_unload_s=avg_unload,
        process_time_s=_working_time(cfg),          # 測試總時長（測項+FAIL recovery）
        check_in_time_s=float(cfg["process"].get("tray_check_in_time_s", 0.0)),
        check_out_time_s=float(cfg["process"].get("tray_check_out_time_s", 0.0)),
        error_prob=0.0,                             # 測試機無隨機 error；問題來自資料 FAIL（已含在 process_time）
        error_downtime_s=0.0,
        arrival_interval_s=float(cfg["products"]["arrival_interval_s"]),
        jitter=str(cfg["products"].get("arrival_jitter", "fixed")),
        total=min(int(cfg["products"]["total"]), 200),   # 容量驗證只需取樣，config total 可能極大
    )


def _avg(reports: list[SimReport], n_m: int, n_a: int) -> SimReport:
    f = lambda attr: mean(getattr(r, attr) for r in reports)
    return SimReport(
        n_machine=n_m, n_arm=n_a, total=reports[0].total,
        completed=f("completed"), scrapped=f("scrapped"), makespan=f("makespan"),
        mean_wait=f("mean_wait"), p95_wait=f("p95_wait"),
        peak_intake=max(r.peak_intake for r in reports),
        machine_occupancy=f("machine_occupancy"), arm_occupancy=f("arm_occupancy"),
    )


def validate(cfg: dict, n_machine: int, n_arm: int, seeds: int) -> SimReport:
    proc = _proc_params(cfg)
    return _avg([run_one(proc, n_machine, n_arm, seed) for seed in range(seeds)], n_machine, n_arm)


def plan(cfg: dict, seeds: int = 5, search: bool = True, wait_factor: float = 2.0, max_iter: int = 8):
    a = analytical(cfg)
    t_proc = float(cfg["process"]["machine_process_time_s"])
    target_wait = wait_factor * t_proc
    n_m, n_a = a["n_machine"], a["n_arm"]
    history: list[SimReport] = []
    for _ in range(max_iter):
        rep = validate(cfg, n_m, n_a, seeds)
        history.append(rep)
        if not search or rep.mean_wait <= target_wait:
            break
        if rep.arm_occupancy >= rep.machine_occupancy:   # bump the busier resource
            n_a += 1
        else:
            n_m += 1
    return a, history, target_wait


def _format(cfg: dict, a: dict, history: list[SimReport], target_wait: float) -> str:
    final = history[-1]
    L = []
    L.append("=" * 60)
    L.append("CAPACITY PLAN  (b.4 / SPEC §5.5)")
    L.append("=" * 60)
    L.append(f"inputs: arrival_interval={cfg['products']['arrival_interval_s']}s "
             f"(λ={a['lambda']:.4f}/s), process={cfg['process']['machine_process_time_s']}s, "
             f"arm_move={cfg['arm']['arm_move_time_s']}s, err_p={cfg['error']['error_prob_per_job']}, "
             f"downtime={cfg['error']['error_downtime_s']}s, products={cfg['products']['total']}")
    L.append("")
    L.append("ANALYTICAL ESTIMATE")
    L.append(f"  machine-time/arrival = {a['t_machine_per_arrival']:.2f}s  -> N_machine >= {a['n_machine']}")
    L.append(f"  arm-time/arrival     = {a['t_arm_per_arrival']:.2f}s  -> N_arm     >= {a['n_arm']}")
    L.append(f"  machine availability A = {a['availability']:.3f}")
    L.append("")
    L.append(f"SIMULATION VALIDATION (same policy, VirtualClock; target mean wait <= {target_wait:.0f}s)")
    for rep in history:
        ok = "OK " if rep.mean_wait <= target_wait else "BUSY"
        L.append(f"  [{ok}] machines={rep.n_machine} arms={rep.n_arm}: "
                 f"completed={rep.completed:.0f} scrapped={rep.scrapped:.0f} "
                 f"mean_wait={rep.mean_wait:.1f}s p95={rep.p95_wait:.1f}s peak_queue={rep.peak_intake} "
                 f"makespan={rep.makespan:.0f}s mach_occ={rep.machine_occupancy:.0%} arm_occ={rep.arm_occupancy:.0%}")
    L.append("")
    L.append(f"RECOMMENDATION: {final.n_machine} machines + {final.n_arm} arms")
    L.append(f"  expected: mean wait {final.mean_wait:.1f}s, machine occupancy {final.machine_occupancy:.0%}, "
             f"arm occupancy {final.arm_occupancy:.0%}, scrap {final.scrapped:.0f}/{final.total}")
    L.append("=" * 60)
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="Capacity planner for the Isaac Sim scheduling sim.")
    ap.add_argument("--config", default="/app/config/simulation.json")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--no-search", action="store_true", help="report analytical config only, no bump search")
    ap.add_argument("--wait-factor", type=float, default=2.0, help="target mean wait = factor * process_time")
    args = ap.parse_args()

    cfg = load_json(args.config)
    a, history, target = plan(cfg, seeds=args.seeds, search=not args.no_search, wait_factor=args.wait_factor)
    print(_format(cfg, a, history, target))


if __name__ == "__main__":
    main()
