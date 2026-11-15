"""One database handle, two dialects, no ORM.

The server is a single process on a 2-core box with a handful of routes. An ORM would add a dependency
and a learning surface for nothing; what is actually needed is:

  * one place that knows how to talk to SQLite (developer machine, in-memory, tests) and MySQL (ECS);
  * a translator for the one difference that leaks into every statement — `?` versus `%s`;
  * a transaction helper that serialises access, because both drivers are used from a thread-pooled
    HTTP server and SQLite cannot take concurrent writers at all.

`QUIZ_DSN` selects the backend:

    sqlite::memory:                    default; schema is applied at startup, data dies with the process
    sqlite:/abs/path/quiz.sqlite3      a file, for poking at data between runs
    mysql://user:pass@host:3306/quiz   production; schema.sql is applied by hand, not here

Keeping the SQL portable has one hard rule: no dialect-specific upsert, no `RETURNING`, no window
functions. Insert-then-select is two round trips and it is fine at this scale.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import unquote, urlparse

DB_DIR = Path(__file__).resolve().parent.parent / "db"
SQLITE_SCHEMA = DB_DIR / "schema.sqlite.sql"


class Database:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("QUIZ_DSN", "sqlite::memory:")
        self.is_sqlite = self.dsn.startswith("sqlite:")
        self.is_memory = self.dsn in ("sqlite::memory:", "sqlite:")
        self._lock = threading.RLock()
        self._conn: Any = None

    # ---- connection ----

    def connect(self, *, init_schema: bool = False) -> None:
        if self.is_sqlite:
            path = self.dsn.split("sqlite:", 1)[1]
            target = ":memory:" if path in ("", ":memory:") else path
            self._conn = sqlite3.connect(target, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            # No journal file at all. The default rollback journal creates and deletes a sibling file on
            # every transaction, which fails on a read-only-adjacent volume or under a sandbox that
            # blocks unlinking. OFF is safe here because this database has exactly one writer process:
            # it is a developer/acceptance convenience, never a deployment target.
            self._conn.execute("PRAGMA journal_mode = OFF")
            self._conn.execute("PRAGMA synchronous = OFF")
            if init_schema:
                has_tables = self._conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
                if not has_tables:
                    self._conn.executescript(SQLITE_SCHEMA.read_text(encoding="utf-8"))
        else:
            import pymysql  # only imported for the MySQL path

            parsed = urlparse(self.dsn)
            self._conn = pymysql.connect(
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port or 3306,
                user=unquote(parsed.username or ""),
                password=unquote(parsed.password or ""),
                database=(parsed.path or "/quiz").lstrip("/"),
                charset="utf8mb4",
                autocommit=False,
                cursorclass=pymysql.cursors.DictCursor,
            )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _sql(self, sql: str) -> str:
        # Our SQL never contains a literal '?' inside a string, so a plain swap is safe here. If that
        # ever stops being true, this is the line that breaks, and it breaks loudly.
        return sql if self.is_sqlite else sql.replace("?", "%s")

    # ---- reads ----

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(self._sql(sql), tuple(params))
                rows = cur.fetchall()
            finally:
                cur.close()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        return next(iter(row.values()))

    # ---- writes ----

    @contextmanager
    def tx(self) -> Iterator["Cursor"]:
        """A write transaction. Nested use is a no-op bracket, not a savepoint."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield Cursor(self, cur)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Single statement, own transaction. Row count."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(self._sql(sql), tuple(params))
                self._conn.commit()
                return cur.rowcount
            finally:
                cur.close()

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Single INSERT, own transaction. Returns the new auto-increment id, or 0."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(self._sql(sql), tuple(params))
                self._conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                cur.close()

    def lock_clause(self) -> str:
        """Row lock, where the dialect has one. SQLite serialises writers on its own; our global lock
        covers the read-decide-write window that `FOR UPDATE` protects in MySQL."""
        return "" if self.is_sqlite else " FOR UPDATE"

    def now(self) -> str:
        """Timestamps are stored as ISO-8601 text in SQLite and DATETIME in MySQL; the application
        passes them in rather than leaning on a dialect default."""
        import datetime

        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Cursor:
    """Thin wrapper so a transaction body does not touch the driver."""

    def __init__(self, db: Database, cur: Any):
        self._db = db
        self._cur = cur

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        self._cur.execute(self._db._sql(sql), tuple(params))
        return int(self._cur.rowcount)

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        self._cur.execute(self._db._sql(sql), tuple(params))
        return int(self._cur.lastrowid or 0)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        self._cur.execute(self._db._sql(sql), tuple(params))
        return [dict(row) for row in self._cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


_default: Database | None = None


def default_db(**kwargs: Any) -> Database:
    """Process-wide handle, connected on first use."""
    global _default
    if _default is None:
        _default = Database()
        _default.connect(init_schema=True, **kwargs)
    return _default


def reset_default() -> None:
    """Tests only."""
    global _default
    if _default is not None:
        _default.close()
    _default = None
