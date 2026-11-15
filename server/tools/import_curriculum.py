"""Load the curriculum tree into the database.

    python3 -m server.tools.import_curriculum                    # default file, default DSN
    QUIZ_DSN=mysql://... python3 -m server.tools.import_curriculum

Idempotent by natural key (subject code, then textbook, then unit ordinal, then topic ordinal), so
running it twice updates names instead of duplicating rows. That matters because the tree will be
re-imported at least three times: scaffold -> OCR -> human corrections.

What it deliberately does **not** do: touch questions. Importing a tree never deletes a topic, because
deleting one would orphan every question and every wrong-answer count that points at it. A topic that
disappears from the file is reported and skipped, and removing it is a manual decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.db import Database, default_db  # noqa: E402

CURRICULUM = Path(__file__).resolve().parent.parent / "content" / "curriculum" / "grade6-up.json"


def import_tree(db: Database, tree: dict) -> dict[str, int]:
    stats = {"subjects": 0, "textbooks": 0, "units": 0, "topics": 0, "skipped_topics": 0}
    subject_ids: dict[str, int] = tree.get("_subject_ids", {})
    grade, term = tree["grade"], tree["term"]

    for subject in tree["subjects"]:
        code = subject["code"]
        subject_id = int(subject_ids[code])

        existing = db.query_one("SELECT id FROM subject WHERE code = ?", (code,))
        if existing is None:
            db.insert(
                "INSERT INTO subject (id, code, name_zh, sort_order) VALUES (?, ?, ?, ?)",
                (subject_id, code, subject["name_zh"], len(stats)),
            )
            stats["subjects"] += 1
        else:
            subject_id = int(existing["id"])
            db.execute("UPDATE subject SET name_zh = ? WHERE id = ?", (subject["name_zh"], subject_id))

        textbook = db.query_one(
            "SELECT id FROM textbook WHERE subject_id = ? AND publisher = ? AND grade = ? AND term = ?",
            (subject_id, subject["publisher"], grade, term),
        )
        if textbook is None:
            textbook_id = db.insert(
                "INSERT INTO textbook (subject_id, publisher, grade, term, title_zh, is_active)"
                " VALUES (?, ?, ?, ?, ?, 1)",
                (subject_id, subject["publisher"], grade, term, subject["textbook_title_zh"]),
            )
            stats["textbooks"] += 1
        else:
            textbook_id = int(textbook["id"])
            db.execute(
                "UPDATE textbook SET title_zh = ? WHERE id = ?",
                (subject["textbook_title_zh"], textbook_id),
            )

        for unit in subject["units"]:
            row = db.query_one(
                "SELECT id FROM unit WHERE textbook_id = ? AND ordinal = ?",
                (textbook_id, unit["ordinal"]),
            )
            if row is None:
                unit_id = db.insert(
                    "INSERT INTO unit (textbook_id, ordinal, name_zh, week_start) VALUES (?, ?, ?, ?)",
                    (textbook_id, unit["ordinal"], unit["name_zh"], unit.get("week_start")),
                )
                stats["units"] += 1
            else:
                unit_id = int(row["id"])
                db.execute(
                    "UPDATE unit SET name_zh = ?, week_start = ? WHERE id = ?",
                    (unit["name_zh"], unit.get("week_start"), unit_id),
                )

            seen_ordinals = set()
            for topic in unit["topics"]:
                seen_ordinals.add(topic["ordinal"])
                row = db.query_one(
                    "SELECT id FROM topic WHERE unit_id = ? AND ordinal = ?",
                    (unit_id, topic["ordinal"]),
                )
                if row is None:
                    db.insert(
                        "INSERT INTO topic (unit_id, ordinal, name_zh, summary_zh) VALUES (?, ?, ?, ?)",
                        (unit_id, topic["ordinal"], topic["name_zh"], topic.get("summary_zh")),
                    )
                    stats["topics"] += 1
                else:
                    db.execute(
                        "UPDATE topic SET name_zh = ?, summary_zh = ? WHERE id = ?",
                        (topic["name_zh"], topic.get("summary_zh"), int(row["id"])),
                    )

            stale = db.query(
                "SELECT id, ordinal, name_zh FROM topic WHERE unit_id = ?", (unit_id,)
            )
            for row in stale:
                if int(row["ordinal"]) not in seen_ordinals:
                    print(
                        f"  skipped: {code} unit {unit['ordinal']} topic {row['ordinal']} "
                        f"({row['name_zh']}) still in the database but gone from the file",
                        file=sys.stderr,
                    )
                    stats["skipped_topics"] += 1

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the curriculum tree.")
    parser.add_argument("--file", default=str(CURRICULUM))
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args(argv)

    tree = json.loads(Path(args.file).read_text(encoding="utf-8"))

    unverified = [s["code"] for s in tree["subjects"] if not s.get("verified")]
    if unverified:
        # Loud on purpose: questions generated against an unverified tree are only fit for P1 wiring.
        print(
            f"warning: tree not yet verified against the OCR'd textbook for: {', '.join(unverified)}",
            file=sys.stderr,
        )

    db = Database(args.dsn) if args.dsn else default_db()
    if args.dsn:
        db.connect(init_schema=True)
    stats = import_tree(db, tree)
    print(
        "import: ok — "
        + ", ".join(f"{v} {k}" for k, v in stats.items() if v)
        + ("" if any(stats.values()) else "nothing to change")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
