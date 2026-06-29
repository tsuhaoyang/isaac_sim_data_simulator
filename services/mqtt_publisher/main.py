"""mqtt_publisher (指令翻譯 -> Isaac Sim). M2: scheduler/command -> isaacsim/arm/{id}/command."""

import signal
import threading

from isaac_common import topics
from isaac_common.config import load_json
from isaac_common.logging import get_logger
from isaac_common.mqtt_client import MqttClient
from isaac_common.schemas import ArmCommand
from isaac_common.settings import Settings

from translate import translate

SERVICE = "mqtt_publisher"


def main() -> None:
    settings = Settings.from_env(SERVICE)
    log = get_logger(SERVICE)
    positions = load_json(settings.positions_path)
    client = MqttClient(settings.mqtt_host, settings.mqtt_port, SERVICE, log)

    def on_command(topic: str, payload) -> None:
        try:
            cmd = ArmCommand.model_validate(payload)
        except Exception:
            log.warning("bad scheduler command: %s", payload)
            return
        ic = translate(cmd, positions)
        client.publish_json(topics.isaacsim_arm_command(cmd.arm_id), ic)
        missing = [w.key for w in (ic.pick, ic.place) if w.pos is None]
        log.info("%s arm=%s %s pick=%s place=%s%s", cmd.task_id, cmd.arm_id, cmd.action.value,
                 ic.pick.key, ic.place.key,
                 f"  [WARN no position for {missing}]" if missing else "")

    machine_states: dict[str, dict] = {}  # machine_id -> {state, product_id, elapsed_s, remaining_s, ts}

    def on_machine_state(topic: str, payload) -> None:
        # aggregate ALL machines into one retained snapshot for Isaac Sim:
        #   { "M01": {state, product_id, elapsed_s, remaining_s, ts}, "M02": {...}, ... }
        mid = topic.split("/")[2]   # telemetry/machine/{id}/state
        if isinstance(payload, dict):
            machine_states[mid] = {k: v for k, v in payload.items() if k != "machine_id"}
            client.publish_json(topics.ISAACSIM_MACHINE_STATE, machine_states, retain=True)

    client.subscribe(topics.SCHEDULER_COMMAND, on_command)
    client.subscribe(topics.telemetry_state_filter(), on_machine_state)
    client.connect()
    log.info("mqtt_publisher up: %d known positions", len(positions.get("locations", {})))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        stop.wait(3600)

    log.info("mqtt_publisher shutting down")
    client.disconnect()


if __name__ == "__main__":
    main()
