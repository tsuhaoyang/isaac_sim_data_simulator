"""data_collector (擷取 / 正規化 / 狀態紀錄表).

Sources (SPEC §2.2): MQTT (fake machines + real machines on the broker) and a TCP
socket gateway (real machines, M4). Both feed ONE ingestion path -> persist to the
event log + snapshot and republish normalized retained state for the scheduler, so
fake vs real is indistinguishable downstream.
"""

import os
import signal
import threading
from pathlib import Path

from isaac_common import topics
from isaac_common.event_sink import make_sink
from isaac_common.logging import get_logger
from isaac_common.mqtt_client import MqttClient
from isaac_common.schemas import ArmCommand, EventRecord, MachineStateEvent, MachineTestEvent
from isaac_common.settings import Settings

from collector import Collector
from socket_gateway import SocketGateway

SERVICE = "data_collector"


def main() -> None:
    settings = Settings.from_env(SERVICE)
    log = get_logger(SERVICE)

    ext = "db" if settings.storage_backend == "sqlite" else "jsonl"
    store_path = Path(settings.storage_dir) / f"events.{ext}"
    sink = make_sink(settings.storage_backend, store_path)
    collector = Collector(sink)
    client = MqttClient(settings.mqtt_host, settings.mqtt_port, SERVICE, log)
    ingest_lock = threading.Lock()  # serialize mqtt + socket sources into the collector

    def ingest_state(ev: MachineStateEvent, source: str) -> None:
        with ingest_lock:
            rec = collector.ingest_machine_state(ev)
            # normalized, retained -> the scheduler's current-state feed (§6.1/§7.1)
            client.publish_json(topics.telemetry_state(ev.machine_id), ev, retain=True)
        log.info("#%d %s %s->%s product=%s [%s]", rec.seq, ev.machine_id,
                 rec.from_state, rec.to_state, ev.product_id, source)

    # --- MQTT source ---
    def on_machine_state(topic: str, payload) -> None:
        try:
            ev = MachineStateEvent.model_validate(payload)
        except Exception:
            log.warning("bad machine state on %s: %s", topic, payload)
            return
        ingest_state(ev, "mqtt")

    def on_machine_telemetry(topic: str, payload) -> None:
        try:
            ev = MachineStateEvent.model_validate(payload)
        except Exception:
            return
        with ingest_lock:
            collector.update_telemetry(ev)
            client.publish_json(topics.telemetry_state(ev.machine_id), ev, retain=True)

    def on_arm_command(topic: str, payload) -> None:
        try:
            cmd = ArmCommand.model_validate(payload)
        except Exception:
            return
        with ingest_lock:
            collector.log_event(EventRecord(
                ts=cmd.ts, entity_type="arm", entity_id=cmd.arm_id, event=cmd.action.value,
                from_state=cmd.from_, to_state=cmd.to, product_id=cmd.product_id,
                detail={"task_id": cmd.task_id},
            ))

    def on_test_item(topic: str, payload) -> None:
        # 逐筆測項 -> 歷史事件紀錄表（含 FAIL path）；不轉 Isaac
        try:
            ev = MachineTestEvent.model_validate(payload)
        except Exception:
            return
        with ingest_lock:
            collector.log_event(EventRecord(
                ts=ev.ts, entity_type="test", entity_id=ev.machine_id, event=ev.result,
                product_id=ev.product_id,
                detail={"index": ev.index, "total": ev.total, "item": ev.item,
                        "fault": ev.fault, "path": ev.path,
                        "path_tree": [s.model_dump() for s in ev.path_tree]},
            ))
        if ev.result == "FAIL":
            log.info("%s test #%d/%d %s FAIL(%s) path[%d]", ev.machine_id, ev.index, ev.total,
                     ev.item, ev.fault, len(ev.path))

    def on_control(topic: str, payload) -> None:
        cmd = payload.get("cmd") if isinstance(payload, dict) else None
        if cmd:
            with ingest_lock:
                collector.log_event(EventRecord(entity_type="run", entity_id="-", event=cmd))
            log.info("=== run marker: %s ===", cmd)

    client.subscribe(topics.plant_state_filter(), on_machine_state)
    client.subscribe("plant/machine/+/telemetry", on_machine_telemetry)
    client.subscribe(topics.SCHEDULER_COMMAND, on_arm_command)  # ArmCommand (from/to) — §7.2
    client.subscribe(topics.plant_test_filter(), on_test_item)  # 逐筆測項
    client.subscribe(topics.SIM_CONTROL, on_control)
    client.connect()
    log.info("data_collector up: storage=%s -> %s", settings.storage_backend, store_path)

    # --- socket source (real machines, M4) ---
    gateway = None
    if os.getenv("SOCKET_ENABLED", "true").lower() == "true":
        gateway = SocketGateway(
            os.getenv("SOCKET_HOST", "0.0.0.0"),
            int(os.getenv("SOCKET_PORT", "9000")),
            on_event=lambda ev: ingest_state(ev, "socket"),
            log=log,
        )
        gateway.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        stop.wait(10.0)
        if collector.snapshot:
            log.info("snapshot: %s", collector.state_counts())

    log.info("data_collector shutting down")
    if gateway is not None:
        gateway.stop()
    client.disconnect()
    sink.close()


if __name__ == "__main__":
    main()
