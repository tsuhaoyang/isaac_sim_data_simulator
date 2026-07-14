from policy import (
    ArmFreed,
    ArmW,
    MachineObserved,
    MachineW,
    MStatus,
    ProductArrived,
    SchedulingPolicy,
    build_world,
)


def _policy(reachable=("M01", "M02")):
    arms = {"A1": ArmW("A1", list(reachable))}
    machines = {m: MachineW(m) for m in reachable}
    return SchedulingPolicy(arms, machines)


def test_arrival_triggers_load_and_reserves():
    p = _policy()
    decs = p.handle(ProductArrived("P1"))
    assert len(decs) == 1
    d = decs[0]
    assert d.kind == "load" and d.product_id == "P1" and d.frm == "ProductIn"
    assert p.machines[d.machine_id].status == MStatus.BUSY
    assert p.arms["A1"].busy is True
    # busy arm does nothing on a second arrival
    assert p.handle(ProductArrived("P2")) == []
    assert list(p.intake) == ["P2"]


def test_load_prefers_fastest_free_machine():
    arms = {"A1": ArmW("A1", ["M01", "M02", "M03"])}
    machines = {m: MachineW(m) for m in ["M01", "M02", "M03"]}
    load_s = {"M01": 5.0, "M02": 2.0, "M03": 4.0}
    p = SchedulingPolicy(arms, machines, load_time=lambda mid: load_s[mid])
    assert p.handle(ProductArrived("P1"))[0].machine_id == "M02"   # fastest first
    p.handle(ProductArrived("P2"))                # queued (arm busy)
    d2 = p.handle(ArmFreed("A1"))[0]               # arm free -> next fastest free machine
    assert d2.machine_id == "M03"                  # M03 (4.0) beats M01 (5.0)


def test_fifo_order_across_arm_frees():
    p = _policy()
    first = p.handle(ProductArrived("P1"))[0]
    p.handle(ProductArrived("P2"))           # queued, arm busy
    second = p.handle(ArmFreed("A1"))[0]      # arm free -> pull next FIFO head
    assert first.product_id == "P1"
    assert second.product_id == "P2"
    assert first.machine_id != second.machine_id


def test_unload_has_priority_over_load():
    p = _policy()
    # M01 finished holding P1; M02 free; a product waiting; arm idle
    p.machines["M01"].status = MStatus.DONE
    p.machines["M01"].product = "P1"
    p.intake.append("P2")
    decs = p._try_act()
    assert len(decs) == 1
    assert decs[0].kind == "unload" and decs[0].machine_id == "M01"
    assert p.machines["M01"].status == MStatus.UNLOADING
    assert p.metrics["completed"] == 1


def test_error_is_transient_not_scrapped():
    # 工作中的 error = 測試 FAIL 復原中，機台仍忙、不報廢、繼續等 done
    p = _policy()
    p.handle(ProductArrived("P1"))            # loads onto a machine
    mid = next(m.id for m in p.machines.values() if m.status == MStatus.BUSY)
    p.handle(MachineObserved(mid, "error", "P1"))
    assert p.metrics["scrapped"] == 0
    assert p.machines[mid].status == MStatus.BUSY   # 仍保留、不釋放
    p.handle(MachineObserved(mid, "working", "P1"))  # 復原回 working
    assert p.machines[mid].status == MStatus.BUSY
    p.handle(MachineObserved(mid, "done", "P1"))     # 測完
    assert p.machines[mid].status == MStatus.DONE


def test_full_cycle_completes():
    p = _policy()
    load = p.handle(ProductArrived("P1"))[0]
    mid = load.machine_id
    p.handle(ArmFreed("A1"))                   # arm free
    p.handle(MachineObserved(mid, "working", "P1"))   # ignored
    unload = p.handle(MachineObserved(mid, "done", "P1"))[0]
    assert unload.kind == "unload" and unload.machine_id == mid
    p.handle(ArmFreed("A1"))
    p.handle(MachineObserved(mid, "empty"))
    assert p.machines[mid].status == MStatus.FREE
    assert p.metrics["completed"] == 1


def test_reset_clears_world():
    p = _policy()
    p.handle(ProductArrived("P1"))      # load -> machine busy, arm busy
    p.intake.append("P2")               # something queued
    p.reset()
    assert all(m.status == MStatus.FREE and m.product is None for m in p.machines.values())
    assert all(not a.busy for a in p.arms.values())
    assert len(p.intake) == 0
    assert p.metrics == {"arrivals": 0, "completed": 0, "scrapped": 0}
    # after reset a new arrival schedules cleanly again
    assert p.handle(ProductArrived("P9"))[0].kind == "load"


def test_build_world_partitions_machines():
    arms, machines = build_world({"machines": {"count": 6}, "arms": {"count": 2}})
    assert len(machines) == 6
    assert set(arms) == {"A1", "A2"}
    # round-robin partition, disjoint, covers all machines
    all_reach = arms["A1"].reachable + arms["A2"].reachable
    assert sorted(all_reach) == sorted(machines)
    assert not set(arms["A1"].reachable) & set(arms["A2"].reachable)


def test_build_world_uses_matrix():
    cfg = {"reachability_matrix": {"A1": ["M01", "M02"], "A2": ["M03"]}}
    arms, machines = build_world(cfg)
    assert arms["A1"].reachable == ["M01", "M02"]
    assert set(machines) == {"M01", "M02", "M03"}
