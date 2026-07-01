"""Per-machine arm move times (SPEC §5): different trays/machines are at different
positions, so load (ProductIn->machine) and unload (machine->ProductOut) take
different times per machine.

ADDITIVE model:  effective time = base + per-machine delta
- base: the common part (grip / approach) — arm_move_time_s / arm_to_tray_time_s
- delta: the machine-specific extra (travel to that position) — per_machine load_s/unload_s

config `arm` block:
    "arm": {
      "arm_move_time_s": 5.0,      # base load time (common to every machine)
      "arm_to_tray_time_s": 5.0,   # base unload time
      "per_machine": {             # extra seconds ADDED per machine (travel delta)
        "Tray_00": {"load_s": 2.7, "unload_s": 2.9},   # -> load 7.7s, unload 7.9s
        ...
      }
    }
A machine not listed in per_machine has delta 0 -> just the base time.
"""


class ArmTimes:
    def __init__(self, cfg: dict):
        arm = cfg.get("arm", {})
        self._load_base = float(arm.get("arm_move_time_s", 3.0))
        self._unload_base = float(arm.get("arm_to_tray_time_s", self._load_base))
        self._per = arm.get("per_machine") or {}

    def load(self, machine_id: str) -> float:
        return self._load_base + float(self._per.get(machine_id, {}).get("load_s", 0.0))

    def unload(self, machine_id: str) -> float:
        return self._unload_base + float(self._per.get(machine_id, {}).get("unload_s", 0.0))

    def averages(self, machine_ids: list[str]) -> tuple[float, float]:
        """Mean load/unload over the given machines — used for capacity estimates
        (which decide *how many* machines, not which)."""
        if not machine_ids:
            return self._load_base, self._unload_base
        loads = [self.load(m) for m in machine_ids]
        unloads = [self.unload(m) for m in machine_ids]
        return sum(loads) / len(loads), sum(unloads) / len(unloads)
