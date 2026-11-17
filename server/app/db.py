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
    mysql://user@localhost/quiz?unix_socket=/run/mysqld/mysqld.sock
                                       production without a password in any file: the account is
                                       `auth_socket`, so the kernel has already proved who we are

Keeping the SQL portable has one hard rule: no dialect-specific upsert, no `RETURNING`, no window
functions. Insert-then-select is two round trips and it is fine at this scale.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import parse_qs, unquote, urlparse

DB_DIR = Path(__file__).resolve().parent.parent / "db"
SQLITE_SCHEMA = DB_DIR / "schema.sqlite.sql"


def _plain(value: Any) -> Any:
    """Dates and datetimes as the strings SQLite would have handed back.

    PyMySQL returns `datetime.date` / `datetime.datetime` objects; SQLite returns the text that was
    written. Callers above this layer slice those values as strings — `parse_iso(text[:19])` — so one
    backend works and the other raises `TypeError` on the very first grade. That is not hypothetical:
    it is what happened the first time this server ran against a real MySQL (2026-11-16).

    Normalising here keeps the dialect difference inside the one module whose job is dialect
    differences; every route above sees the same shape it always saw.
    """
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return value


class Database:
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("QUIZ_DSN", "sqlite::memory:")
        self.is_sqlite = self.dsn.startswith("sqlite:")
        self.is_memory = self.dsn in ("sqlite::memory:", "sqlite:")
        self._lock = threading.RLock()
        self._conn: Any = None
        self._depth = 0

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
            options: dict[str, Any] = {
                "user": unquote(parsed.username or ""),
                "password": unquote(parsed.password or ""),
                "database": (parsed.path or "/quiz").lstrip("/"),
                "charset": "utf8mb4",
                # One statement, one transaction — the same shape SQLite gives us here, and the only
                # shape that cannot go stale. With autocommit off, a plain SELECT left an open
                # REPEATABLE READ transaction behind, and every later read on that connection saw the
                # world as it was when the *previous* request ended: a mistake the parent recorded on
                # the phone was invisible to the next group (found on MySQL, 2026-11-17). `tx()` opens
                # a real transaction with BEGIN instead.
                "autocommit": True,
                "cursorclass": pymysql.cursors.DictCursor,
            }
            # A unix socket DSN carries no password on purpose (see the module docstring): there is
            # nothing to leak into a config file or into `ps`, and MySQL's auth_socket plugin has
            # already checked the uid of the process on the other end of the socket.
            socket_path = parse_qs(parsed.query).get("unix_socket", [""])[0]
            if socket_path:
                options["unix_socket"] = socket_path
            else:
                options["host"] = parsed.hostname or "127.0.0.1"
                options["port"] = parsed.port or 3306
            self._conn = pymysql.connect(**options)

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
        return [{key: _plain(value) for key, value in dict(row).items()} for row in rows]

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
        """A write transaction. Nested use is a no-op bracket, not a savepoint.

        MySQL runs with autocommit on (see `connect`), so the outermost bracket has to say BEGIN
        itself; a nested one must *not*, or MySQL would silently commit the outer transaction and
        leave half of it on disk. That is why the depth counter is here rather than a comment saying
        "do not nest".
        """
        with self._lock:
            outermost = self._depth == 0
            if outermost and not self.is_sqlite:
                self._conn.begin()
            self._depth += 1
            cur = self._conn.cursor()
            try:
                yield Cursor(self, cur)
                if outermost:
                    self._conn.commit()
            except Exception:
                if outermost:
                    self._conn.rollback()
                raise
            finally:
                self._depth -= 1
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
        # Same normalisation as `Database.query`: inside a transaction PyMySQL hands back
        # `datetime` objects where SQLite hands back text, and a caller that compares the two — the
        # pause window does exactly that — would silently get a different answer on each backend.
        return [{key: _plain(value) for key, value in dict(row).items()}
                for row in self._cur.fetchall()]

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
