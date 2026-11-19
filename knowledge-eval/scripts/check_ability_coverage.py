"""Is every (diagnostic node × declared ability line) cell ready to be judged?

The matrix in `check_coverage_matrix.py` answers the same question but writes two report files and always
exits 0 — it is a report. This is the gate: no files written, `--strict` exits non-zero when a cell
cannot produce all four stages, so a batch of generated questions can be rejected before it reaches the
database.

It reuses `ccm.analyse`, never a second copy of the arithmetic: the ceiling a cell can reach is decided
by `mastery.summarise` + `mastery.level_of` there, and two implementations of "is this cell complete"
would eventually disagree about a cell.

The unit is the cell a node declares in `ability_lines`, not all four lines of the subject. A node that
can only be asked about 理解意义 / 情境应用 / 辨析变式 has three cells, and reporting a fourth would make
the bank look thinner than it is while pushing the writer to invent questions nobody should answer.

    python knowledge-eval/scripts/check_ability_coverage.py            # report, exit 0
    python knowledge-eval/scripts/check_ability_coverage.py --strict   # exit 1 on any incomplete cell
    python knowledge-eval/scripts/check_ability_coverage.py --subject math --detail
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_coverage_matrix as ccm  # noqa: E402
import keval  # noqa: E402

DIFFICULTY_WORDS = ("易", "中", "难")
FLOOR = 1
ROBUST = 2

#: Why a cell is not ready, in the order worth fixing: nothing at all, then a missing difficulty.
def verdict(cell: dict) -> str:
    counts = cell["questions"]
    if counts["total"] == 0:
        return "missing"
    if not cell["four_stage"]:
        return "partial"
    if cell["robust"]["ok"]:
        return "robust"
    return "floor"


def deficit_of(cell: dict, want: int) -> dict[str, int]:
    return {word: max(0, want - int(cell["questions"][key]))
            for key, word in zip(("easy", "medium", "hard"), DIFFICULTY_WORDS)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="per-cell readiness gate")
    parser.add_argument("--nodes", nargs="*", default=None)
    parser.add_argument("--questions", nargs="*", default=None)
    parser.add_argument("--subject", default=None, choices=list(keval.SUBJECTS))
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 unless every cell reaches all four stages")
    parser.add_argument("--detail", action="store_true", help="print every cell, not just the gaps")
    args = parser.parse_args(argv)

    nodes = [node for _, payload in keval.load_nodes(args.nodes) for node in payload["nodes"]]
    questions = [q for _, payload in keval.load_questions(args.questions) for q in payload["questions"]]
    cells = ccm.analyse(nodes, questions)["cells"]
    if args.subject:
        cells = [cell for cell in cells if cell["subject"] == args.subject]

    subjects = ([args.subject] if args.subject else list(keval.SUBJECTS))
    by_subject: dict[str, dict] = OrderedDict()
    for subject in subjects:
        mine = [cell for cell in cells if cell["subject"] == subject]
        marks = Counter(verdict(cell) for cell in mine)
        by_subject[subject] = {
            "cells": len(mine),
            "missing": marks["missing"],
            "partial": marks["partial"],
            "floor": marks["floor"],
            "robust": marks["robust"],
            "nodes": len({cell["diagnostic_node_id"] for cell in mine}),
            "nodes_ready": len({cell["diagnostic_node_id"] for cell in mine
                                if verdict(cell) in ("floor", "robust")}),
        }

    total = Counter(verdict(cell) for cell in cells)
    print(f"cells {len(cells)}: {total['floor']} floor, {total['robust']} robust, "
          f"{total['partial']} partial, {total['missing']} missing")
    for subject, block in by_subject.items():
        print(f"  {subject:<8} {block['cells']:>3} cells  "
              f"floor {block['floor'] + block['robust']:>3}  robust {block['robust']:>3}  "
              f"partial {block['partial']:>3}  missing {block['missing']:>3}  "
              f"nodes ready {block['nodes_ready']}/{block['nodes']}")

    for want, name in ((FLOOR, "floor"), (ROBUST, "robust")):
        need = Counter()
        for cell in cells:
            for word, count in deficit_of(cell, want).items():
                need[word] += count
        print(f"top-up to {name}: {sum(need.values())} questions "
              f"({', '.join(f'{word} {need[word]}' for word in DIFFICULTY_WORDS)})")

    if args.detail:
        print("")
        print("| node | line | 易 | 中 | 难 | state |")
        print("|---|---|---|---|---|---|")
        for cell in cells:
            counts = cell["questions"]
            print(f"| `{cell['diagnostic_node_id']}` | {cell['ability_tag']} | {counts['easy']} | "
                  f"{counts['medium']} | {counts['hard']} | {verdict(cell)} |")

    incomplete = [cell for cell in cells if verdict(cell) not in ("floor", "robust")]
    if incomplete and args.detail:
        print("")
        for cell in incomplete:
            need = deficit_of(cell, FLOOR)
            short = "、".join(f"{word} {count}" for word, count in need.items() if count)
            print(f"  x {cell['diagnostic_node_id']} {cell['ability_tag']}: {verdict(cell)}"
                  + (f"，还缺 {short}" if short else ""))

    if args.strict and incomplete:
        print(f"ability coverage: {len(incomplete)} cell(s) cannot produce four stages", file=sys.stderr)
        return 1
    print("ability coverage: ok" if not incomplete
          else f"ability coverage: {len(incomplete)} cell(s) still open (not a failure without --strict)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
