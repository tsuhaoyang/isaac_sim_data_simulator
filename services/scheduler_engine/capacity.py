"""Capacity planner (b.4 / SPEC §5.5): analytical estimate + simulation validation.

analytical (per arrival, p=error_prob, D=downtime):
    machine-time/arrival = (1-p)*t_proc + p*(t_proc/2) + p*D
    arm-time/arrival     = t_load + (1-p)*t_unload   # always 1 load; unload only if completed
    N_machine >= ceil(lambda * machine-time/arrival)
    N_arm     >= ceil(lambda * arm-time/arrival)
(t_load=arm_move_time_s ProductIn->machine; t_unload=arm_to_tray_time_s machine->ProductOut.
 p=0 & t_load==t_unload reduces to SPEC's N_machine>=lambda*t_proc, N_arm>=lambda*2*t_move.)

Then validate by running the real SchedulingPolicy under VirtualClock across seeds;
if mean wait exceeds the target, bump the bottleneck resource and re-run.
"""

import argparse
import math
from statistics import mean

from isaac_common.config import load_json

from sim_driver import SimReport, run_one


def analytical(cfg: dict) -> dict:
    interval = float(cfg["products"]["arrival_interval_s"])
    lam = 1.0 / interval
    t_proc = float(cfg["process"]["machine_process_time_s"])
    t_load = float(cfg["arm"]["arm_move_time_s"])
    t_unload = float(cfg["arm"].get("arm_to_tray_time_s", t_load))
    p = float(cfg["error"]["error_prob_per_job"])
    d = float(cfg["error"]["error_downtime_s"])

    t_machine = (1 - p) * t_proc + p * (t_proc / 2) + p * d
    t_arm = t_load + (1 - p) * t_unload
    return {
        "lambda": lam, "t_machine_per_arrival": t_machine, "t_arm_per_arrival": t_arm,
        "n_machine": max(1, math.ceil(lam * t_machine)),
        "n_arm": max(1, math.ceil(lam * t_arm)),
        "availability": (1 - p) * t_proc / t_machine if t_machine else 1.0,
    }


def _proc_params(cfg: dict) -> dict:
    return dict(
        arm_load_s=float(cfg["arm"]["arm_move_time_s"]),
        arm_unload_s=float(cfg["arm"].get("arm_to_tray_time_s", cfg["arm"]["arm_move_time_s"])),
        process_time_s=float(cfg["process"]["machine_process_time_s"]),
        load_time_s=float(cfg["process"].get("machine_load_time_s", 0.0)),
        error_prob=float(cfg["error"]["error_prob_per_job"]),
        error_downtime_s=float(cfg["error"]["error_downtime_s"]),
        arrival_interval_s=float(cfg["products"]["arrival_interval_s"]),
        jitter=str(cfg["products"].get("arrival_jitter", "fixed")),
        total=int(cfg["products"]["total"]),
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
