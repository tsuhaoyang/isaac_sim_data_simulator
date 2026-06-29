"""Example IsaacArmController skeleton (M5 part 1).

Implements the ArmController shape that IsaacArmBridge calls. Pick-place takes many
frames, so it is NON-BLOCKING: `pick_place()` only sets a goal; `update(dt)` (called
every frame) steps a small per-arm state machine. Fill the TODO blocks with your
robot's motion API (RMPflow/Lula, ArticulationController, gripper, etc.).

This file intentionally does NOT import omni/isaac so it can be read/checked outside
Isaac Sim. Inside Isaac Sim, add the imports and implement the TODOs.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto

# Per-location config that lives on the Isaac side (NOT sent over MQTT).
# Keyed by the same location key the publisher uses ('ProductIn', 'ProductOut', 'Tray_00', ...).
# Tune approach height / gripper to your fixtures. See docs/positions_guide.md.
LOCATION_CONFIG: dict[str, dict] = {
    "ProductIn":  {"approach_dz": 0.15, "gripper_open": 0.08, "gripper_close": 0.02},
    "ProductOut": {"approach_dz": 0.15, "gripper_open": 0.08, "gripper_close": 0.02},
    # "Tray_00": {"approach_dz": 0.15, "gripper_open": 0.08, "gripper_close": 0.02},
}
DEFAULT_LOCATION_CFG = {"approach_dz": 0.15, "gripper_open": 0.08, "gripper_close": 0.02}


class Phase(Enum):
    IDLE = auto()
    APPROACH_PICK = auto()
    DESCEND_PICK = auto()
    GRASP = auto()
    LIFT = auto()
    APPROACH_PLACE = auto()
    DESCEND_PLACE = auto()
    RELEASE = auto()
    RETRACT = auto()


@dataclass
class _ArmJob:
    pick_pos: list[float]
    place_pos: list[float]
    pick_key: str
    place_key: str
    product_id: str | None
    phase: Phase = Phase.IDLE


@dataclass
class IsaacArmController:
    # TODO: pass in your per-arm articulation / motion-gen handles, e.g.
    #   arms: dict[str, Articulation]
    #   motion: dict[str, RmpFlow]
    arms: dict = field(default_factory=dict)
    _jobs: dict[str, _ArmJob] = field(default_factory=dict)          # current job per arm
    _pending: dict[str, deque] = field(default_factory=dict)         # per-arm FIFO backlog

    # --- called by IsaacArmBridge.pump() (main thread) ---
    def pick_place(self, arm_id: str, pick: dict, place: dict, product_id: str | None) -> None:
        # Defense-in-depth: even though the scheduler serializes per-arm by time,
        # never run two motions on one arm at once — queue and run FIFO instead.
        job = _ArmJob(
            pick_pos=pick.get("pos") or [], place_pos=place.get("pos") or [],
            pick_key=pick.get("key", ""), place_key=place.get("key", ""),
            product_id=product_id, phase=Phase.APPROACH_PICK,
        )
        cur = self._jobs.get(arm_id)
        if cur is None or cur.phase is Phase.IDLE:
            self._jobs[arm_id] = job                 # arm free -> start now
        else:
            self._pending.setdefault(arm_id, deque()).append(job)  # busy -> enqueue

    # --- called every Isaac Sim frame (main thread) ---
    def update(self, dt: float) -> None:
        for arm_id, job in self._jobs.items():
            if job.phase is Phase.IDLE:
                pend = self._pending.get(arm_id)
                if pend:                              # current done -> start next queued
                    self._jobs[arm_id] = job = pend.popleft()
                else:
                    continue
            self._step(arm_id, job)

    def _step(self, arm_id: str, job: _ArmJob) -> None:
        pick_cfg = LOCATION_CONFIG.get(job.pick_key, DEFAULT_LOCATION_CFG)
        place_cfg = LOCATION_CONFIG.get(job.place_key, DEFAULT_LOCATION_CFG)

        # Each branch: command the motion, then advance phase once it has settled.
        # `_reached(arm_id, target)` is your "has the arm arrived?" check (TODO).
        if job.phase is Phase.APPROACH_PICK:
            self._move(arm_id, _above(job.pick_pos, pick_cfg["approach_dz"]))
            if self._reached(arm_id, _above(job.pick_pos, pick_cfg["approach_dz"])):
                job.phase = Phase.DESCEND_PICK
        elif job.phase is Phase.DESCEND_PICK:
            self._move(arm_id, job.pick_pos)
            if self._reached(arm_id, job.pick_pos):
                job.phase = Phase.GRASP
        elif job.phase is Phase.GRASP:
            self._gripper(arm_id, pick_cfg["gripper_close"])
            job.phase = Phase.LIFT
        elif job.phase is Phase.LIFT:
            self._move(arm_id, _above(job.pick_pos, pick_cfg["approach_dz"]))
            if self._reached(arm_id, _above(job.pick_pos, pick_cfg["approach_dz"])):
                job.phase = Phase.APPROACH_PLACE
        elif job.phase is Phase.APPROACH_PLACE:
            self._move(arm_id, _above(job.place_pos, place_cfg["approach_dz"]))
            if self._reached(arm_id, _above(job.place_pos, place_cfg["approach_dz"])):
                job.phase = Phase.DESCEND_PLACE
        elif job.phase is Phase.DESCEND_PLACE:
            self._move(arm_id, job.place_pos)
            if self._reached(arm_id, job.place_pos):
                job.phase = Phase.RELEASE
        elif job.phase is Phase.RELEASE:
            self._gripper(arm_id, place_cfg["gripper_open"])
            job.phase = Phase.RETRACT
        elif job.phase is Phase.RETRACT:
            self._move(arm_id, _above(job.place_pos, place_cfg["approach_dz"]))
            if self._reached(arm_id, _above(job.place_pos, place_cfg["approach_dz"])):
                job.phase = Phase.IDLE  # ready for the next command

    # --- TODO: implement these with your Isaac Sim motion stack ---
    def _move(self, arm_id: str, target_pos: list[float]) -> None:
        # TODO: set the motion-gen / IK target for arm_id to target_pos (world frame).
        # e.g. self.motion[arm_id].set_end_effector_target(target_pos)
        ...

    def _reached(self, arm_id: str, target_pos: list[float], tol: float = 0.01) -> bool:
        # TODO: return True when the end-effector is within tol of target_pos.
        # Placeholder returns True so the example sequence advances in the selftest.
        return True

    def _gripper(self, arm_id: str, width: float) -> None:
        # TODO: command the gripper of arm_id to `width` meters.
        ...


def _above(pos: list[float], dz: float) -> list[float]:
    return [pos[0], pos[1], pos[2] + dz] if len(pos) == 3 else list(pos)
