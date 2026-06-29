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

from isaac_common import topics
from isaac_common.config import load_json
from isaac_common.logging import get_logger
from isaac_common.mqtt_client import MqttClient
from isaac_common.schemas import IsaacSimCommand, MachineState, MachineStateEvent
from isaac_common.settings import Settings

from machine import Machine, MachineConfig

SERVICE = "machine_simulator"


def _machine_ids(cfg: dict) -> list[str]:
    """Machine names from config — same source as the scheduler's world model.
    Uses reachability_matrix values if present, else M01..M{count}."""
    matrix = cfg.get("reachability_matrix")
    if matrix:
        return sorted({m for ms in matrix.values() for m in ms})
    return [f"M{i:02d}" for i in range(1, int(cfg["machines"]["count"]) + 1)]


def main() -> None:
    settings = Settings.from_env(SERVICE)
    log = get_logger(SERVICE)
    cfg = load_json(settings.config_path)

    machine_ids = _machine_ids(cfg)
    mode = os.getenv("MACHINE_MODE", "autonomous").lower()
    tick_s = float(os.getenv("TICK_INTERVAL_S", "0.25"))
    arm_load_s = float(cfg.get("arm", {}).get("arm_move_time_s", 3.0))      # ProductIn -> machine
    arm_unload_s = float(cfg.get("arm", {}).get("arm_to_tray_time_s", arm_load_s))  # machine -> tray
    seed_env = os.getenv("SIM_SEED")
    rng = Random(int(seed_env)) if seed_env else Random()

    mcfg = MachineConfig(
        process_time_s=float(cfg["process"]["machine_process_time_s"]),
        load_time_s=float(cfg["process"].get("machine_load_time_s", 0.0)),
        error_prob_per_job=float(cfg["error"]["error_prob_per_job"]),
        error_downtime_s=float(cfg["error"]["error_downtime_s"]),
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
    machines = {
        mid: Machine(mid, mcfg, rng, now0, next_product_id) for mid in machine_ids
    }

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
                pending.append((now + arm_load_s, cmd.place.key, "load", cmd.product_id))
            elif cmd.pick.key in machines:  # picking from a machine = unload (至盤)
                pending.append((now + arm_unload_s, cmd.pick.key, "unload", None))

    if mode == "driven":
        client.subscribe(topics.isaacsim_arm_filter(), on_arm_command)

    client.connect()
    # seed retained empty state so collector/scheduler start in sync
    for mid in machines:
        client.publish_json(topics.machine_state(mid),
                            MachineStateEvent(machine_id=mid, state=MachineState.EMPTY), retain=True)
    log.info("machine_simulator up: %d machines %s, mode=%s, process=%.1fs, err_p=%.2f",
             len(machine_ids), machine_ids, mode, mcfg.process_time_s, mcfg.error_prob_per_job)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        now = time.monotonic()

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
                log.info("%s -> %-7s product=%s", m.id, ev.state.value, ev.product_id)
            if out.telemetry is not None:
                client.publish_json(topics.machine_telemetry(m.id), out.telemetry)

        stop.wait(tick_s)

    log.info("machine_simulator shutting down")
    client.disconnect()


if __name__ == "__main__":
    main()
