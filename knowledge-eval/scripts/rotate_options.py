"""Move the correct option away from the front of every choice in a spec.

    python knowledge-eval/scripts/rotate_options.py knowledge-eval/batches/math-M1-robust.jsonl
    python knowledge-eval/scripts/rotate_options.py <spec> --seed 20261121 --dry-run

A spec is written with the correct option first, because that is the only order in which a person can
check the content while writing it. `batch_spec.py` then refuses a batch with more than 60% of its
choices on one label, so the rotation has to happen between the two — and doing it by hand is how the
first maths batch ended up 24 A's.

The rotation asserts the thing that can go wrong silently: the option now marked correct is the same
text that was marked correct before. A tool that recomputes `k` from the position, instead of moving
the text and reading the label back, will rotate the label and leave the answer behind.

Targets are round-robined per option count (so 4-option items get an even 25% each), then the whole
assignment is shuffled with a fixed seed: even without the shuffle the round-robin would repeat every
four lines, which a child learning the pattern could ride.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

LABELS = "ABCDEFGH"


class SpecError(Exception):
    """A line where the label and the options disagree — nothing is written."""


def rotate_line(line: dict, target: int) -> dict:
    """Put the line's correct option at `target`, keeping the text and rewriting the label."""
    options = [item.strip() for item in str(line.get("o", "")).split("|") if item.strip()]
    if not options:
        return line
    label = str(line.get("k", "")).strip()
    if label not in LABELS[:len(options)]:
        raise SpecError(f"answer {label!r} is not one of {LABELS[:len(options)]}")
    correct = options[LABELS.index(label)]
    others = options[:LABELS.index(label)] + options[LABELS.index(label) + 1:]
    target %= len(options)
    rotated = others[:target] + [correct] + others[target:]
    if rotated[target] != correct:  # pragma: no cover - the construction above guarantees this
        raise SpecError("rotation lost the correct option")

    line = dict(line)
    line["o"] = "|".join(rotated)
    line["k"] = LABELS[target]
    return line


def rotate(lines: list[dict], seed: int) -> list[dict]:
    """Balanced targets, shuffled. Lines keep their order; only `o` and `k` change.

    Fills are excluded from the shuffle, not given target 0 and left in the pot. A real batch is about
    half fills, and when their zeros join the shuffle they take most of the zero slots: the first run
    of this tool put 12 of 19 choices back on A, which is the very bias it exists to remove.
    """
    seen: Counter = Counter()
    targets: list[int | None] = []
    for line in lines:
        size = len([item for item in str(line.get("o", "")).split("|") if item.strip()])
        if size < 2:
            targets.append(None)
            continue
        targets.append(seen[size] % size)
        seen[size] += 1

    choice_slots: dict[int, list[int]] = {}
    for index, line in enumerate(lines):
        size = len([item for item in str(line.get("o", "")).split("|") if item.strip()])
        if size >= 2:
            choice_slots.setdefault(size, []).append(index)

    # Shuffle inside each option count, never across them. A judgement item has two labels, so a
    # pooled shuffle hands its surplus zeros to the four-option items: one run put 5 of 13 choices on A
    # while only 1 reached C, which is the bias this tool exists to remove, wearing a different coat.
    for indexes in choice_slots.values():
        values = [targets[index] for index in indexes]
        random.Random(seed).shuffle(values)
        for index, target in zip(indexes, values):
            targets[index] = target

    return [rotate_line(line, target or 0) for line, target in zip(lines, targets)]


def read_spec(path: Path) -> list[dict]:
    lines: list[dict] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            lines.append(json.loads(text))
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path.name}:{number}: not JSON — {error}") from error
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--seed", type=int, default=20261120, help="fixed so a rerun is the same rotation")
    parser.add_argument("--dry-run", action="store_true", help="report the distribution, write nothing")
    args = parser.parse_args(argv)

    lines = read_spec(args.spec)
    try:
        rotated = rotate(lines, args.seed)
    except SpecError as error:
        print(f"rotate: {error} — nothing written", file=sys.stderr)
        return 1

    spread = Counter(line["k"] for line in rotated
                     if len(str(line.get("o", "")).split("|")) > 1 and str(line.get("o", "")).strip())
    print(f"{args.spec.name}: {len(rotated)} line(s), choices {dict(sorted(spread.items()))}")
    if args.dry_run:
        print("dry run, not saved")
        return 0

    args.spec.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in rotated) + "\n",
                         encoding="utf-8")
    print("rotated in place")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
