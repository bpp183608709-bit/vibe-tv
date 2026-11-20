"""Load the flat node files into the database.

    python3 -m server.tools.import_curriculum                       # knowledge-eval/data/nodes_*_6up.json
    QUIZ_DSN=mysql://... python3 -m server.tools.import_curriculum
    python3 -m server.tools.import_curriculum --dir /tmp/nodes      # any directory of node files

The files are the §4.1 flat shape: one array per subject, `node_type` says whether an entry is a chapter
(aggregate) or a diagnostic node, and every entry carries its own `parent_id` and `chapter_order`. There
is no nested tree to walk and no derived field to invent here — `check_schema.py` has already validated
the objects the importer is about to read, unchanged.

Idempotent by business key: subject code, then textbook (subject, publisher, grade, term), then
`unit.code`, then `topic.code`. Re-running updates names instead of duplicating rows, which matters
because the book gets re-cut at least once a term.

What it deliberately does **not** do: touch questions. Importing nodes never deletes a topic, because
deleting one would orphan every question and every wrong-answer count that points at it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.db import Database, default_db  # noqa: E402

DATA = Path(__file__).resolve().parent.parent.parent / "knowledge-eval" / "data"
SUBJECTS = ("math", "chinese", "english")


def _json(value):
    """JSON columns take a string on both backends; NULL stays NULL so "no micro-points" is readable."""
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=False)


def default_paths() -> list[Path]:
    return [DATA / f"nodes_{subject}_6up.json" for subject in SUBJECTS]


def load_files(directory: str | Path | None = None) -> list[dict]:
    if directory:
        return [json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(Path(directory).glob("nodes_*_6up.json"))]
    return [json.loads(path.read_text(encoding="utf-8")) for path in default_paths()]


def import_tree(db: Database, payloads: list[dict]) -> dict[str, int]:
    stats = {"subjects": 0, "textbooks": 0, "units": 0, "topics": 0, "skipped_topics": 0}

    for payload in payloads:
        code = payload["subject"]
        grade, term = payload["grade"], payload["semester"]
        subject_id = int(payload.get("subject_id") or 0)

        existing = db.query_one("SELECT id FROM subject WHERE code = ?", (code,))
        if existing is None:
            if not subject_id:
                raise SystemExit(f"{code}: no subject row and no subject_id in the file")
            db.insert(
                "INSERT INTO subject (id, code, name_zh, sort_order) VALUES (?, ?, ?, ?)",
                (subject_id, code, payload["subject_name_zh"], len(stats)),
            )
            stats["subjects"] += 1
        else:
            subject_id = int(existing["id"])
            db.execute("UPDATE subject SET name_zh = ? WHERE id = ?",
                       (payload["subject_name_zh"], subject_id))

        textbook = db.query_one(
            "SELECT id FROM textbook WHERE subject_id = ? AND publisher = ? AND grade = ? AND term = ?",
            (subject_id, payload["publisher"], grade, term),
        )
        if textbook is None:
            textbook_id = db.insert(
                "INSERT INTO textbook (subject_id, publisher, grade, term, title_zh, is_active)"
                " VALUES (?, ?, ?, ?, ?, 1)",
                (subject_id, payload["publisher"], grade, term, payload["textbook_title_zh"]),
            )
            stats["textbooks"] += 1
        else:
            textbook_id = int(textbook["id"])
            db.execute("UPDATE textbook SET title_zh = ? WHERE id = ?",
                       (payload["textbook_title_zh"], textbook_id))

        nodes = payload["nodes"]
        aggregates = [n for n in nodes if n["node_type"] == "aggregate"]
        diagnostics = [n for n in nodes if n["node_type"] == "diagnostic"]
        unit_ids: dict[str, int] = {}

        for aggregate in sorted(aggregates, key=lambda n: n["chapter_order"]):
            row = None
            if aggregate.get("node_id"):
                row = db.query_one("SELECT id FROM unit WHERE code = ?", (aggregate["node_id"],))
            if row is None:
                row = db.query_one("SELECT id FROM unit WHERE textbook_id = ? AND ordinal = ?",
                                   (textbook_id, aggregate["chapter_order"]))
            values = (textbook_id, int(aggregate["chapter_order"]), aggregate["node_id"],
                      aggregate["name"], aggregate.get("week_start"))
            if row is None:
                unit_id = db.insert(
                    "INSERT INTO unit (textbook_id, ordinal, code, name_zh, week_start)"
                    " VALUES (?, ?, ?, ?, ?)", values)
                stats["units"] += 1
            else:
                unit_id = int(row["id"])
                db.execute("UPDATE unit SET textbook_id = ?, ordinal = ?, code = ?, name_zh = ?,"
                           " week_start = ? WHERE id = ?", (*values, unit_id))
            unit_ids[aggregate["node_id"]] = unit_id

        # Ordinals are positions, and a new edition can reorder the chapters: `math-6up-u01` used to be
        # 分数乘法 and is 确定位置 in the 2024 edition, so `u01`/`u02` swap places in one import. Topics
        # are matched by `code` and then re-positioned, which means a topic can ask for a
        # (unit_id, ordinal) slot that another topic has not vacated yet. On a populated database that
        # is "Duplicate entry '17-1' for key 'topic.uk_topic'" — invisible on an empty one, because
        # there is nothing to collide with.
        #
        # So push every existing ordinal out of the way first: ordinals are tiny (1..n), +100 stays
        # inside TINYINT UNSIGNED, and the bump is injective within a unit. The loop below then owns
        # slots 1..n again. A row that vanished from the file keeps an ordinal ≥100 until it is
        # deleted, which is also how the stale report at the end tells it apart.
        db.execute(
            "UPDATE topic SET ordinal = ordinal + 100"
            " WHERE ordinal < 100 AND unit_id IN"
            " (SELECT id FROM unit WHERE textbook_id = ?)",
            (textbook_id,),
        )

        placed: set[int] = set()
        for parent_id, unit_id in unit_ids.items():
            mine = [n for n in diagnostics if n["parent_id"] == parent_id]
            for ordinal, node in enumerate(sorted(mine, key=lambda n: n["node_id"]), start=1):
                payload_values = (
                    unit_id,
                    ordinal,
                    int(node["chapter_order"]),
                    node["node_id"],
                    node["name"],
                    node["description"],
                    node.get("knowledge_type"),
                    _json(node["micro_nodes"]),
                    _json(node.get("source_ids")),
                    _json(node["prerequisite_node_ids"]),
                )
                row = db.query_one("SELECT id FROM topic WHERE code = ?", (node["node_id"],))
                if row is None:
                    # A row with no `code` predates the column. It is still "the node that used to sit
                    # at this ordinal", so look for the slot it was released from (the +100 above),
                    # not the one it holds now. `code IS NULL` is load-bearing: after the bump every
                    # row sits at ordinal+100, and without it a new node silently overwrites an
                    # unrelated old one instead of being reported stale (math-6up-d27 ate math-6up-d10).
                    row = db.query_one(
                        "SELECT id FROM topic WHERE unit_id = ? AND ordinal = ? AND code IS NULL",
                        (unit_id, ordinal + 100))
                if row is None:
                    placed.add(db.insert(
                        "INSERT INTO topic (unit_id, ordinal, chapter_order, code, name_zh, summary_zh,"
                        " knowledge_type, micro_nodes_json, source_ids_json, prereq_json)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", payload_values))
                    stats["topics"] += 1
                else:
                    db.execute(
                        "UPDATE topic SET unit_id = ?, ordinal = ?, chapter_order = ?, code = ?,"
                        " name_zh = ?, summary_zh = ?, knowledge_type = ?, micro_nodes_json = ?,"
                        " source_ids_json = ?, prereq_json = ? WHERE id = ?",
                        (*payload_values, int(row["id"])))
                    placed.add(int(row["id"]))
        # What the file no longer has, reported once every unit has been positioned. Reporting it
        # inside the unit loop gives false positives: in the 2026 edition `math-6up-d01` only moved
        # from u01 to u02, but it is still sitting in u01 when u01 is processed, so it read as deleted
        # (14 lines on the real migration, of which only 7 were). "Not placed by this file" is the
        # exact test, and it does not care whether the row was matched by code or by ordinal.
        for parent_id, unit_id in unit_ids.items():
            for row in db.query("SELECT id, code, ordinal, name_zh FROM topic WHERE unit_id = ?",
                                (unit_id,)):
                if int(row["id"]) in placed:
                    continue
                print(f"  skipped: {code} unit {parent_id} topic {row['code'] or row['ordinal']}"
                      f" ({row['name_zh']}) still in the database but gone from the file",
                      file=sys.stderr)
                stats["skipped_topics"] += 1

        orphans = [(n["node_id"], n["parent_id"]) for n in diagnostics if n["parent_id"] not in unit_ids]
        for node_id, parent_id in orphans:
            print(f"  skipped: {code} {node_id} — parent {parent_id} not in the file", file=sys.stderr)
            stats["skipped_topics"] += 1

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the flat node files.")
    parser.add_argument("--dir", default=None, help="directory holding nodes_*_6up.json")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args(argv)

    payloads = load_files(args.dir)
    if not payloads:
        print("import: no node files found", file=sys.stderr)
        return 1

    unverified = [p["subject"] for p in payloads if not p.get("verified")]
    if unverified:
        print("warning: nodes not yet verified against the printed textbook for: "
              + ", ".join(unverified), file=sys.stderr)

    db = Database(args.dsn) if args.dsn else default_db()
    if args.dsn:
        db.connect(init_schema=True)
    stats = import_tree(db, payloads)
    print("import: ok — " + ", ".join(f"{v} {k}" for k, v in stats.items() if v)
          + ("" if any(stats.values()) else "nothing to change"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
