"""scheduler_engine (排程 + 容量試算). M2: pull-based policy + LiveDriver."""

import os
import signal
import threading
from random import Random

from isaac_common import topics
from isaac_common.clock import RealClock
from isaac_common.config import load_json
from isaac_common.logging import get_logger
from isaac_common.mqtt_client import MqttClient
from isaac_common.settings import Settings

from live_driver import LiveDriver
from policy import SchedulingPolicy, build_world

SERVICE = "scheduler_engine"


def main() -> None:
    settings = Settings.from_env(SERVICE)
    log = get_logger(SERVICE)
    cfg = load_json(settings.config_path)

    arms, machines = build_world(cfg)
    policy = SchedulingPolicy(arms, machines)
    seed_env = os.getenv("SIM_SEED")
    rng = Random(int(seed_env)) if seed_env else Random()

    client = MqttClient(settings.mqtt_host, settings.mqtt_port, SERVICE, log)
    driver = LiveDriver(
        policy, client, RealClock(),
        arm_load_s=float(cfg["arm"]["arm_move_time_s"]),
        arm_unload_s=float(cfg["arm"].get("arm_to_tray_time_s", cfg["arm"]["arm_move_time_s"])),
        arrival_interval_s=float(cfg["products"]["arrival_interval_s"]),
        jitter=str(cfg["products"].get("arrival_jitter", "fixed")),
        total_products=int(cfg["products"]["total"]),
        rng=rng, log=log,
    )

    client.connect()
    driver.start()
    log.info("scheduler up: %d arms, %d machines, %d products @ %.1fs",
             len(arms), len(machines), cfg["products"]["total"],
             cfg["products"]["arrival_interval_s"])
    for aid, arm in sorted(arms.items()):
        log.info("  cell %s -> %s", aid, arm.reachable)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        stop.wait(5.0)
        m = policy.metrics
        client.publish_json(topics.SCHEDULER_METRICS, {**m, "intake": len(policy.intake)})
        log.info("metrics: arrivals=%d completed=%d scrapped=%d intake=%d",
                 m["arrivals"], m["completed"], m["scrapped"], len(policy.intake))

    log.info("scheduler shutting down")
    client.disconnect()


if __name__ == "__main__":
    main()
