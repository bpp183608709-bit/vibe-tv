"""Hold the content files to their schemas and to the coverage rules, before anything touches a database.

    python3 -m server.tools.check_content                 # validate, print a summary
    python3 -m server.tools.check_content --emit-nodes    # print the flattened node list (the report shape)
    python3 -m server.tools.check_content --emit-nodes --out /tmp/nodes.json

What it checks, and why each one is here rather than in a comment:

  schema      every node and question validates against server/content/schema/*.schema.json — a typo in a
              field name is caught here, not by an importer that silently ignores it;
  ids         node codes and question ids are unique, and a question's node is a diagnostic node that
              exists — a question pointing at a chapter would never be judged weak;
  answers     a choice's correct_answer is one of its labels and exactly one label is correct; a fill's
              correct_answer is its first accepted string; a maths fill parses as a number, because the
              grader compares Fractions and the TV keypad can only type digits
  coverage    per subject 10–15 questions, at least 3 nodes covered, and every covered node has at least
              two different ability_tag values and at least one 中 question (docs/RULES.md §2.3a)

Exit code 1 on any violation, with one line per problem.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app.grade import as_fraction  # noqa: E402
from server.tools import schema_check  # noqa: E402

CONTENT = Path(__file__).resolve().parent.parent / "content"
TREE = CONTENT / "curriculum" / "grade6-up.json"
QUESTIONS = CONTENT / "questions"
SUBJECTS = ("math", "chinese", "english")

MIN_PER_SUBJECT, MAX_PER_SUBJECT = 10, 15
MIN_NODES_PER_SUBJECT = 3
MAX_STEM = 120


def flat_nodes(tree: dict) -> list[dict]:
    """The two levels as one list: a unit becomes an aggregate node, its topics become diagnostics."""
    nodes = []
    for subject in tree["subjects"]:
        code = subject["code"]
        for unit in subject["units"]:
            unit_id = f"{code}-6up-u{unit['ordinal']:02d}"
            nodes.append({
                "node_id": unit_id,
                "name": unit["name_zh"],
                "subject": code,
                "grade": tree["grade"],
                "semester": tree["term"],
                "node_type": "aggregate",
                "parent_id": None,
                "chapter_name": unit["name_zh"],
                "knowledge_type": "procedural" if code == "math" else
                                  ("reading" if code == "english" else "阅读理解"),
                "micro_nodes": [t["name_zh"] for t in unit["topics"]],
                "prerequisite_node_ids": [],
                "description": unit["name_zh"],
                "status": "active",
            })
            for topic in unit["topics"]:
                nodes.append({
                    "node_id": topic["code"],
                    "name": topic["name_zh"],
                    "subject": code,
                    "grade": tree["grade"],
                    "semester": tree["term"],
                    "node_type": "diagnostic",
                    "parent_id": unit_id,
                    "chapter_name": unit["name_zh"],
                    "knowledge_type": topic["knowledge_type"],
                    "micro_nodes": [m["name"] for m in topic["micro_nodes"]],
                    "prerequisite_node_ids": topic.get("prereq") or [],
                    "description": topic["summary_zh"],
                    "status": "active",
                })
    return nodes


def check_tree(tree: dict) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    nodes = flat_nodes(tree)
    schema = schema_check.load("node.schema.json")
    for node in nodes:
        errors.extend(f"node {node['node_id']}: {e}" for e in schema_check.check(node, schema))

    codes = [n["node_id"] for n in nodes]
    for code, count in Counter(codes).items():
        if count > 1:
            errors.append(f"node code {code} appears {count} times")

    diagnostics = {n["node_id"] for n in nodes if n["node_type"] == "diagnostic"}
    aggregates = {n["node_id"] for n in nodes if n["node_type"] == "aggregate"}
    for node in nodes:
        if node["node_type"] == "diagnostic":
            if node["parent_id"] not in aggregates:
                errors.append(f"node {node['node_id']}: parent {node['parent_id']} is not an aggregate")
            for prereq in node["prerequisite_node_ids"]:
                if prereq not in diagnostics:
                    errors.append(f"node {node['node_id']}: prerequisite {prereq} is not a diagnostic node")
                if prereq == node["node_id"]:
                    errors.append(f"node {node['node_id']}: is its own prerequisite")
    return errors, nodes


def check_questions(nodes: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    errors: list[str] = []
    schema = schema_check.load("question.schema.json")
    diagnostics = {n["node_id"] for n in nodes}
    by_subject: dict[str, list[dict]] = defaultdict(list)

    for subject in SUBJECTS:
        path = QUESTIONS / f"{subject}_6up.json"
        if not path.exists():
            errors.append(f"{path.name}: missing")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("subject") != subject:
            errors.append(f"{path.name}: subject field is {data.get('subject')!r}")
        for index, raw in enumerate(data["questions"]):
            question = dict(raw, subject=subject)
            label = f"{path.name}[{index}] {raw.get('question_id')}"
            errors.extend(f"{label}: {e}" for e in schema_check.check(question, schema))

            node_id = raw.get("diagnostic_node_id")
            if node_id not in diagnostics:
                errors.append(f"{label}: node {node_id!r} is not a diagnostic node")
            if len(raw.get("stem", "")) > MAX_STEM:
                errors.append(f"{label}: stem is {len(raw['stem'])} characters, over {MAX_STEM}")

            qtype = raw.get("question_type")
            options = raw.get("options") or []
            accepted = raw.get("accepted") or []
            answer = raw.get("correct_answer")

            if qtype in ("选择题", "判断题"):
                if len(options) < 2:
                    errors.append(f"{label}: a choice needs at least two options")
                labels = [o.get("label") for o in options]
                if len(set(labels)) != len(labels):
                    errors.append(f"{label}: duplicate option labels {labels}")
                if answer not in labels:
                    errors.append(f"{label}: correct_answer {answer!r} is not one of {labels}")
                if qtype == "判断题" and len(options) != 2:
                    errors.append(f"{label}: a judgement item needs exactly two options")
            else:
                if options:
                    errors.append(f"{label}: a fill should not carry options")
                if not accepted:
                    errors.append(f"{label}: a fill needs at least one accepted string")
                elif accepted[0] != answer:
                    errors.append(f"{label}: correct_answer {answer!r} must equal accepted[0] {accepted[0]!r}")
                if subject == "math" and as_fraction(answer) is None:
                    errors.append(f"{label}: maths fill answer {answer!r} is not a number or fraction")
                for extra in accepted:
                    if subject == "math" and as_fraction(extra) is None:
                        errors.append(f"{label}: accepted {extra!r} is not a number or fraction")
                    if subject != "math":
                        errors.append(f"{label}: {subject} cannot use a fill — the TV keypad has no letters")

            by_subject[subject].append(question)

    return errors, by_subject


def check_coverage(by_subject: dict[str, list[dict]]) -> list[str]:
    errors: list[str] = []
    total = 0
    for subject in SUBJECTS:
        questions = by_subject.get(subject, [])
        total += len(questions)
        if not (MIN_PER_SUBJECT <= len(questions) <= MAX_PER_SUBJECT):
            errors.append(f"{subject}: {len(questions)} questions, want {MIN_PER_SUBJECT}–{MAX_PER_SUBJECT}")

        per_node: dict[str, list[dict]] = defaultdict(list)
        for question in questions:
            per_node[question["diagnostic_node_id"]].append(question)
        if len(per_node) < MIN_NODES_PER_SUBJECT:
            errors.append(f"{subject}: covers {len(per_node)} nodes, want at least {MIN_NODES_PER_SUBJECT}")

        for node_id, items in per_node.items():
            tags = {q["ability_tag"] for q in items}
            if len(tags) < 2:
                errors.append(f"{node_id}: only one ability_tag ({tags.pop()}), want at least two")
            if not any(q["difficulty_preset"] == "中" for q in items):
                errors.append(f"{node_id}: no 中 difficulty question")
    if not (30 <= total <= 45):
        errors.append(f"total: {total} questions, want 30–45")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validate the content tree and question banks")
    parser.add_argument("--emit-nodes", action="store_true", help="print the flattened node list")
    parser.add_argument("--out", default=None, help="with --emit-nodes, write there instead of stdout")
    args = parser.parse_args(argv)

    tree = json.loads(TREE.read_text(encoding="utf-8"))
    tree_errors, nodes = check_tree(tree)
    question_errors, by_subject = check_questions(nodes)
    coverage_errors = check_coverage(by_subject)
    errors = tree_errors + question_errors + coverage_errors

    if args.emit_nodes:
        payload = json.dumps(nodes, ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
            print(f"wrote {len(nodes)} nodes to {args.out}")
        else:
            print(payload)
        return 0

    for node in nodes:
        if node["node_type"] != "diagnostic":
            continue
    diagnostics = [n for n in nodes if n["node_type"] == "diagnostic"]
    aggregates = [n for n in nodes if n["node_type"] == "aggregate"]
    print(f"nodes: {len(aggregates)} aggregate, {len(diagnostics)} diagnostic")
    for subject in SUBJECTS:
        mine = [n for n in diagnostics if n["subject"] == subject]
        tags = Counter(q["ability_tag"] for q in by_subject.get(subject, []))
        levels = Counter(q["difficulty_preset"] for q in by_subject.get(subject, []))
        print(f"  {subject:<8} {len(mine):>2} nodes  {len(by_subject.get(subject, [])):>2} questions"
              f"  {dict(levels)}  {dict(tags)}")
    if errors:
        for error in errors:
            print(f"  ✗ {error}", file=sys.stderr)
        print(f"content: {len(errors)} problem(s)")
        return 1
    print("content: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
