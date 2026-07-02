"""SimDriver — runs the SAME SchedulingPolicy under a VirtualClock for capacity
validation (SPEC §5.4/§5.5). No MQTT: it embeds the machine timing + fault model
and feeds the policy the same events LiveDriver would, so what we validate is the
exact logic that ships.

Per emitted decision it schedules the future observations:
  load  -> arm free (arm_move); then machine done at arm_move+load+proc,
           OR error at a uniform point in-process, then empty after downtime.
  unload-> arm free (arm_move); machine empty after arm_move.
"""

from dataclasses import dataclass
from random import Random

from isaac_common.clock import VirtualClock

from policy import ArmFreed, MachineObserved, ProductArrived, SchedulingPolicy, build_world


@dataclass
class SimReport:
    n_machine: int
    n_arm: int
    total: int
    completed: float
    scrapped: float
    makespan: float
    mean_wait: float
    p95_wait: float
    peak_intake: int
    machine_occupancy: float
    arm_occupancy: float


class SimDriver:
    def __init__(self, policy: SchedulingPolicy, clock: VirtualClock, *,
                 arm_load_s: float, arm_unload_s: float, process_time_s: float,
                 check_in_time_s: float = 0.0, check_out_time_s: float = 0.0,
                 error_prob: float, error_downtime_s: float,
                 arrival_interval_s: float, jitter: str, total: int, rng: Random):
        self.policy = policy
        self.clock = clock
        self.arm_load_s = arm_load_s        # ProductIn -> machine
        self.arm_unload_s = arm_unload_s    # machine -> ProductOut (至盤)
        self.process_time_s = process_time_s
        self.check_in_time_s = check_in_time_s     # Tray 收合
        self.check_out_time_s = check_out_time_s   # Tray 吐出
        self.error_prob = error_prob
        self.error_downtime_s = error_downtime_s
        self.arrival_interval_s = arrival_interval_s
        self.jitter = jitter
        self.total = total
        self.rng = rng
        self._arrival_at: dict[str, float] = {}
        self._waits: list[float] = []
        self._peak_intake = 0
        self._made = 0

    def run(self) -> SimReport:
        self._schedule_arrival()
        makespan = self.clock.run()
        return self._report(makespan)

    # --- arrivals ---
    def _schedule_arrival(self) -> None:
        if self._made >= self.total:
            return
        delay = (self.rng.expovariate(1.0 / self.arrival_interval_s)
                 if self.jitter == "poisson" else self.arrival_interval_s)
        self.clock.call_later(delay, self._arrive)

    def _arrive(self) -> None:
        self._made += 1
        pid = f"P{self._made:06d}"
        self._arrival_at[pid] = self.clock.now()
        self._process(ProductArrived(pid))
        self._schedule_arrival()

    # --- policy plumbing ---
    def _process(self, event) -> None:
        for d in self.policy.handle(event):
            self._apply(d)
        if len(self.policy.intake) > self._peak_intake:
            self._peak_intake = len(self.policy.intake)

    def _apply(self, d) -> None:
        move_s = self.arm_load_s if d.kind == "load" else self.arm_unload_s
        self.clock.call_later(move_s, lambda a=d.arm_id: self._process(ArmFreed(a)))
        if d.kind == "load":
            self._waits.append(self.clock.now() - self._arrival_at.get(d.product_id, self.clock.now()))
            # arm places product, then Tray 收合 (check_in), then working starts
            work_start = self.arm_load_s + self.check_in_time_s
            if self.rng.random() < self.error_prob:
                err_at = work_start + self.rng.uniform(0.0, self.process_time_s)
                self.clock.call_later(err_at, lambda m=d.machine_id, p=d.product_id:
                                      self._process(MachineObserved(m, "error", p)))
                self.clock.call_later(err_at + self.error_downtime_s, lambda m=d.machine_id:
                                      self._process(MachineObserved(m, "empty")))
            else:
                # working -> Tray 吐出 (check_out) -> done
                done_at = work_start + self.process_time_s + self.check_out_time_s
                self.clock.call_later(done_at, lambda m=d.machine_id, p=d.product_id:
                                      self._process(MachineObserved(m, "done", p)))
        else:  # unload
            self.clock.call_later(self.arm_unload_s, lambda m=d.machine_id:
                                  self._process(MachineObserved(m, "empty")))

    # --- metrics ---
    def _report(self, makespan: float) -> SimReport:
        completed = self.policy.metrics["completed"]
        scrapped = self.policy.metrics["scrapped"]
        n_m = len(self.policy.machines)
        n_a = len(self.policy.arms)
        waits = sorted(self._waits)
        mean_wait = sum(waits) / len(waits) if waits else 0.0
        p95 = waits[min(len(waits) - 1, int(0.95 * len(waits)))] if waits else 0.0
        span = makespan or 1.0
        cycle = self.check_in_time_s + self.process_time_s + self.check_out_time_s  # machine busy per good job
        machine_busy = completed * cycle + scrapped * (self.check_in_time_s + self.process_time_s / 2 + self.error_downtime_s)
        # every product gets 1 load; only completed ones also get an unload (scrap auto-empties)
        arm_busy = (completed + scrapped) * self.arm_load_s + completed * self.arm_unload_s
        return SimReport(
            n_machine=n_m, n_arm=n_a, total=self.total,
            completed=completed, scrapped=scrapped, makespan=makespan,
            mean_wait=mean_wait, p95_wait=p95, peak_intake=self._peak_intake,
            machine_occupancy=machine_busy / (n_m * span),
            arm_occupancy=arm_busy / (n_a * span),
        )


def run_one(cfg_proc: dict, n_machine: int, n_arm: int, seed: int) -> SimReport:
    """One simulation run with a partitioned world of n_machine/n_arm."""
    arms, machines = build_world({"machines": {"count": n_machine}, "arms": {"count": n_arm}})
    policy = SchedulingPolicy(arms, machines)
    clock = VirtualClock()
    driver = SimDriver(policy, clock, rng=Random(seed), **cfg_proc)
    return driver.run()
