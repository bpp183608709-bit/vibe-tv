"""Bring an existing database up to the current schema.

`schema.sqlite.sql` only ever runs against an empty file — `Database.connect(init_schema=True)` checks
for tables first and otherwise leaves the database alone. That is the right default (it never touches a
live database), but it means a database created before a schema change keeps the old shape and the
server dies at the first query that mentions a new column: `no such column: used_ms`.

This is the deliberate way to do that, run by hand:

    python3 -m server.tools.migrate --dsn sqlite:server/local/tv1.sqlite3

What it does: for every table and column in the current DDL, add what is missing. Nothing is dropped
or rewritten, so it is safe to run twice, and safe on a database whose shape is already current. For
MySQL it prints the statements instead of running them — production gets applied by hand, reviewed,
because "add a column" on a live table is not something to do implicitly.

Nothing here copies data. A column added by this tool starts at its DEFAULT, which for `used_ms` means
"never reported" rather than "watched zero minutes" — the parent page draws a dash, which is true.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "db"

#: Column kinds for MySQL, only used when printing statements. SQLite takes anything.
_MYSQL_TYPE = {
    "used_ms": "INT UNSIGNED NOT NULL DEFAULT 0",
    "used_at": "DATETIME NULL",
    "recovery_question": "VARCHAR(200) NULL",
    "recovery_answer_hash": "VARCHAR(255) NULL",
    "recover_fail_count": "SMALLINT UNSIGNED NOT NULL DEFAULT 0",
    "recover_locked_until": "DATETIME NULL",
}


def ddl_blocks(path: Path) -> dict[str, str]:
    """Every `CREATE TABLE <name> ( ... );` and `CREATE INDEX <name> ...;` in one DDL file."""
    sql = path.read_text(encoding="utf-8")
    sql = re.sub(r"--[^\n]*", "", sql)
    blocks: dict[str, str] = {}
    for match in re.finditer(r"CREATE\s+(?:TABLE|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)(.*?);",
                             sql, re.I | re.S):
        blocks[match.group(1)] = match.group(0)
    return blocks


def expected_columns(sqlite_ddl: Path) -> dict[str, list[tuple[str, str]]]:
    """{table: [(column, declaration)]}, read from the SQLite DDL the server actually runs."""
    sql = re.sub(r"--[^\n]*", "", sqlite_ddl.read_text(encoding="utf-8"))
    out: dict[str, list[tuple[str, str]]] = {}
    for match in re.finditer(r"CREATE\s+TABLE\s+(\w+)\s*\(", sql, re.I):
        body = _body(sql, match.end() - 1)
        columns = []
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT",
                                                    "CHECK", "KEY")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                columns.append((parts[0], parts[1]))
        out[match.group(1)] = columns
    return out


def _body(sql: str, open_paren: int) -> str:
    depth = 0
    for i in range(open_paren, len(sql)):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren + 1 : i]
    raise ValueError("unbalanced parentheses")


def sqlite_path(dsn: str) -> Path:
    if not dsn.startswith("sqlite:"):
        raise SystemExit("this tool only edits sqlite: databases; pass the MySQL statements by hand")
    raw = dsn.split(":", 1)[1] or ""
    if not raw or raw == ":memory:":
        raise SystemExit("refusing to migrate an in-memory database")
    return Path(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="add missing tables and columns")
    parser.add_argument("--dsn", default="sqlite:server/local/quiz.sqlite3")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = sqlite_path(args.dsn)
    if not path.exists():
        print(f"no such database: {path}", file=sys.stderr)
        return 1

    blocks = ddl_blocks(DB_DIR / "schema.sqlite.sql")
    wanted = expected_columns(DB_DIR / "schema.sqlite.sql")

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=OFF")
    existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    existing_indexes = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }

    statements: list[str] = []
    for table in wanted:
        if table not in existing:
            statements.append(blocks[table])
            # Its indexes are separate statements; they are skipped when the table is new only if the
            # table block is all that was missing, so run them too and ignore "already exists".
            continue
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, declaration in wanted[table]:
            if column not in have:
                statements.append(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    for name, block in blocks.items():
        if not block.upper().startswith("CREATE INDEX"):
            continue
        if name in existing_indexes:
            continue
        statements.append(block)

    if not statements:
        print(f"{path.name}: already current")
        return 0

    for statement in statements:
        print(("would run: " if args.dry_run else "run: ") + " ".join(statement.split())[:110])
        if not args.dry_run:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                # An index on a column that already existed is not a failure worth stopping for.
                if "already exists" not in str(exc):
                    raise
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"{path.name}: {len(statements)} change(s){' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
