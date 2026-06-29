"""MQTT topic builders. Single source of truth for topic strings (SPEC §6.1)."""

# --- health / heartbeat (M0 connectivity) ---
def health_heartbeat(service: str) -> str:
    return f"health/{service}/heartbeat"


def health_filter() -> str:
    return "health/+/heartbeat"


# --- plant telemetry (machine_simulator / real machines -> data_collector) ---
def machine_state(machine_id: str) -> str:
    return f"plant/machine/{machine_id}/state"


def machine_telemetry(machine_id: str) -> str:
    return f"plant/machine/{machine_id}/telemetry"


def plant_state_filter() -> str:
    return "plant/machine/+/state"


# --- normalized telemetry (data_collector -> scheduler_engine) ---
def telemetry_state(machine_id: str) -> str:
    return f"telemetry/machine/{machine_id}/state"


def telemetry_state_filter() -> str:
    return "telemetry/machine/+/state"


# --- scheduler -> publisher / dashboard ---
SCHEDULER_COMMAND = "scheduler/command"
SCHEDULER_METRICS = "scheduler/metrics"


# --- publisher -> Isaac Sim ---
def isaacsim_arm_command(arm_id: str) -> str:
    return f"isaacsim/arm/{arm_id}/command"


def isaacsim_arm_filter() -> str:
    return "isaacsim/arm/+/command"


# single aggregated snapshot of ALL machines: payload = {machine_id: {state,...}, ...}
ISAACSIM_MACHINE_STATE = "isaacsim/machine/state"
