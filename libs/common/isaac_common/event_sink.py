"""Pluggable persistence for the historical event log (SPEC §7.2/§7.3).

Backend is swappable via config so services never bind to a storage choice.
demo defaults to sqlite; jsonl is handy for grep/debugging. TimescaleDB later.
"""

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from .schemas import EventRecord


class EventSink(ABC):
    @abstractmethod
    def write(self, rec: EventRecord) -> None: ...

    def close(self) -> None:  # optional override
        pass


class JsonlSink(EventSink):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._lock = threading.Lock()
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, rec: EventRecord) -> None:
        with self._lock:
            self._seq += 1
            rec.seq = self._seq
            self._fh.write(rec.model_dump_json() + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class SqliteSink(EventSink):
    _DDL = """
        CREATE TABLE IF NOT EXISTS event_log (
            seq         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL,
            entity_type TEXT,
            entity_id   TEXT,
            event       TEXT,
            from_state  TEXT,
            to_state    TEXT,
            product_id  TEXT,
            detail      TEXT
        )
    """

    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(self._DDL)
        self._conn.commit()

    def write(self, rec: EventRecord) -> None:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO event_log "
                "(ts, entity_type, entity_id, event, from_state, to_state, product_id, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.ts, rec.entity_type, rec.entity_id, rec.event,
                    rec.from_state, rec.to_state, rec.product_id, json.dumps(rec.detail),
                ),
            )
            rec.seq = cur.lastrowid
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def make_sink(backend: str, path: str | Path) -> EventSink:
    backend = (backend or "sqlite").lower()
    if backend == "jsonl":
        return JsonlSink(path)
    if backend == "sqlite":
        return SqliteSink(path)
    raise ValueError(f"unknown storage backend: {backend!r} (use 'sqlite' or 'jsonl')")
