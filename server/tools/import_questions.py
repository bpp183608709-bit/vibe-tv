"""Load the hand-written question banks into the database.

    python3 -m server.tools.import_questions --dsn sqlite:server/local/tv1.sqlite3
    QUIZ_DSN=mysql://... python3 -m server.tools.import_questions

Reads server/content/questions/{math,chinese,english}_6up.json, resolves each `diagnostic_node_id`
(a node `code`, not an id) against the imported tree, and writes question + question_option /
question_answer rows.

Idempotent on (topic_id, template_key='hand', param_hash=sha256(question_id)[:16]): a question edited in
the file is updated in place, options and accepted answers are rewritten, so the file stays the source.
The param_hash is derived from the question_id rather than from the content on purpose — editing a stem
must not create a second row beside the one a child has already answered.

Two derived columns, and the mapping between the two vocabularies:

  difficulty   易→1 中→2 难→3
  angle        记忆理解→recall, 应用→apply, 分析评价创造→understand. `angle` is what the picker's index
               was built for and stays a three-value ENUM; `ability_tag` carries the finer label.

verify_status is 'manual_ok': a human wrote these, and the importer is not a checker. Anything a later
pipeline generates must not claim that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.db import Database, default_db  # noqa: E402

QUESTIONS = Path(__file__).resolve().parent.parent / "content" / "questions"
SUBJECTS = ("math", "chinese", "english")

DIFFICULTY = {"易": 1, "中": 2, "难": 3}
ANGLE = {"记忆理解": "recall", "应用": "apply", "分析评价创造": "understand"}
QTYPE = {"选择题": "choice", "判断题": "choice", "填空题": "fill"}


def param_hash(question_id: str) -> str:
    return hashlib.sha256(question_id.encode("utf-8")).hexdigest()[:16]


def import_file(db: Database, path: Path) -> dict[str, int]:
    stats = {"created": 0, "updated": 0, "options": 0, "answers": 0, "skipped": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    subject_code = data["subject"]

    for raw in data["questions"]:
        node = db.query_one(
            "SELECT t.id AS topic_id, t.unit_id, u.textbook_id FROM topic t"
            " JOIN unit u ON u.id = t.unit_id WHERE t.code = ?",
            (raw["diagnostic_node_id"],),
        )
        if node is None:
            # check_content already refuses this; reaching here means the tree was not imported yet.
            print(f"  skipped: {raw['question_id']} — node {raw['diagnostic_node_id']} not in the database",
                  file=sys.stderr)
            stats["skipped"] += 1
            continue

        qtype = QTYPE[raw["question_type"]]
        accepted = raw.get("accepted") or []
        payload = (
            int(node["textbook_id"]),
            int(node["unit_id"]),
            int(node["topic_id"]),
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

        existing = db.query_one(
            "SELECT id FROM question WHERE topic_id = ? AND template_key = 'hand' AND param_hash = ?",
            (int(node["topic_id"]), payload[-1]),
        )
        if existing is None:
            question_id = db.insert(
                "INSERT INTO question (textbook_id, unit_id, topic_id, angle, ability_tag, difficulty,"
                " qtype, stem, stem_len, answer_norm, explanation_zh, verify_status, source,"
                " template_key, param_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
            stats["created"] += 1
        else:
            question_id = int(existing["id"])
            db.execute(
                "UPDATE question SET textbook_id = ?, unit_id = ?, topic_id = ?, angle = ?, ability_tag = ?,"
                " difficulty = ?, qtype = ?, stem = ?, stem_len = ?, answer_norm = ?, explanation_zh = ?,"
                " verify_status = ?, source = ?, template_key = ?, param_hash = ? WHERE id = ?",
                (*payload, question_id),
            )
            stats["updated"] += 1

        db.execute("DELETE FROM question_option WHERE question_id = ?", (question_id,))
        db.execute("DELETE FROM question_answer WHERE question_id = ?", (question_id,))

        if qtype == "choice":
            for option in raw["options"]:
                db.execute(
                    "INSERT INTO question_option (question_id, label, text, is_correct) VALUES (?, ?, ?, ?)",
                    (question_id, option["label"], option["text"],
                     1 if option["label"] == raw["correct_answer"] else 0),
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

    print(f"  {subject_code}: {stats}")
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the hand-written question banks.")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--dir", default=str(QUESTIONS))
    args = parser.parse_args(argv)

    db = Database(args.dsn) if args.dsn else default_db()
    if args.dsn:
        db.connect(init_schema=True)
    total = {"created": 0, "updated": 0, "options": 0, "answers": 0, "skipped": 0}
    for subject in SUBJECTS:
        path = Path(args.dir) / f"{subject}_6up.json"
        if not path.exists():
            print(f"  missing: {path}", file=sys.stderr)
            continue
        for key, value in import_file(db, path).items():
            total[key] += value
    print("questions: ok — " + ", ".join(f"{v} {k}" for k, v in total.items() if v))
    return 1 if total["skipped"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
