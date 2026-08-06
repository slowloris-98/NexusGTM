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


# Columns added to tables that already ship in existing databases. CREATE TABLE
# IF NOT EXISTS silently does nothing to a table that exists, so a column added to
# schema.sql alone would never reach a store that has already been created.
_ADDED_COLUMNS = (
    ("orchestrations", "flow", "TEXT"),
    ("decisions", "flow", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add missing nullable columns in place.

    Adding a nullable column is the one schema change SQLite does without
    rebuilding the table, which is why every entry here must stay nullable.
    """
    for table, column, decl in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Create the schema if absent, migrate it if stale, return an open connection."""
    conn = connect(path)
    conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
    _migrate(conn)
    conn.commit()
    return conn
