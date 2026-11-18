"""Validate the raw data files against the schemas. No renaming, no reshaping.

    python knowledge-eval/scripts/check_schema.py                      # all three subjects
    python knowledge-eval/scripts/check_schema.py --nodes data/nodes_math_6up.json
    python knowledge-eval/scripts/check_schema.py --report /tmp/report.json

The point of this script is what it does *not* do: it hands the object read from disk straight to
`keval.validate`. If a field is spelled `name_zh` instead of `name`, or `micro_nodes` is a list of
objects instead of strings, the check fails here — it cannot be papered over by a translation layer,
because there is no place to put one.

Exit code 1 on any violation, one line per problem on stderr.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402


def check_nodes(paths: list[str] | None) -> tuple[list[str], int]:
    errors: list[str] = []
    spec = keval.schema("node.schema.json")
    count = 0
    for path, payload in keval.load_nodes(paths):
        for index, node in enumerate(payload.get("nodes", [])):
            label = f"{path.name}[{index}] {node.get('node_id', '?')}"
            errors.extend(f"{label}: {e}" for e in keval.validate(node, spec))
            count += 1
    return errors, count


def check_questions(paths: list[str] | None) -> tuple[list[str], int]:
    errors: list[str] = []
    spec = keval.schema("question.schema.json")
    count = 0
    for path, payload in keval.load_questions(paths):
        for index, question in enumerate(payload.get("questions", [])):
            label = f"{path.name}[{index}] {question.get('question_id', '?')}"
            errors.extend(f"{label}: {e}" for e in keval.validate(question, spec))
            count += 1
    return errors, count


def check_report(path: str) -> list[str]:
    spec = keval.schema("report.schema.json")
    return keval.validate(keval.read_json(path), spec)


def _selftest() -> None:
    for name in ("node.schema.json", "question.schema.json", "report.schema.json"):
        spec = keval.schema(name)
        stack = [spec]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            unknown = set(node) - keval.SUPPORTED_KEYWORDS
            if unknown:
                raise SystemExit(f"{name}: unsupported keyword(s) {sorted(unknown)}")
            stack.extend(node.get("properties", {}).values())
            for item in node.get("properties", {}).values():
                if isinstance(item.get("items"), dict):
                    stack.append(item["items"])
            if isinstance(node.get("items"), dict):
                stack.append(node["items"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="validate node / question / report JSON")
    parser.add_argument("--nodes", nargs="*", default=None, help="node files (default: all three)")
    parser.add_argument("--questions", nargs="*", default=None, help="question files")
    parser.add_argument("--report", default=None, help="also validate one report JSON")
    args = parser.parse_args(argv)

    _selftest()
    node_errors, nodes = check_nodes(args.nodes)
    question_errors, questions = check_questions(args.questions)
    report_errors = check_report(args.report) if args.report else []
    errors = node_errors + question_errors + report_errors

    print(f"schema: {nodes} nodes, {questions} questions"
          + (", 1 report" if args.report else "") + " checked against knowledge-eval/schemas")
    if errors:
        for error in errors:
            print(f"  x {error}", file=sys.stderr)
        print(f"schema: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("schema: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
