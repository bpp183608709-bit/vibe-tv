"""Turn a file of answers into the mastery report — no database, no server, no network.

    python knowledge-eval/scripts/evaluate.py \
      --nodes knowledge-eval/data/nodes_math_6up.json \
              knowledge-eval/data/nodes_chinese_6up.json \
              knowledge-eval/data/nodes_english_6up.json \
      --questions knowledge-eval/data/questions_math_6up.json \
                  knowledge-eval/data/questions_chinese_6up.json \
                  knowledge-eval/data/questions_english_6up.json \
      --answers knowledge-eval/tests/fixtures/sample_answers.json \
      --out /tmp/report.json

The arithmetic is the server's: this script imports `server.app.mastery` and `server.app.report`,
which are pure modules (no connection is opened by importing them). One model, two entry points —
the database one reads `mastery_event`, this one reads a JSON file, and both hand the same rows to
`report.build`.

Answers file:

    {"schema_version": 1, "student_id": "...", "period": "全部作答",
     "answers": [{"question_id": "...", "is_correct": true, "answered_at": "2026-11-01T09:00:00"}, ...]}

`is_correct` is a fact already decided by the grader, not something this script re-decides: grading a
fill needs the TV's normalisation rules, and a report is not a second grader.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402

sys.path.insert(0, str(keval.REPO))

from server.app import mastery, report as report_mod  # noqa: E402

DIFFICULTY = {"易": 1, "中": 2, "难": 3}
WINDOW = mastery.MASTERY_WINDOW


def _window_by_node(answers: list[dict], questions: dict[str, dict]) -> dict[str, list[dict]]:
    """Last ten answers per diagnostic node, oldest first, in the order they were given."""
    ordered = sorted(enumerate(answers), key=lambda pair: (str(pair[1].get("answered_at") or ""), pair[0]))
    windows: dict[str, list[dict]] = {}
    for _, answer in ordered:
        question = questions.get(str(answer.get("question_id")))
        if question is None:
            continue
        node_id = question["diagnostic_node_id"]
        row = {
            "is_correct": 1 if answer.get("is_correct") else 0,
            "weight_x10": mastery.WEIGHT_X10[DIFFICULTY[question["difficulty_preset"]]],
            "question_id": question["question_id"],
            "ability_tag": question["ability_tag"],
        }
        bucket = windows.setdefault(node_id, [])
        bucket.append(row)
        if len(bucket) > WINDOW:
            del bucket[0]
    return windows


def _abilities(window: list[dict]) -> list[dict]:
    slots: "OrderedDict[str, dict]" = OrderedDict()
    for row in window:
        slot = slots.setdefault(row["ability_tag"], {"tag": row["ability_tag"], "weight": 0, "got": 0, "sample": 0})
        slot["weight"] += row["weight_x10"]
        slot["sample"] += 1
        if row["is_correct"]:
            slot["got"] += row["weight_x10"]
    return [{"tag": slot["tag"], "sample": slot["sample"],
             "acc": round(1000 * slot["got"] / slot["weight"]) if slot["weight"] else 0}
            for slot in slots.values()]


def build_groups(nodes: list[dict], windows: dict[str, list[dict]], student_id: str) -> list[dict]:
    aggregates = [n for n in nodes if n["node_type"] == "aggregate"]
    diagnostics = [n for n in nodes if n["node_type"] == "diagnostic"]
    groups = []
    for aggregate in sorted(aggregates, key=lambda n: (n["chapter_order"], n["node_id"])):
        rows = []
        mine = [n for n in diagnostics if n["parent_id"] == aggregate["node_id"]]
        for node in sorted(mine, key=lambda n: n["node_id"]):
            window = windows.get(node["node_id"], [])
            summary = mastery.summarise(window)
            level = mastery.level_of(summary["acc_x1000"], summary["hard_right"])
            rows.append({
                "node_id": node["node_id"],
                "node_name": node["name"],
                "level": level,
                "acc": summary["acc_x1000"],
                "sample": summary["sample_count"],
                "measured": summary["sample_count"] >= mastery.MIN_SAMPLE,
                "evidence": [row["question_id"] for row in window[::-1][:3]],
                "abilities": _abilities(window),
            })
        groups.append({
            "student_id": student_id,
            "aggregate_id": aggregate["node_id"],
            "aggregate_name": aggregate["name"],
            "diagnostic_count": len(mine),
            "diagnostics": rows,
        })
    return groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="offline mastery report")
    parser.add_argument("--nodes", nargs="+", required=True)
    parser.add_argument("--questions", nargs="+", required=True)
    parser.add_argument("--answers", required=True)
    parser.add_argument("--out", default="-", help="'-' writes to stdout")
    parser.add_argument("--period", default=None, help="label printed in the report")
    parser.add_argument("--include-full-view", action="store_true",
                        help="also emit full_view (every measured node), not just the weak list")
    args = parser.parse_args(argv)

    nodes = keval.all_nodes(args.nodes)
    questions = {q["question_id"]: q for q in keval.all_questions(args.questions)}
    answers_file = keval.read_json(args.answers)
    answers = answers_file["answers"]
    student_id = answers_file.get("student_id") or "unknown"
    period = args.period or answers_file.get("period") or "全部作答"

    unknown = [a["question_id"] for a in answers if str(a.get("question_id")) not in questions]
    if unknown:
        print(f"note: {len(unknown)} answer(s) name a question outside --questions: {unknown[:5]}",
              file=sys.stderr)

    windows = _window_by_node(answers, questions)
    groups = build_groups(nodes, windows, student_id)
    scope = "all" if args.include_full_view else "weak"
    payload = report_mod.build(groups, period, scope=scope, source="files")

    errors = keval.validate(payload, keval.schema("report.schema.json"))
    if errors:
        for error in errors:
            print(f"  x {error}", file=sys.stderr)
        print(f"report: {len(errors)} schema problem(s)", file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
    weak = sum(chapter["weak_diagnostic_count"] for chapter in payload["aggregate_summary"])
    print(f"report: {len(payload['aggregate_summary'])} chapters, {weak} weak node(s), scope={scope}"
          f" -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
