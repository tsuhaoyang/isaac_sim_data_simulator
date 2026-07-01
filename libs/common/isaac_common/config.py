import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON config file (simulation.json / positions.json)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def machine_ids(cfg: dict) -> list[str]:
    """Machine names from config — single source used by every service.
    reachability_matrix values if present, else M01..M{count}."""
    matrix = cfg.get("reachability_matrix")
    if matrix:
        return sorted({m for ms in matrix.values() for m in ms})
    return [f"M{i:02d}" for i in range(1, int(cfg["machines"]["count"]) + 1)]
