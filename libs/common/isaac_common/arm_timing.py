"""Per-machine arm move times (SPEC §5): different trays/machines are at different
positions, so load (ProductIn->machine) and unload (machine->ProductOut) take
different times per machine.

config `arm` block:
    "arm": {
      "arm_move_time_s": 3.0,      # default load time (fallback)
      "arm_to_tray_time_s": 3.0,   # default unload time (fallback)
      "per_machine": {             # optional overrides per machine
        "Tray_00": {"load_s": 2.7, "unload_s": 2.9},
        ...
      }
    }
Machines not listed in per_machine use the flat defaults.
"""


class ArmTimes:
    def __init__(self, cfg: dict):
        arm = cfg.get("arm", {})
        self._load_def = float(arm.get("arm_move_time_s", 3.0))
        self._unload_def = float(arm.get("arm_to_tray_time_s", self._load_def))
        self._per = arm.get("per_machine") or {}

    def load(self, machine_id: str) -> float:
        return float(self._per.get(machine_id, {}).get("load_s", self._load_def))

    def unload(self, machine_id: str) -> float:
        return float(self._per.get(machine_id, {}).get("unload_s", self._unload_def))

    def averages(self, machine_ids: list[str]) -> tuple[float, float]:
        """Mean load/unload over the given machines — used for capacity estimates
        (which decide *how many* machines, not which)."""
        if not machine_ids:
            return self._load_def, self._unload_def
        loads = [self.load(m) for m in machine_ids]
        unloads = [self.unload(m) for m in machine_ids]
        return sum(loads) / len(loads), sum(unloads) / len(unloads)
