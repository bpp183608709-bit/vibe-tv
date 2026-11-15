"""Compare the MySQL and SQLite DDLs so the two cannot drift apart.

schema.sql is the production truth; schema.sqlite.sql exists so the acceptance suite can run on a
machine without MySQL. The risk of a second DDL is that someone adds a column to one file and forgets
the other, and the failure shows up much later as a cryptic "no such column" during grading.

So: parse both files, compare {table -> columns} exactly. Types are deliberately not compared (they
differ by design). Exit code 1 on any difference, which makes this usable in a pre-commit hook.

    python3 server/tools/check_schema_parity.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "db"

# Lines that look like a column but are table-level constraints.
_NOT_A_COLUMN = ("primary", "unique", "key", "constraint", "foreign", "check", "index")


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return re.sub(r"--[^\n]*", "", sql)


def _block(sql: str, start: int) -> str:
    """Return the text inside the parentheses that open at or after `start`."""
    open_paren = sql.index("(", start)
    depth = 0
    for i in range(open_paren, len(sql)):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren + 1 : i]
    raise ValueError("unbalanced parentheses")


def _split_top_level(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse(path: Path) -> dict[str, list[str]]:
    sql = _strip_comments(path.read_text(encoding="utf-8"))
    tables: dict[str, list[str]] = {}
    for match in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*\(", sql, re.I):
        name = match.group(1).lower()
        body = _block(sql, match.start())
        columns = []
        for item in _split_top_level(body):
            first = re.split(r"\s+", item, maxsplit=1)[0]
            ident = first.strip("`\"[]").lower()
            if ident in _NOT_A_COLUMN:
                continue
            columns.append(ident)
        tables[name] = columns
    return tables


def main() -> int:
    mysql = parse(DB_DIR / "schema.sql")
    sqlite = parse(DB_DIR / "schema.sqlite.sql")

    problems: list[str] = []
    for name in sorted(set(mysql) - set(sqlite)):
        problems.append(f"table {name}: present in schema.sql, missing from schema.sqlite.sql")
    for name in sorted(set(sqlite) - set(mysql)):
        problems.append(f"table {name}: present in schema.sqlite.sql, missing from schema.sql")
    for name in sorted(set(mysql) & set(sqlite)):
        a, b = mysql[name], sqlite[name]
        for col in [c for c in a if c not in b]:
            problems.append(f"table {name}: column {col} only in schema.sql")
        for col in [c for c in b if c not in a]:
            problems.append(f"table {name}: column {col} only in schema.sqlite.sql")
        if not problems and a != b:
            problems.append(f"table {name}: column order differs ({a} vs {b})")

    if problems:
        print("schema drift:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1

    cols = sum(len(v) for v in mysql.values())
    print(f"schema parity: ok ({len(mysql)} tables, {cols} columns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
