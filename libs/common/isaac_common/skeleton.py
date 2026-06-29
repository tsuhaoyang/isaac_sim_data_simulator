"""M0 service skeleton: connect, emit heartbeats, discover peers over MQTT.

Each service's main.py calls run_skeleton(name). Later milestones replace the
body with real logic while keeping the same connect/shutdown lifecycle.
"""

import signal
import threading

from . import topics
from .logging import get_logger
from .mqtt_client import MqttClient
from .schemas import Heartbeat
from .settings import Settings


def run_skeleton(default_service_name: str) -> None:
    settings = Settings.from_env(default_service_name)
    name = settings.service_name
    log = get_logger(name)

    client = MqttClient(settings.mqtt_host, settings.mqtt_port, name, log)
    peers: set[str] = set()

    def on_heartbeat(topic: str, payload) -> None:
        peer = payload.get("service") if isinstance(payload, dict) else None
        if peer and peer != name and peer not in peers:
            peers.add(peer)
            log.info("discovered peer '%s' (known peers: %s)", peer, sorted(peers))

    client.subscribe(topics.health_filter(), on_heartbeat)
    client.connect()
    log.info("service '%s' up; broker=%s:%s", name, settings.mqtt_host, settings.mqtt_port)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    while not stop.is_set():
        client.publish_json(topics.health_heartbeat(name), Heartbeat(service=name))
        stop.wait(settings.heartbeat_interval_s)

    log.info("service '%s' shutting down", name)
    client.disconnect()
