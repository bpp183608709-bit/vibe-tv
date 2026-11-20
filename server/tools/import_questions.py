"""Load the hand-written question banks into the database.

    python3 -m server.tools.import_questions --dsn sqlite:server/local/tv1.sqlite3
    QUIZ_DSN=mysql://... python3 -m server.tools.import_questions

Reads knowledge-eval/data/questions_{math,chinese,english}_6up.json, resolves each `diagnostic_node_id`
(a node_id, not an id) against the imported nodes, and writes question + question_option /
question_answer rows.

`options` in the file are plain strings: label of options[i] is 'A' + i, which is what the file's
`correct_answer` names. The row shape the TV downloads (label + text) is built here, once, instead of
being demanded from every author and every exporter.

`unit_id` is written as NULL. A question belongs to a diagnostic node; the chapter is reached through
`topic.unit_id`, and the picker no longer needs the direct pointer (topic.chapter_order carries the
ordinal it used to read from `unit`).

Idempotent on `code` (the question_id from the file): a question edited in the file is updated in place,
options and accepted answers are rewritten, so the file stays the source. A row whose `code` is still
NULL — anything written before that column existed — is matched by its dedup key
`(template_key, param_hash)` and adopts the file's code, so re-importing an old database updates rows
instead of colliding with them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.db import Database, default_db  # noqa: E402

DATA = Path(__file__).resolve().parent.parent.parent / "knowledge-eval" / "data"
SUBJECTS = ("math", "chinese", "english")

DIFFICULTY = {"易": 1, "中": 2, "难": 3}
ANGLE = {"记忆理解": "recall", "应用": "apply", "分析评价创造": "understand"}
QTYPE = {"选择题": "choice", "判断题": "choice", "填空题": "fill"}


def param_hash(question_id: str) -> str:
    return hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:16]


def label_of(index: int) -> str:
    return chr(ord("A") + index)


def import_file(db: Database, path: Path) -> dict[str, int]:
    stats = {"created": 0, "updated": 0, "options": 0, "answers": 0, "skipped": 0}
    data = json.loads(path.read_text(encoding="utf-8"))

    for raw in data["questions"]:
        node = db.query_one(
            "SELECT t.id AS topic_id, t.code AS node_code, u.textbook_id, b.subject_id"
            " FROM topic t JOIN unit u ON u.id = t.unit_id"
            " JOIN textbook b ON b.id = u.textbook_id WHERE t.code = ?",
            (raw["diagnostic_node_id"],),
        )
        if node is None:
            # check_content already refuses this; reaching here means the nodes were not imported yet.
            print(f"  skipped: {raw['question_id']} — node {raw['diagnostic_node_id']} not in the database",
                  file=sys.stderr)
            stats["skipped"] += 1
            continue

        qtype = QTYPE[raw["question_type"]]
        accepted = raw.get("accepted") or []
        payload = (
            int(node["textbook_id"]),
            int(node["topic_id"]),
            raw["question_id"],
            int(node["subject_id"]),
            ANGLE[raw["cognitive_level"]],
            raw["ability_tag"],
            DIFFICULTY[raw["difficulty_preset"]],
            qtype,
            raw["stem"],
            len(raw["stem"]),
            accepted[0] if qtype == "fill" and accepted else None,
            raw.get("explanation"),
            "manual_ok",
            "manual",
            "hand",
            param_hash(raw["question_id"]),
        )

        existing = db.query_one("SELECT id FROM question WHERE code = ?", (raw["question_id"],))
        if existing is None:
            # Rows written before `question.code` existed carry NULL there: the 2026-11 migration adds
            # the column but cannot backfill it (param_hash is a hash of the old name, not the name).
            # Matching on `code` alone therefore treats every legacy row as new and the INSERT then
            # dies on uk_question_dedup (topic_id, template_key, param_hash) — on a populated database
            # only, which is why the empty-database run never saw it. The dedup key *is* "this is the
            # same question", so match on it instead and let the update below adopt the file's code.
            # The row keeps its id, and every quiz_answer that points at it stays attached.
            existing = db.query_one(
                "SELECT id FROM question WHERE template_key = ? AND param_hash = ?",
                ("hand", param_hash(raw["question_id"])),
            )
        if existing is None:
            question_id = db.insert(
                "INSERT INTO question (textbook_id, topic_id, code, subject_id, angle, ability_tag,"
                " difficulty, qtype, stem, stem_len, answer_norm, explanation_zh, verify_status,"
                " source, template_key, param_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
            stats["created"] += 1
        else:
            question_id = int(existing["id"])
            db.execute(
                "UPDATE question SET textbook_id = ?, topic_id = ?, code = ?, subject_id = ?, angle = ?,"
                " ability_tag = ?, difficulty = ?, qtype = ?, stem = ?, stem_len = ?, answer_norm = ?,"
                " explanation_zh = ?, verify_status = ?, source = ?, template_key = ?, param_hash = ?"
                " WHERE id = ?",
                (*payload, question_id),
            )
            stats["updated"] += 1

        db.execute("DELETE FROM question_option WHERE question_id = ?", (question_id,))
        db.execute("DELETE FROM question_answer WHERE question_id = ?", (question_id,))
        db.execute("DELETE FROM question_topic WHERE question_id = ?", (question_id,))
        db.execute(
            "INSERT INTO question_topic (question_id, topic_id, is_primary) VALUES (?, ?, 1)",
            (question_id, int(node["topic_id"])),
        )

        if qtype == "choice":
            for index, text in enumerate(raw["options"]):
                db.execute(
                    "INSERT INTO question_option (question_id, label, text, is_correct) VALUES (?, ?, ?, ?)",
                    (question_id, label_of(index), text,
                     1 if label_of(index) == raw["correct_answer"] else 0),
                )
                stats["options"] += 1
        else:
            for ordinal, text in enumerate(accepted):
                db.execute(
                    "INSERT INTO question_answer (question_id, ordinal, text_raw, cmp_kind)"
                    " VALUES (?, ?, ?, 'numeric')",
                    (question_id, ordinal, text),
                )
                stats["answers"] += 1

    print(f"  {data['subject']}: {stats}")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the hand-written question banks.")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--dir", default=str(DATA))
    args = parser.parse_args(argv)

    db = Database(args.dsn) if args.dsn else default_db()
    if args.dsn:
        db.connect(init_schema=True)
    total = {"created": 0, "updated": 0, "options": 0, "answers": 0, "skipped": 0}
    for subject in SUBJECTS:
        path = Path(args.dir) / f"questions_{subject}_6up.json"
        if not path.exists():
            print(f"  missing: {path}", file=sys.stderr)
            continue
        for key, value in import_file(db, path).items():
            total[key] += value
    print("questions: ok — " + ", ".join(f"{v} {k}" for k, v in total.items() if v))
    return 1 if total["skipped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
