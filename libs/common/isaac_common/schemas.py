"""Message contracts (pydantic v2). All MQTT payloads must use these (SPEC §6)."""

import time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def _now() -> float:
    return time.time()


# --- M0 connectivity ---
class Heartbeat(BaseModel):
    service: str
    status: str = "alive"
    ts: float = Field(default_factory=_now)


# --- machine state (SPEC §4 / §6.2) ---
class MachineState(str, Enum):
    EMPTY = "empty"
    START = "start"
    WORKING = "working"
    DONE = "done"
    ERROR = "error"


class MachineStateEvent(BaseModel):
    machine_id: str
    state: MachineState
    product_id: str | None = None
    elapsed_s: float = 0.0
    remaining_s: float = 0.0
    ts: float = Field(default_factory=_now)


# --- arm command (SPEC §6.3) ---
class ArmAction(str, Enum):
    LOAD = "load"      # ProductIn -> machine
    UNLOAD = "unload"  # machine -> ProductOut


class ArmCommand(BaseModel):
    # `from` is a python keyword -> exposed as `from_`, serialized as "from".
    model_config = ConfigDict(populate_by_name=True)

    task_id: str
    arm_id: str
    action: ArmAction
    from_: str = Field(alias="from")
    to: str
    product_id: str | None = None
    ts: float = Field(default_factory=_now)


# --- Isaac Sim command (SPEC §6.4) ---
class Waypoint(BaseModel):
    key: str
    pos: list[float] | None = None


class IsaacSimCommand(BaseModel):
    arm_id: str
    action: str = "move"
    pick: Waypoint
    place: Waypoint
    product_id: str | None = None
    ts: float = Field(default_factory=_now)


# --- historical event log row (SPEC §7.2) ---
class EventRecord(BaseModel):
    seq: int = 0
    ts: float = Field(default_factory=_now)
    entity_type: str                      # machine | arm | product
    entity_id: str
    event: str                            # state_change | load | unload | error | recovery | arrival | scrap
    from_state: str | None = None
    to_state: str | None = None
    product_id: str | None = None
    detail: dict = Field(default_factory=dict)
