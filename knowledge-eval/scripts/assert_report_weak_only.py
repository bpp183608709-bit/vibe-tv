"""Fail unless a report contains only weak nodes in `diagnostics`.

    python knowledge-eval/scripts/assert_report_weak_only.py /tmp/report.json

Two properties, both of which a reviewer can check by eye as well:

  * every line in `diagnostics` is 0-未掌握 or 1-基础 — no 熟练, no 拓展, ever;
  * `weak_diagnostic_count` equals the number of lines actually printed, per chapter.

`full_view` is allowed to hold anything measured; it is the chapter view, not the weak list.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402

WEAK = ("0-未掌握", "1-基础")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: assert_report_weak_only.py REPORT.json", file=sys.stderr)
        return 2
    report = keval.read_json(argv[0])
    problems = []
    for chapter in report.get("aggregate_summary", []):
        lines = chapter.get("diagnostics", [])
        wrong = [line for line in lines if line.get("mastery_level") not in WEAK]
        if wrong:
            problems.append(
                f"{chapter.get('aggregate_node_id')}: diagnostics contains "
                + ", ".join(f"{line.get('diagnostic_node_id')}={line.get('mastery_level')}" for line in wrong))
        if int(chapter.get("weak_diagnostic_count", -1)) != len(lines):
            problems.append(f"{chapter.get('aggregate_node_id')}: weak_diagnostic_count "
                            f"{chapter.get('weak_diagnostic_count')} != {len(lines)} lines printed")
    if problems:
        for problem in problems:
            print(f"  x {problem}", file=sys.stderr)
        print("weak-only: FAIL", file=sys.stderr)
        return 1
    total = sum(len(chapter.get("diagnostics", [])) for chapter in report.get("aggregate_summary", []))
    print(f"weak-only: ok ({total} weak node(s), every one 0-未掌握 or 1-基础)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
