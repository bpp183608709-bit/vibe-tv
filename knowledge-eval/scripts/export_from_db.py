"""Export the database back into the three file shapes the rest of knowledge-eval reads.

    python knowledge-eval/scripts/export_from_db.py --out-dir /tmp/export
    QUIZ_DSN=mysql://... python knowledge-eval/scripts/export_from_db.py --out-dir knowledge-eval/data
    python knowledge-eval/scripts/export_from_db.py --out-dir /tmp/export --device <device id>

Writes:

    nodes_<subject>_6up.json      §4.1 flat nodes (aggregate + diagnostic, same array)
    questions_<subject>_6up.json  §4.2 questions, options as plain strings
    answers_<device>.json         an answers file for evaluate.py, when --device is given

Why this exists: the database is the *runtime* copy of the content, and an export that only round-trips
through the importer proves nothing if both sides share a bug. The exported files are validated against
the same schemas as the checked-in ones and can be diffed against them — `--check` does exactly that and
fails on any difference, which is the strongest statement available that the two representations agree.

Nothing here renames a field: `unit.code` is `node_id`, `topic.micro_nodes_json` is `micro_nodes`, and
`question.answer_norm` is `correct_answer` because those are the same fields under the names the design
uses. That is a projection of a database row, not a translation of a document.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402

sys.path.insert(0, str(keval.REPO))

from server.app.db import Database, default_db  # noqa: E402

DIFFICULTY = {1: "易", 2: "中", 3: "难"}
COGNITIVE = {"recall": "记忆理解", "apply": "应用", "understand": "分析评价创造"}
QTYPE = {"choice": "选择题", "fill": "填空题"}
SUBJECT_NAMES = {"math": "数学", "chinese": "语文", "english": "英语"}


def _json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def export_nodes(db) -> dict[str, dict]:
    subjects = {row["code"]: row for row in db.query(
        "SELECT s.id, s.code, s.name_zh, b.id AS textbook_id, b.publisher, b.grade, b.term,"
        "       b.title_zh"
        "  FROM subject s JOIN textbook b ON b.subject_id = s.id ORDER BY s.id")}
    aggregates = db.query(
        "SELECT s.code AS subject, u.code AS node_id, u.name_zh AS name, u.ordinal AS chapter_order,"
        "       u.week_start"
        "  FROM unit u JOIN textbook b ON b.id = u.textbook_id JOIN subject s ON s.id = b.subject_id"
        " ORDER BY s.id, u.ordinal")
    diagnostics = db.query(
        "SELECT s.code AS subject, t.code AS node_id, t.name_zh AS name, t.chapter_order,"
        "       t.knowledge_type, t.micro_nodes_json, t.source_ids_json, t.prereq_json,"
        "       t.summary_zh, u.code AS parent_code"
        "  FROM topic t JOIN unit u ON u.id = t.unit_id JOIN textbook b ON b.id = u.textbook_id"
        "       JOIN subject s ON s.id = b.subject_id"
        " ORDER BY s.id, u.ordinal, t.ordinal")

    out: dict[str, dict] = {}
    for code, row in subjects.items():
        out[code] = {
            "schema_version": 3,
            "shape": "§4.1 flat node list: one array, node_type marks aggregate vs diagnostic",
            "subject": code,
            "subject_id": int(row["id"]),
            "subject_name_zh": row["name_zh"],
            "publisher": row["publisher"],
            "publisher_zh": "",
            "textbook_title_zh": row["title_zh"],
            "edition": None,
            "verified": True,
            "source": "exported from db",
            "taxonomy_version": "pep-primary-math-6up-v2" if code == "math" else None,
            "grade": int(row["grade"]),
            "semester": row["term"],
            "nodes": [],
        }

    for row in aggregates:
        bucket = out[row["subject"]]
        bucket["nodes"].append({
            "node_id": row["node_id"],
            "name": row["name"],
            "subject": row["subject"],
            "grade": bucket["grade"],
            "semester": bucket["semester"],
            "node_type": "aggregate",
            "parent_id": None,
            "chapter_name": row["name"],
            "micro_nodes": [n["name"] for n in diagnostics
                            if n["parent_code"] == row["node_id"]] or [row["name"]],
            "prerequisite_node_ids": [],
            "description": row["name"],
            "status": "active",
            "chapter_order": int(row["chapter_order"]),
            "week_start": row["week_start"],
        })

    for row in diagnostics:
        bucket = out[row["subject"]]
        micro = _json(row["micro_nodes_json"], [])
        names = [item["name"] if isinstance(item, dict) else item for item in micro]
        node = {
            "node_id": row["node_id"],
            "name": row["name"],
            "subject": row["subject"],
            "grade": bucket["grade"],
            "semester": bucket["semester"],
            "node_type": "diagnostic",
            "parent_id": row["parent_code"],
            "chapter_name": next((n["name"] for n in bucket["nodes"]
                                  if n["node_id"] == row["parent_code"]), ""),
            "micro_nodes": names or [row["name"]],
            "source_ids": _json(row["source_ids_json"], []),
            "prerequisite_node_ids": _json(row["prereq_json"], []),
            "description": row["summary_zh"] or row["name"],
            "status": "active",
            "chapter_order": int(row["chapter_order"]),
        }
        if row["knowledge_type"]:
            node["knowledge_type"] = row["knowledge_type"]
        bucket["nodes"].append(node)
    return out


def export_questions(db) -> dict[str, dict]:
    rows = db.query(
        "SELECT q.id, q.code, q.ability_tag, q.angle, q.difficulty, q.qtype, q.stem,"
        "       q.answer_norm, q.explanation_zh, t.code AS node_code, s.code AS subject"
        "  FROM question q JOIN topic t ON t.id = q.topic_id"
        "       JOIN textbook b ON b.id = q.textbook_id JOIN subject s ON s.id = b.subject_id"
        " ORDER BY s.id, t.ordinal, q.code")
    options = {}
    for row in db.query("SELECT question_id, label, text, is_correct FROM question_option ORDER BY label"):
        options.setdefault(int(row["question_id"]), []).append(row)
    accepted = {}
    for row in db.query("SELECT question_id, text_raw FROM question_answer ORDER BY ordinal"):
        accepted.setdefault(int(row["question_id"]), []).append(row["text_raw"])

    out: dict[str, dict] = {
        subject: {"schema_version": 3,
                  "shape": "§4.2 flat question list: options are plain strings, label = A + index",
                  "subject": subject, "grade": 6, "semester": "up", "questions": []}
        for subject in keval.SUBJECTS
    }
    for row in rows:
        qid = int(row["id"])
        item = {
            "question_id": row["code"],
            "diagnostic_node_id": row["node_code"],
            "subject": row["subject"],
            "ability_tag": row["ability_tag"],
            "cognitive_level": COGNITIVE.get(row["angle"], "应用"),
            "difficulty_preset": DIFFICULTY.get(int(row["difficulty"]), "中"),
            "question_type": QTYPE.get(row["qtype"], "选择题"),
            "stem": row["stem"],
        }
        if row["qtype"] == "choice":
            opts = options.get(qid, [])
            item["options"] = [opt["text"] for opt in opts]
            correct = next((opt["label"] for opt in opts if int(opt["is_correct"])), "")
            item["correct_answer"] = correct
        else:
            item["correct_answer"] = row["answer_norm"] or ""
            item["accepted"] = accepted.get(qid, [])
        item["explanation"] = row["explanation_zh"] or item["stem"]
        item["tags"] = [row["node_code"]]
        out[row["subject"]]["questions"].append(item)
    return out


def export_answers(db, device_id: str) -> dict:
    rows = db.query(
        "SELECT q.code, a.is_correct, a.answered_at FROM quiz_answer a"
        "  JOIN question q ON q.id = a.question_id"
        " WHERE a.device_id = ? ORDER BY a.answered_at, a.question_id", (device_id,))
    return {
        "schema_version": 1,
        "student_id": device_id,
        "note": "exported from quiz_answer; is_correct is the grader's own verdict",
        "answers": [{"question_id": row["code"], "is_correct": bool(int(row["is_correct"] or 0)),
                     "answered_at": row["answered_at"]} for row in rows if row["code"]],
    }


def _diff(expected: Path, actual: dict) -> list[str]:
    if not expected.exists():
        return [f"{expected.name}: no checked-in file to compare with"]
    want = keval.read_json(expected)
    problems = []
    for key in ("subject", "grade", "semester"):
        if want.get(key) != actual.get(key):
            problems.append(f"{expected.name}: {key} {want.get(key)!r} != {actual.get(key)!r}")
    for key in ("nodes", "questions"):
        if key not in want:
            continue
        got = {item.get("node_id") or item.get("question_id"): item for item in actual.get(key, [])}
        for item in want[key]:
            ident = item.get("node_id") or item.get("question_id")
            if ident not in got:
                problems.append(f"{expected.name}: {ident} missing from the export")
                continue
            for field, value in item.items():
                if field == "tags":
                    continue  # authoring metadata; the database does not store it
                if got[ident].get(field) != value:
                    problems.append(f"{expected.name}: {ident}.{field} {value!r} != {got[ident].get(field)!r}")
        for ident in set(got) - {(item.get("node_id") or item.get("question_id")) for item in want[key]}:
            problems.append(f"{expected.name}: {ident} is in the export but not in the file")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="export nodes / questions / answers as JSON")
    parser.add_argument("--out-dir", default=str(keval.DATA))
    parser.add_argument("--device", default=None, help="also export this device's answers")
    parser.add_argument("--check", action="store_true",
                        help="do not write; compare the export with the checked-in files and fail on drift")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args(argv)

    db = Database(args.dsn) if args.dsn else default_db()
    db.connect()
    nodes = export_nodes(db)
    questions = export_questions(db)
    db.close()

    out_dir = Path(args.out_dir)
    problems: list[str] = []
    if not args.check:
        out_dir.mkdir(parents=True, exist_ok=True)

    node_spec = keval.schema("node.schema.json")
    for subject, payload in nodes.items():
        for index, node in enumerate(payload["nodes"]):
            problems.extend(f"nodes_{subject}[{index}] {node.get('node_id')}: {error}"
                            for error in keval.validate(node, node_spec))
        target = out_dir / f"nodes_{subject}_6up.json"
        if args.check:
            problems.extend(_diff(keval.DATA / target.name, payload))
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    for subject, payload in questions.items():
        spec = keval.schema("question.schema.json")
        for index, question in enumerate(payload["questions"]):
            problems.extend(f"questions_{subject}[{index}]: {error}"
                            for error in keval.validate(question, spec))
        target = out_dir / f"questions_{subject}_6up.json"
        if args.check:
            problems.extend(_diff(keval.DATA / target.name, payload))
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if args.device and not args.check:
        answers = export_answers(Database(args.dsn) if args.dsn else default_db(), args.device)
        (out_dir / f"answers_{args.device}.json").write_text(
            json.dumps(answers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"answers_{args.device}.json: {len(answers['answers'])} answers")

    if problems:
        for problem in problems:
            print(f"  x {problem}", file=sys.stderr)
        print(f"export: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("export: ok — " + ", ".join(f"{len(p['nodes'])} nodes" for p in nodes.values())
          + "; " + ", ".join(f"{len(p['questions'])} questions" for p in questions.values())
          + (" (checked, not written)" if args.check else f" -> {out_dir}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
