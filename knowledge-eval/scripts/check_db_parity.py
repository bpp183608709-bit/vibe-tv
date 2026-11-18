"""Does the live database have the shape the DDL promises, and does it obey the two-level rules?

    python knowledge-eval/scripts/check_db_parity.py
    python knowledge-eval/scripts/check_db_parity.py --dsn sqlite:server/local/tv1.sqlite3
    QUIZ_DSN=mysql://... python knowledge-eval/scripts/check_db_parity.py --content

Two questions, in order:

  1. shape   every table in the DDL exists, with exactly the columns the DDL declares, and nothing else;
             the two DDL files are also compared with each other (server/tools/check_schema_parity.py),
             so MySQL and SQLite cannot drift apart;
  2. content the rules the review asked for, as SQL: no question points at a chapter, every question
             knows its subject and code, every node knows its chapter and its stable id.

Exit code 1 on any difference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402

sys.path.insert(0, str(keval.REPO))

from server.app.db import Database, default_db  # noqa: E402
from server.tools import check_schema_parity as parity  # noqa: E402

DB_DIR = keval.REPO / "server" / "db"

#: Rule -> (SQL returning the number of violations, what it means).
CONTENT_RULES = (
    ("question.unit_id is empty",
     "SELECT COUNT(*) FROM question WHERE unit_id IS NOT NULL"),
    ("no question reaches a chapter through unit",
     "SELECT COUNT(*) FROM question q JOIN unit u ON u.id = q.unit_id"),
    ("every question knows its subject",
     "SELECT COUNT(*) FROM question WHERE subject_id IS NULL"),
    ("every question has its business key",
     "SELECT COUNT(*) FROM question WHERE code IS NULL"),
    ("every diagnostic node has its stable id",
     "SELECT COUNT(*) FROM topic WHERE code IS NULL"),
    ("every diagnostic node knows its chapter order",
     "SELECT COUNT(*) FROM topic WHERE chapter_order = 0"),
    ("every chapter has its stable id",
     "SELECT COUNT(*) FROM unit WHERE code IS NULL"),
    ("no question points at a missing node",
     "SELECT COUNT(*) FROM question q LEFT JOIN topic t ON t.id = q.topic_id WHERE t.id IS NULL"),
)


def live_columns(db: Database) -> dict[str, list[str]]:
    if db.is_sqlite:
        names = [row["name"] for row in db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {name: [row["name"] for row in db.query(f'PRAGMA table_info("{name}")')]
                for name in names}
    names = [list(row.values())[0] for row in db.query("SHOW TABLES")]
    return {name: [row["column_name"] for row in db.query(
        "SELECT COLUMN_NAME AS column_name FROM information_schema.COLUMNS"
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION", (name,))]
        for name in names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="database shape and content parity")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--content", action="store_true", help="also run the content rules")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    problems: list[str] = []

    # 1a. the two DDL files agree with each other
    mysql = parity.parse(DB_DIR / "schema.sql")
    sqlite = parity.parse(DB_DIR / "schema.sqlite.sql")
    for name in sorted(set(mysql) ^ set(sqlite)):
        problems.append(f"ddl: table {name} only in one of schema.sql / schema.sqlite.sql")
    for name in sorted(set(mysql) & set(sqlite)):
        for column in [c for c in mysql[name] if c not in sqlite[name]]:
            problems.append(f"ddl: {name}.{column} only in schema.sql")
        for column in [c for c in sqlite[name] if c not in mysql[name]]:
            problems.append(f"ddl: {name}.{column} only in schema.sqlite.sql")

    # 1b. the live database matches its DDL
    db = Database(args.dsn) if args.dsn else default_db()
    db.connect()
    expected = mysql if not db.is_sqlite else sqlite
    live = live_columns(db)
    for name in sorted(set(expected) - set(live)):
        problems.append(f"db: table {name} is declared but missing")
    for name in sorted(set(live) - set(expected)):
        problems.append(f"db: table {name} exists but is not declared")
    for name in sorted(set(expected) & set(live)):
        want, got = expected[name], live[name]
        for column in [c for c in want if c not in got]:
            problems.append(f"db: {name}.{column} declared but missing")
        for column in [c for c in got if c not in want]:
            problems.append(f"db: {name}.{column} exists but is not declared")

    # 2. the content rules
    if args.content:
        for label, sql in CONTENT_RULES:
            bad = db.scalar(sql, default=0)
            if int(bad or 0):
                problems.append(f"content: {label} — {bad} row(s)")
    db.close()

    if problems:
        for problem in problems:
            print(f"  x {problem}", file=sys.stderr)
        print(f"db parity: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"db parity: ok ({len(live)} tables, {sum(len(v) for v in live.values())} columns"
              + (", content rules hold" if args.content else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
