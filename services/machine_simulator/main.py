"""machine_simulator (= 假資料來源 / 機台狀態機).

M1: drive N machine state machines in real time, publish state + telemetry.
Default autonomous mode keeps machines cycling (滿足 a.1「一直發」); driven mode
reacts to arm load/unload commands (used from M2).
"""

import os
import signal
import threading
import time
from random import Random

from pathlib import Path

from isaac_common import topics
from isaac_common.arm_timing import ArmTimes
from isaac_common.config import load_json, machine_ids as machine_ids_from_cfg
from isaac_common.logging import get_logger
from isaac_common.mqtt_client import MqttClient
from isaac_common.schemas import IsaacSimCommand, MachineState, MachineStateEvent
from isaac_common.settings import Settings
from isaac_common.test_report import parse_report

from machine import Machine, MachineConfig

SERVICE = "machine_simulator"


def main() -> None:
    settings = Settings.from_env(SERVICE)
    log = get_logger(SERVICE)
    cfg = load_json(settings.config_path)

    machine_ids = machine_ids_from_cfg(cfg)
    arm_times = ArmTimes(cfg)               # per-machine load/unload delays
    mode = os.getenv("MACHINE_MODE", "autonomous").lower()
    tick_s = float(os.getenv("TICK_INTERVAL_S", "0.25"))
    seed_env = os.getenv("SIM_SEED")
    rng = Random(int(seed_env)) if seed_env else Random()

    # --- test report data: each machine streams its report's rows as test items ---
    data_dir = Path(os.getenv("TEST_DATA_DIR", "/app/data"))
    td = cfg.get("test_data", {})
    default_file = td.get("default")
    per_machine_file = td.get("per_machine", {})
    _report_cache: dict[str, list] = {}
    def load_rows(mid: str) -> list:
        fname = per_machine_file.get(mid, default_file)
        if not fname:
            return []
        if fname not in _report_cache:
            _report_cache[fname] = parse_report(data_dir / fname)
        return _report_cache[fname]

    proc = cfg.get("process", {})

    def make_cfg(mid: str) -> MachineConfig:
        return MachineConfig(
            test_rows=load_rows(mid),
            row_interval_s=float(proc.get("row_interval_s", 2.0)),
            fail_recovery_s=float(proc.get("fail_recovery_s", 10.0)),
            check_in_time_s=float(proc.get("tray_check_in_time_s", 0.0)),
            check_out_time_s=float(proc.get("tray_check_out_time_s", 0.0)),
            telemetry_interval_s=float(os.getenv("TELEMETRY_INTERVAL_S", "1.0")),
            autonomous=(mode == "autonomous"),
            idle_before_load_s=float(os.getenv("MACHINE_IDLE_BEFORE_LOAD_S", "1.0")),
            done_hold_s=float(os.getenv("MACHINE_DONE_HOLD_S", "1.0")),
        )

    _counter = {"n": 0}
    def next_product_id() -> str:
        _counter["n"] += 1
        return f"P{_counter['n']:06d}"

    now0 = time.monotonic()
    machines = {mid: Machine(mid, make_cfg(mid), rng, now0, next_product_id) for mid in machine_ids}

    client = MqttClient(settings.mqtt_host, settings.mqtt_port, SERVICE, log)

    # driven mode: react to arm commands (applied after the nominal arm move time)
    pending: list[tuple[float, str, str, str | None]] = []  # (apply_at, machine_id, kind, product)
    pending_lock = threading.Lock()

    def on_arm_command(topic: str, payload) -> None:
        # the plant reacts to the physical arm motion (same command Isaac Sim gets):
        # placing onto a machine = load; picking from a machine = unload.
        try:
            cmd = IsaacSimCommand.model_validate(payload)
        except Exception:
            log.warning("bad arm command on %s: %s", topic, payload)
            return
        now = time.monotonic()
        with pending_lock:
            if cmd.place.key in machines:  # placing onto a machine = load
                pending.append((now + arm_times.load(cmd.place.key), cmd.place.key, "load", cmd.product_id))
            elif cmd.pick.key in machines:  # picking from a machine = unload (至盤)
                pending.append((now + arm_times.unload(cmd.pick.key), cmd.pick.key, "unload", None))

    reset_flag = threading.Event()

    def on_control(topic: str, payload) -> None:
        if isinstance(payload, dict) and payload.get("cmd") == "start":
            reset_flag.set()  # performed in the tick loop (single-threaded machine state)

    if mode == "driven":
        client.subscribe(topics.isaacsim_arm_filter(), on_arm_command)
    client.subscribe(topics.SIM_CONTROL, on_control)

    client.connect()
    # seed retained empty state so collector/scheduler start in sync
    for mid in machines:
        client.publish_json(topics.machine_state(mid),
                            MachineStateEvent(machine_id=mid, state=MachineState.EMPTY), retain=True)
    for mid in machine_ids:
        r = machines[mid].cfg.test_rows
        fails = sum(1 for it in r if it.result == "FAIL")
        log.info("  %s: %d 測項 (%d FAIL) file=%s", mid, len(r), fails,
                 per_machine_file.get(mid, default_file))
    log.info("machine_simulator up: %d machines, mode=%s, row_interval=%.1fs, fail_recovery=%.1fs",
             len(machine_ids), mode, float(proc.get("row_interval_s", 2.0)),
             float(proc.get("fail_recovery_s", 10.0)))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        now = time.monotonic()

        # run reset (sim/control start): clear all machines + queues back to empty
        if reset_flag.is_set():
            reset_flag.clear()
            with pending_lock:
                pending.clear()
            _counter["n"] = 0
            for m in machines.values():
                m.reset(now)
                client.publish_json(topics.machine_state(m.id),
                                    MachineStateEvent(machine_id=m.id, state=MachineState.EMPTY), retain=True)
            log.info("=== machines reset for new run ===")

        # apply due arm-driven triggers
        if pending:
            with pending_lock:
                due = [p for p in pending if p[0] <= now]
                pending[:] = [p for p in pending if p[0] > now]
            for _, mid, kind, product in due:
                if kind == "load":
                    machines[mid].request_load(product or next_product_id())
                else:
                    machines[mid].request_unload()

        for m in machines.values():
            out = m.tick(now)
            for ev in out.transitions:
                client.publish_json(topics.machine_state(m.id), ev, retain=True)
                log.info("%s -> %-9s product=%s", m.id, ev.state.value, ev.product_id)
            if out.telemetry is not None:
                client.publish_json(topics.machine_telemetry(m.id), out.telemetry)
            for ti in out.test_items:                       # 逐筆測項 -> plant/machine/{id}/test
                client.publish_json(topics.machine_test(m.id), ti)
                if ti.result == "FAIL":
                    log.info("%s test #%d/%d %s FAIL(%s) path[%d]",
                             m.id, ti.index, ti.total, ti.item, ti.fault, len(ti.path))

        stop.wait(tick_s)

    log.info("machine_simulator shutting down")
    client.disconnect()


if __name__ == "__main__":
    main()
