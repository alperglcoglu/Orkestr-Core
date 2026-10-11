"""SQLite connections, migrations, and transactions (PHASE3_IMPLEMENTATION.md §5.1)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..domain.errors import OrkestrError, now

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(path: Path) -> sqlite3.Connection:
    """One connection per operation; transactions are explicit (isolation_level=None)."""
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, write: bool = True):
    """BEGIN IMMEDIATE takes the write lock up front, so two writers never interleave."""
    conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrations() -> list[tuple[int, str]]:
    out = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        out.append((int(f.name.split("_", 1)[0]), f.read_text(encoding="utf-8")))
    return out


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Applies pending forward-only migrations, each in its own transaction."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    known = migrations()
    newest = max((v for v, _ in known), default=0)
    if applied and max(applied) > newest:
        raise OrkestrError(
            f"The database schema (version {max(applied)}) is newer than this orkestr-core (version {newest}). Update orkestr-core.",
            "E_SCHEMA",
        )
    done = []
    for version, sql in known:
        if version in applied:
            continue
        # executescript commits implicitly, so the migration is wrapped explicitly.
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations VALUES ({version}, '{now()}');\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        done.append(version)
    return done
