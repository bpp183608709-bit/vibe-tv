"""Two-way content check: the nodes against their schema, the questions against the nodes, and the
micro-point snapshot against the nodes that claim to cover it.

    python knowledge-eval/scripts/check_content.py
    python knowledge-eval/scripts/check_content.py --nodes data/nodes_math_6up.json
    python knowledge-eval/scripts/check_content.py --snapshot data/taxonomy/pep-math-6up-v3.json

The reverse check is the one that matters. The previous revision only asked "does every source_id a
node mentions exist in the snapshot?" — which is satisfied by a tree that mentions 37 of 38 and drops
the 38th silently. This script compares the two sets and fails on either difference:

    snapshot - referenced   the content loss this check exists to catch (missing)
    referenced - snapshot   a node citing something the snapshot does not have (extra)

The sizes are read from the files, never hardcoded: swap the snapshot and the comparison still holds.

Exit code 1 on any violation, one line per problem on stderr.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402

MIN_PER_SUBJECT = 10
MIN_NODES_PER_SUBJECT = 3

#: A per-node ceiling, not a per-subject one. The old `MAX_PER_SUBJECT = 18` was written when 语文 had
#: 14 diagnostic nodes and 12 questions: it passed only because ten of those nodes had none, and it now
#: forbids the one thing the bank exists for — a question on every node the child can be judged on.
#: Six is the 稳健档 from check_coverage_matrix (2 易 + 2 中 + 2 难); past six the extra belongs in a
#: node of its own, or the per-node window stops being well sampled.
MAX_PER_NODE = 6
MAX_STEM = 120
SUBJECTS = keval.SUBJECTS

#: A diagnostic node's name is not decoration: it is the `topic` string in the graded payload the
#: television renders (`review[].topic`). The parent page may say 薄弱/复习, the child's screen may not.
#: acceptance.py catches the word on the wire; this catches it in the data, where it is cheaper to see.
BANNED_IN_NODE_NAME = ("薄弱", "复习", "巩固", "加强", "强化", "专项", "达标", "过关")


def as_number(text: str) -> Fraction | None:
    """'7/2' and '3.5' both parse; anything else is not a maths fill answer."""
    try:
        return Fraction(text.strip())
    except (ValueError, ZeroDivisionError):
        try:
            return Fraction(str(float(text.strip())))
        except (ValueError, TypeError):
            return None


def check_nodes(payloads: list[tuple[Path, dict]]) -> tuple[list[str], list[dict]]:
    errors: list[str] = []
    spec = keval.schema("node.schema.json")
    nodes: list[dict] = []

    for path, payload in payloads:
        file_subject = payload.get("subject")
        for index, node in enumerate(payload.get("nodes", [])):
            label = f"{path.name}[{index}] {node.get('node_id', '?')}"
            errors.extend(f"{label}: {e}" for e in keval.validate(node, spec))
            if node.get("subject") != file_subject:
                errors.append(f"{label}: subject {node.get('subject')!r} differs from the file's {file_subject!r}")
            nodes.append(node)

    ids = [n["node_id"] for n in nodes]
    for node_id, count in Counter(ids).items():
        if count > 1:
            errors.append(f"node {node_id}: appears {count} times")

    aggregates = {n["node_id"] for n in nodes if n["node_type"] == "aggregate"}
    diagnostics = {n["node_id"] for n in nodes if n["node_type"] == "diagnostic"}

    for node in nodes:
        node_id = node["node_id"]
        if node["node_type"] == "aggregate":
            if node["parent_id"] is not None:
                errors.append(f"node {node_id}: an aggregate must have parent_id null, has {node['parent_id']!r}")
            if "knowledge_type" in node:
                errors.append(f"node {node_id}: an aggregate carries no knowledge_type "
                              "(it would have to be invented per subject)")
            if node["prerequisite_node_ids"]:
                errors.append(f"node {node_id}: an aggregate cannot declare prerequisites")
        else:
            if node["parent_id"] not in aggregates:
                errors.append(f"node {node_id}: parent {node['parent_id']!r} is not an aggregate")
            if "knowledge_type" not in node:
                errors.append(f"node {node_id}: a diagnostic node needs knowledge_type")
            for prereq in node["prerequisite_node_ids"]:
                if prereq == node_id:
                    errors.append(f"node {node_id}: is its own prerequisite")
                elif prereq not in diagnostics and not prereq.startswith("mt_g6u_"):
                    errors.append(f"node {node_id}: prerequisite {prereq!r} is neither a diagnostic node nor a taxonomy id")
        if node["node_type"] == "diagnostic":
            said = [word for word in BANNED_IN_NODE_NAME if word in node["name"]]
            if said:
                errors.append(f"node {node_id}: name {node['name']!r} carries {said}; that string is "
                              "rendered on the television, so it has to stay neutral")
        source_ids = node.get("source_ids")
        if source_ids:  # empty (or absent) is how a subject with no public taxonomy says so
            if len(source_ids) != len(node["micro_nodes"]):
                errors.append(f"node {node_id}: {len(source_ids)} source_ids for {len(node['micro_nodes'])} micro_nodes")
            for source_id in source_ids:
                if source_id in (None, ""):
                    errors.append(f"node {node_id}: empty source_id")
    return errors, nodes


def check_coverage(nodes: list[dict], snapshot: dict, path: Path) -> tuple[list[str], list[str]]:
    """The reverse check. Returns (errors, report lines)."""
    errors: list[str] = []
    lines: list[str] = []
    if not snapshot:
        return errors, lines

    known = {topic["id"] for topic in snapshot["topics"]}
    owner: dict[str, str] = {}
    duplicated: set[str] = set()
    for node in nodes:
        if node["node_type"] != "diagnostic":
            continue
        for source_id in node.get("source_ids") or []:
            if source_id in owner:
                duplicated.add(source_id)
            owner[source_id] = node["node_id"]

    referenced = set(owner)
    missing = sorted(known - referenced)
    extra = sorted(referenced - known)

    if missing:
        errors.append(
            f"coverage: snapshot {path.name} has {len(known)} micro points, nodes reference "
            f"{len(referenced)}; missing: {', '.join(missing)}")
    if extra:
        errors.append(
            f"coverage: nodes reference {len(extra)} micro point(s) the snapshot does not have: "
            f"{', '.join(extra)}")
    for source_id in sorted(duplicated):
        errors.append(f"coverage: {source_id} is claimed by more than one node")

    for topic in snapshot["topics"]:
        lines.append(f"  {topic['id']}  {owner.get(topic['id'], 'MISSING'):<16} {topic['name']}")
    lines.insert(0, f"coverage {path.name}: {len(referenced & known)}/{len(known)} micro points referenced")
    return errors, lines


def check_questions(payloads: list[tuple[Path, dict]], nodes: list[dict]) -> tuple[list[str], dict[str, list[dict]]]:
    errors: list[str] = []
    spec = keval.schema("question.schema.json")
    diagnostics = {n["node_id"] for n in nodes if n["node_type"] == "diagnostic"}
    by_subject: dict[str, list[dict]] = defaultdict(list)
    seen: Counter = Counter()

    for path, payload in payloads:
        file_subject = payload.get("subject")
        subjects = {q.get("subject") for q in payload.get("questions", [])}
        if subjects != {file_subject}:
            errors.append(f"{path.name}: per-question subject {sorted(subjects)} does not match the file's {file_subject!r}")
        for index, question in enumerate(payload.get("questions", [])):
            label = f"{path.name}[{index}] {question.get('question_id', '?')}"
            errors.extend(f"{label}: {e}" for e in keval.validate(question, spec))
            seen[question.get("question_id")] += 1

            node_id = question.get("diagnostic_node_id")
            if node_id not in diagnostics:
                errors.append(f"{label}: node {node_id!r} is not a diagnostic node")
            if len(question.get("stem", "")) > MAX_STEM:
                errors.append(f"{label}: stem is {len(question['stem'])} characters, over {MAX_STEM}")

            subject = question.get("subject")
            qtype = question.get("question_type")
            options = question.get("options") or []
            accepted = question.get("accepted") or []
            answer = question.get("correct_answer")

            if qtype in ("选择题", "判断题"):
                if len(options) < 2:
                    errors.append(f"{label}: a choice needs at least two options")
                if not all(isinstance(o, str) for o in options):
                    errors.append(f"{label}: options must be plain strings")
                labels = [chr(ord("A") + i) for i in range(len(options))]
                if answer not in labels:
                    errors.append(f"{label}: correct_answer {answer!r} is not one of {labels}")
                if qtype == "判断题" and len(options) != 2:
                    errors.append(f"{label}: a judgement item needs exactly two options")
                if accepted:
                    errors.append(f"{label}: a choice must not carry accepted strings")
            else:
                if options:
                    errors.append(f"{label}: a fill should not carry options")
                if not accepted:
                    errors.append(f"{label}: a fill needs at least one accepted string")
                elif accepted[0] != answer:
                    errors.append(f"{label}: correct_answer {answer!r} must equal accepted[0] {accepted[0]!r}")
                if subject != "math":
                    errors.append(f"{label}: {subject} cannot use a fill — the TV keypad has no letters")
                for text in accepted:
                    if as_number(text) is None:
                        errors.append(f"{label}: accepted {text!r} is not a number or fraction")

            by_subject[subject].append(question)

    for question_id, count in seen.items():
        if count > 1:
            errors.append(f"question {question_id}: appears {count} times")
    return errors, by_subject


def check_question_coverage(by_subject: dict[str, list[dict]], subjects=SUBJECTS) -> list[str]:
    errors: list[str] = []
    for subject in subjects:
        questions = by_subject.get(subject, [])
        if len(questions) < MIN_PER_SUBJECT:
            errors.append(f"{subject}: {len(questions)} questions, want at least {MIN_PER_SUBJECT}")
        per_node: dict[str, list[dict]] = defaultdict(list)
        for question in questions:
            per_node[question["diagnostic_node_id"]].append(question)
        if len(per_node) < MIN_NODES_PER_SUBJECT:
            errors.append(f"{subject}: covers {len(per_node)} nodes, want at least {MIN_NODES_PER_SUBJECT}")
        for node_id, items in per_node.items():
            if len(items) > MAX_PER_NODE:
                errors.append(f"{node_id}: {len(items)} questions, want at most {MAX_PER_NODE}")
            tags = {item["ability_tag"] for item in items}
            if len(tags) < 2:
                errors.append(f"{node_id}: only one ability_tag ({tags.pop()}), want at least two")
            if not any(item["difficulty_preset"] == "中" for item in items):
                errors.append(f"{node_id}: no 中 difficulty question")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="two-way content check")
    parser.add_argument("--nodes", nargs="*", default=None)
    parser.add_argument("--questions", nargs="*", default=None)
    parser.add_argument("--snapshot", default=None, help="micro-point snapshot (default: maths taxonomy)")
    parser.add_argument("--quiet", action="store_true", help="do not print the micro-point map")
    args = parser.parse_args(argv)

    node_payloads = keval.load_nodes(args.nodes)
    node_errors, nodes = check_nodes(node_payloads)

    snapshot = keval.load_taxonomy(args.snapshot) if (args.snapshot or (not args.nodes)) else {}
    coverage_errors, coverage_lines = check_coverage(nodes, snapshot, Path(args.snapshot or keval.TAXONOMY))

    question_errors, by_subject = check_questions(keval.load_questions(args.questions), nodes)
    # Only the subjects actually loaded: a reviewer checking one file must not be told the other two
    # subjects have no questions.
    present = tuple(payload["subject"] for _, payload in node_payloads)
    pool_errors = check_question_coverage(by_subject, present)
    errors = node_errors + coverage_errors + question_errors + pool_errors

    diagnostics = [n for n in nodes if n["node_type"] == "diagnostic"]
    aggregates = [n for n in nodes if n["node_type"] == "aggregate"]
    print(f"nodes: {len(aggregates)} aggregate, {len(diagnostics)} diagnostic")
    for subject in SUBJECTS:
        mine = [n for n in diagnostics if n["subject"] == subject]
        entries = by_subject.get(subject, [])
        tags = Counter(q["ability_tag"] for q in entries)
        levels = Counter(q["difficulty_preset"] for q in entries)
        print(f"  {subject:<8} {len(mine):>2} nodes  {len(entries):>2} questions  {dict(levels)}  {dict(tags)}")
    if not args.quiet:
        for line in coverage_lines:
            print(line)

    if errors:
        for error in errors:
            print(f"  x {error}", file=sys.stderr)
        print(f"content: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("content: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
