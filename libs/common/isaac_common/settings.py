import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings, all overridable via environment (see .env / docker-compose)."""

    service_name: str
    mqtt_host: str
    mqtt_port: int
    log_level: str
    config_path: str
    positions_path: str
    heartbeat_interval_s: float
    storage_backend: str
    storage_dir: str

    @classmethod
    def from_env(cls, default_service_name: str) -> "Settings":
        return cls(
            service_name=os.getenv("SERVICE_NAME", default_service_name),
            mqtt_host=os.getenv("MQTT_HOST", "broker"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            config_path=os.getenv("CONFIG_PATH", "/app/config/simulation.json"),
            positions_path=os.getenv("POSITIONS_PATH", "/app/config/positions.json"),
            heartbeat_interval_s=float(os.getenv("HEARTBEAT_INTERVAL_S", "5")),
            storage_backend=os.getenv("STORAGE_BACKEND", "sqlite"),
            storage_dir=os.getenv("STORAGE_DIR", "/app/data"),
        )
