import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON config file (simulation.json / positions.json)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
