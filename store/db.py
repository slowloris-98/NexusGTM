"""Connection factory for the single SQLite store.

The orchestrator writes while the API reads, so WAL is not optional -- without it
concurrent access raises "database is locked".
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema.sql")


def db_path() -> Path:
    return Path(os.getenv("NEXUSGTM_DB", "nexusgtm.db")).resolve()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Create the schema if absent and return an open connection."""
    conn = connect(path)
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    return conn
