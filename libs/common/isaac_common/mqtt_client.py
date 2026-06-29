import json
import time
from typing import Any, Callable

import paho.mqtt.client as mqtt
from pydantic import BaseModel

from .logging import get_logger

Handler = Callable[[str, Any], None]


class MqttClient:
    """Thin paho-mqtt v2 wrapper: JSON publish, filtered subscribe, resilient connect.

    Services talk ONLY through this (SPEC: services never import each other).
    """

    def __init__(self, host: str, port: int, client_id: str, logger=None):
        self.log = logger or get_logger(client_id)
        self._host = host
        self._port = port
        self._handlers: dict[str, Handler] = {}
        self._client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    # --- callbacks ---
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.log.info("mqtt connected (rc=%s)", reason_code)
        for topic_filter in self._handlers:  # (re)subscribe after every (re)connect
            client.subscribe(topic_filter)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (ValueError, UnicodeDecodeError):
            payload = msg.payload.decode(errors="replace")
        for topic_filter, handler in self._handlers.items():
            if mqtt.topic_matches_sub(topic_filter, msg.topic):
                try:
                    handler(msg.topic, payload)
                except Exception:  # one bad handler must not kill the loop
                    self.log.exception("handler error on topic %s", msg.topic)

    # --- public API ---
    def subscribe(self, topic_filter: str, handler: Handler) -> None:
        self._handlers[topic_filter] = handler
        if self._client.is_connected():
            self._client.subscribe(topic_filter)

    def publish_json(self, topic: str, payload: BaseModel | dict, *, qos: int = 0, retain: bool = False) -> None:
        data = payload.model_dump(by_alias=True, mode="json") if isinstance(payload, BaseModel) else payload
        self._client.publish(topic, json.dumps(data), qos=qos, retain=retain)

    def connect(self, retries: int = 30, delay_s: float = 2.0) -> None:
        for attempt in range(1, retries + 1):
            try:
                self._client.connect(self._host, self._port, keepalive=30)
                self._client.loop_start()
                return
            except OSError as exc:
                self.log.warning("broker not ready (%d/%d): %s", attempt, retries, exc)
                time.sleep(delay_s)
        raise RuntimeError(f"cannot connect to broker {self._host}:{self._port}")

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
