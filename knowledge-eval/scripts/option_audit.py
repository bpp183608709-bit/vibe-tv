#!/usr/bin/env python3
"""Audit option-length balance across the question banks (rule B11).

The problem this measures: when the correct option is the longest one, a child who cannot
answer can still press the longest option and win. 68% of the Chinese bank used to work that
way, which is not a difficulty distribution — it is a writing habit ("the right answer needs
more words to be precise").

Two numbers matter, and they are not the same:

* `unique longest` — the correct option is strictly longer than every other option. This is
  the hard violation of B11; every one of them is a free point for a guessing child.
* `guess-longest` — the expected accuracy of "always pick the longest option". Ties are split
  evenly, so it is the honest measure of how much the heuristic buys. Random is 25%.

Run:

    python3 scripts/option_audit.py                     # summary for all subjects
    python3 scripts/option_audit.py --subject chinese    # per-node breakdown
    python3 scripts/option_audit.py --subject chinese --emit   # worklist, one block per question
    python3 scripts/option_audit.py --strict             # exit 1 if any hard violation

The worklist format is what rewrite batches consume: question id, ability/difficulty, the stem,
each option with its length, and a `*` on the correct one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
BANKS = {
    "math": DATA / "questions_math_6up.json",
    "chinese": DATA / "questions_chinese_6up.json",
    "english": DATA / "questions_english_6up.json",
}
LABELS = "ABCDEFGH"
PROSE = re.compile(r"[\u4e00-\u9fffA-Za-z]")

# 长度由**词形**决定的题，加字等于改答案，B11 不适用。
# `go swimming` 比 `go swim` 长，`bottles` 比 `bottle` 长，`will be` 比 `will` 长 ——
# 这些长度差正是被考的那个点，把干扰项补长就等于把它改成对的。
FORM_ITEMS = {
    "english-6up-d02-q01",   # go swimming / going swim / go swim / go to swim
    "english-6up-d03-q04",   # studies; watches / study; watch / studys; watches
    "english-6up-d08-q01",   # will be / will / is / was
    "english-6up-d08-q03",   # will be; will hold / will; will hold
    "english-6up-d09-q04",   # It is sunny / It is rain / It is wind
    "english-6up-d09-q06",   # 三句一组的天气词词形
    "english-6up-d10-q03",   # bottles; pieces / bottle; piece
    "english-6up-d10-q06",   # three bottles of juice / three bottle of juice
}

# 题干自己把长度当成判据的题：问的是"表达最完整"，最完整的那句必然最长。
# 改选项改不掉这个矛盾，除非改题干；留作已知例外。
STEM_REWARDS_LENGTH = {
    "english-6up-d02-q11",   # 说"我假期过得很开心"，下面表达最完整、最自然的一项是
}


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def selectable(question: dict) -> bool:
    """Only choice questions are subject to B11; math fill-ins have no options to balance."""
    return len(question.get("options") or []) >= 2


def prose(question: dict) -> bool:
    """Is the length of these options a reading signal at all?

    When every option is pure notation (`8` / `4` / `16` / `2`, or `3/7 + 5` against
    `3/7 + 3/7 + 3/7 + 3/7 + 3/7`) the difference is arithmetic, not wording. Latin letters
    count as prose: `doing` really is a longer word than `do`, and that is a reading signal. "Pick the longest"
    stops being a reading shortcut and becomes digit counting, which no child does — and there is
    no honest way to pad it: `n = 8` next to a bare `16` is a *new* odd-one-out. So those sets are
    measured separately and left alone.
    """
    if question.get("question_id") in FORM_ITEMS:
        return False
    if question.get("question_id") in STEM_REWARDS_LENGTH:
        return False
    return any(PROSE.search(option) for option in question["options"])


def measure(question: dict) -> dict:
    options = question["options"]
    lengths = [len(option) for option in options]
    index = LABELS.index(question["correct_answer"])
    correct = lengths[index]
    top = max(lengths)
    tied = lengths.count(top)
    return {
        "index": index,
        "lengths": lengths,
        "correct": correct,
        "longest": top,
        "unique_longest": correct == top and tied == 1,
        "guess_weight": (1 / tied) if correct == top else 0.0,
        "band": (top / min(lengths)) if min(lengths) else 0.0,
        "gap": correct - max(x for i, x in enumerate(lengths) if i != index),
    }


def audit(subject: str, include_numeric: bool = False) -> list[tuple[dict, dict]]:
    return [(q, measure(q)) for q in load(BANKS[subject])
            if selectable(q) and (include_numeric or prose(q))]


def numeric_count(subject: str) -> int:
    return sum(1 for q in load(BANKS[subject]) if selectable(q) and not prose(q))


def summary() -> None:
    print(f"{'subject':8s} {'prose':>7s} {'unique>':>8s} {'share':>6s} {'guess':>6s} "
          f"{'band>1.5':>9s} {'numeric':>8s}")
    total = Counter()
    for subject in BANKS:
        rows = audit(subject)
        bad = sum(1 for _, m in rows if m["unique_longest"])
        guess = sum(m["guess_weight"] for _, m in rows) / len(rows)
        band = sum(1 for _, m in rows if m["band"] > 1.5)
        total["choice"] += len(rows)
        total["bad"] += bad
        print(f"{subject:8s} {len(rows):7d} {bad:8d} {bad / len(rows):5.0%} "
              f"{guess:6.0%} {band:9d} {numeric_count(subject):8d}")
    print(f"{'total':8s} {total['choice']:7d} {total['bad']:8d} "
          f"{total['bad'] / total['choice']:5.0%}")


def per_node(subject: str) -> None:
    rows = audit(subject)
    grouped = defaultdict(lambda: [0, 0])
    for question, stats in rows:
        node = question["diagnostic_node_id"]
        grouped[node][0] += 1
        grouped[node][1] += stats["unique_longest"]
    print(f"{'node':22s} {'choice':>6s} {'viol':>5s}")
    for node in sorted(grouped):
        count, bad = grouped[node]
        print(f"{node:22s} {count:6d} {bad:5d}" + ("  <<" if bad else ""))


def emit(subject: str, only: str | None) -> None:
    rows = audit(subject)
    shown = 0
    for question, stats in rows:
        if not stats["unique_longest"]:
            continue
        if only and only not in question["diagnostic_node_id"]:
            continue
        shown += 1
        index = stats["index"]
        print(f"### {question['question_id']}")
        print(f"    [{question['ability_tag']}/{question['difficulty_preset']}] {question['stem']}")
        for i, option in enumerate(question["options"]):
            mark = "*" if i == index else " "
            print(f"  {mark}{LABELS[i]} {stats['lengths'][i]:3d}  {option}")
        gap = stats["gap"]
        others = [x for i, x in enumerate(stats["lengths"]) if i != index]
        print(f"    gap={gap:+d}  (correct {stats['correct']} vs next longest {max(others)}), "
              f"band={stats['band']:.2f}")
        print(f"    e: {question['explanation']}")
        print()
    print(f"# {shown} questions", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", choices=sorted(BANKS))
    parser.add_argument("--node", help="only this diagnostic node (e.g. chinese-6up-d04)")
    parser.add_argument("--emit", action="store_true", help="print the rewrite worklist")
    parser.add_argument("--strict", action="store_true", help="exit 1 on any unique-longest")
    parser.add_argument("--all", action="store_true",
                        help="include numeric-only option sets (exempt by default)")
    args = parser.parse_args(argv)

    if args.emit:
        if not args.subject:
            parser.error("--emit needs --subject")
        emit(args.subject, args.node)
        return 0

    if args.subject:
        per_node(args.subject)
    else:
        summary()

    if args.strict:
        remaining = sum(1 for subject in BANKS for _, m in audit(subject, args.all)
                        if m["unique_longest"])
        return 1 if remaining else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
