"""Expand one batch spec into the question bank, with the arithmetic checked before anything is written.

    python knowledge-eval/scripts/batch_spec.py --subject math --spec knowledge-eval/batches/math-M1.jsonl
    python knowledge-eval/scripts/batch_spec.py --subject math --spec ... --dry-run

Why a spec format instead of writing the JSON by hand: a question object is ~250 characters of which
~90 are content. At 500 questions the template is the bulk of the output, and every hand-typed
`question_id` is a chance to collide. The spec carries only what a person has to decide.

One line per question, these keys and no others:

    n   node id, either `math-6up-d05` or just `d05` (the tail needs --subject)
    a   ability line, must be one of the node's `ability_lines`
    d   difficulty preset: 易 / 中 / 难
    t   question type: 选择题 / 填空题 / 判断题
    s   stem, at most MAX_STEM characters
    o   choices only: options separated by `|`, in display order
    k   choice: the label (A/B/C…); fill: the answer as a number or fraction
    x   fill only: other accepted spellings, separated by `|` (e.g. `1.5`)
    e   explanation, shown after the child answers
    g   tags, separated by `|`; defaults to the node name
    c   arithmetic check (math only, required). For a fill it must evaluate to the answer; for a
        choice it must evaluate to True — write the computation that makes the correct option correct,
        not a restatement of the label.

The `c` field is the whole point of the exercise. Forty questions written in one pass will contain
arithmetic mistakes; `Fraction(5,6) * 3 == Fraction(5,2)` will not. Chinese and English have no
equivalent — their questions are gated on form only, and the check has to be reading.

Exit codes: 0 all lines written (or already present), 1 anything rejected. Nothing is written unless
every line passes: a half-applied batch is harder to reason about than a rejected one.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_content  # noqa: E402
import keval  # noqa: E402

FIELDS = {"n", "a", "d", "t", "s", "o", "k", "x", "e", "c", "g"}
COGNITIVE = {"易": "记忆理解", "中": "应用", "难": "分析评价创造"}
LABELS = "ABCDEFGH"
#: eval() runs against this namespace and nothing else: no builtins, no imports. It is the repo's own
#: data file, but a spec line is still a line of Python and should not be able to do anything but arithmetic.
SAFE = {"Fraction": Fraction, "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
        "round": round, "int": int, "float": float, "True": True, "False": False}


def as_number(text: str) -> Fraction | None:
    return check_content.as_number(str(text))


def same_number(got, want: Fraction) -> bool:
    """`1 / 6` is a float and never exactly Fraction(1, 6); compare with a tolerance rather than
    demanding everyone write Fraction(1, 6) in the spec. An exact Fraction or int is compared exactly."""
    if isinstance(got, Fraction):
        return got == want
    if isinstance(got, int):
        return Fraction(got) == want
    if isinstance(got, float):
        return abs(got - float(want)) <= 1e-9
    return False


def evaluate(expr: str) -> object:
    """The `c` expression, evaluated with no builtins. Raises on syntax errors — the caller reports it."""
    return eval(compile(expr, "<check>", "eval"), {"__builtins__": {}}, dict(SAFE))


def split_list(text: str | None) -> list[str]:
    return [item.strip() for item in str(text or "").split("|") if item.strip()]


def resolve_node(raw: str, subject: str | None, by_id: dict[str, dict], by_tail: dict[str, dict]) -> tuple[dict | None, str]:
    text = str(raw or "").strip()
    if text in by_id:
        return by_id[text], ""
    if subject and f"{subject}-6up-{text}" in by_id:
        return by_id[f"{subject}-6up-{text}"], ""
    if "-" not in text:
        owner = by_tail.get(text)
        if owner is not None and subject is None:
            return None, f"node {text!r} needs --subject (it exists under {owner})"
    return None, f"node {text!r} is not a diagnostic node"


def validate(line: dict, index: int, node: dict | None, seen_stems: dict[str, str],
             bank_stems: dict[str, str], cell_counts: dict[tuple[str, str], int],
             stats: dict) -> tuple[dict | None, list[str]]:
    """One spec line to one question object. Returns (question, errors); a question with errors is dropped."""
    where = f"line {index}"
    errors: list[str] = []
    unknown = set(line) - FIELDS
    if unknown:
        errors.append(f"{where}: unknown key(s) {sorted(unknown)}")
    for key in ("n", "a", "d", "t", "s", "k", "e"):
        if not str(line.get(key, "")).strip():
            errors.append(f"{where}: `{key}` is required")

    if node is None or errors:
        return None, errors

    node_id = node["node_id"]
    subject = node["subject"]
    ability = str(line["a"]).strip()
    difficulty = str(line["d"]).strip()
    qtype = str(line["t"]).strip()
    stem = str(line["s"]).strip()
    answer = str(line["k"]).strip()

    if ability not in (node.get("ability_lines") or []):
        errors.append(f"{where}: ability {ability!r} is not declared on {node_id} {node.get('ability_lines')}")
    if difficulty not in COGNITIVE:
        errors.append(f"{where}: difficulty {difficulty!r} is not 易/中/难")
    if qtype not in ("选择题", "填空题", "判断题"):
        errors.append(f"{where}: type {qtype!r} is not 选择题/填空题/判断题")
    if len(stem) > check_content.MAX_STEM:
        errors.append(f"{where}: stem is {len(stem)} characters, over {check_content.MAX_STEM}")

    key = re.sub(r"\s+", "", stem)
    if key in seen_stems:
        errors.append(f"{where}: the same stem already appears on this spec line {seen_stems[key]}")
    elif key in bank_stems:
        errors.append(f"{where}: the same stem is already in the bank as {bank_stems[key]}")
    if key and key not in seen_stems:
        seen_stems[key] = str(index)

    options = split_list(line.get("o"))
    extras = split_list(line.get("x"))
    question: dict = {
        "diagnostic_node_id": node_id,
        "subject": subject,
        "ability_tag": ability,
        "cognitive_level": COGNITIVE.get(difficulty, ""),
        "difficulty_preset": difficulty,
        "question_type": qtype,
        "stem": stem,
    }

    if qtype in ("选择题", "判断题"):
        if not options:
            errors.append(f"{where}: a choice needs `o` with options separated by |")
        if extras:
            errors.append(f"{where}: a choice must not carry `x` accepted strings")
        if len(set(options)) != len(options):
            errors.append(f"{where}: two options are identical")
        if qtype == "判断题" and len(options) != 2:
            errors.append(f"{where}: a judgement item needs exactly two options, has {len(options)}")
        if answer not in LABELS[:len(options)]:
            errors.append(f"{where}: answer {answer!r} is not one of {LABELS[:len(options)]}")
        question["options"] = options
        question["correct_answer"] = answer
    else:
        if subject != "math":
            errors.append(f"{where}: {subject} cannot use a fill — the TV keypad has no letters")
        if options:
            errors.append(f"{where}: a fill must not carry `o`")
        if as_number(answer) is None:
            errors.append(f"{where}: fill answer {answer!r} is not a number or fraction")
        question["accepted"] = [answer] + extras
        question["correct_answer"] = answer

    if subject == "math":
        expr = str(line.get("c", "")).strip()
        verified = False
        if not expr:
            if qtype == "填空题":
                errors.append(f"{where}: a maths fill needs `c` — nothing else verifies the answer")
        else:
            try:
                got = evaluate(expr)
            except Exception as error:  # noqa: BLE001 - report the expression's own failure
                errors.append(f"{where}: `c` does not evaluate: {error}")
            else:
                if qtype == "填空题":
                    want = as_number(answer)
                    if want is None or not same_number(got, want):
                        errors.append(f"{where}: `c` gives {got!r}, the answer says {answer!r} — "
                                      "a fill's `c` must compute the answer, not agree with it")
                    else:
                        verified = True
                elif got is True:
                    verified = True
                else:
                    errors.append(f"{where}: `c` must evaluate to True for a choice, got {got!r}")
        if verified:
            stats["checked"] += 1
    elif str(line.get("c", "")).strip():
        errors.append(f"{where}: `c` is maths-only; nothing here can evaluate it")

    question["explanation"] = str(line["e"]).strip()
    question["tags"] = split_list(line.get("g")) or [node["name"].strip()]

    cell = (node_id, ability)
    cell_counts[cell] += 1
    if cell_counts[cell] > check_content.MAX_PER_CELL:
        errors.append(f"{where}: {node_id}/{ability} would hold {cell_counts[cell]} questions, "
                      f"over the {check_content.MAX_PER_CELL} ceiling")
    return (None if errors else question), errors


def next_ids(existing: list[dict], node_id: str, count: int) -> list[str]:
    pattern = re.compile(rf"^{re.escape(node_id)}-q(\d+)$")
    used = 0
    for question in existing:
        match = pattern.match(question["question_id"])
        if match:
            used = max(used, int(match.group(1)))
    return [f"{node_id}-q{used + step:02d}" for step in range(1, count + 1)]


def read_spec(path: Path) -> list[tuple[int, dict]]:
    lines: list[tuple[int, dict]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            lines.append((number, json.loads(text)))
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path.name}:{number}: not JSON — {error}") from error
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", required=True, type=Path, help="JSONL, one question per line")
    parser.add_argument("--subject", choices=keval.SUBJECTS, help="needed when `n` is a bare tail like d05")
    parser.add_argument("--dry-run", action="store_true", help="validate and report, write nothing")
    parser.add_argument("--data-dir", type=Path, default=keval.DATA,
                        help="where the node and question files live; tests point this at a copy")
    args = parser.parse_args(argv)

    node_paths = [args.data_dir / f"nodes_{subject}_6up.json" for subject in keval.SUBJECTS]
    question_paths = [args.data_dir / f"questions_{subject}_6up.json" for subject in keval.SUBJECTS]
    nodes = [node for _, payload in keval.load_nodes([str(p) for p in node_paths]) for node in payload["nodes"]]
    by_id = {node["node_id"]: node for node in nodes if node["node_type"] == "diagnostic"}
    by_tail: dict[str, str] = {}
    for node in by_id.values():
        by_tail.setdefault(node["node_id"].rsplit("-", 1)[1], node["subject"])

    payloads = {path: payload for path, payload in keval.load_questions([str(p) for p in question_paths])}
    bank_stems: dict[str, str] = {}
    bank_keys: dict[tuple[str, str, str, str], str] = {}
    for payload in payloads.values():
        for question in payload["questions"]:
            stem = re.sub(r"\s+", "", question["stem"])
            bank_stems[stem] = question["question_id"]
            bank_keys[(question["diagnostic_node_id"], question["ability_tag"],
                       question["difficulty_preset"], stem)] = question["question_id"]
    existing = [q for payload in payloads.values() for q in payload["questions"]]

    cell_counts: dict[tuple[str, str], int] = Counter()
    for question in existing:
        cell_counts[(question["diagnostic_node_id"], question["ability_tag"])] += 1

    seen_stems: dict[str, str] = {}
    stats: dict = Counter()
    accepted: list[dict] = []
    rejects: list[str] = []
    reused: list[tuple[int, str]] = []
    for index, line in read_spec(args.spec):
        node, why = resolve_node(line.get("n"), args.subject, by_id, by_tail)
        if node is None:
            rejects.append(f"line {index}: {why}")
            continue
        # Re-running a batch that is already applied is not an error: the same four fields on the same
        # node is the same question, so say so and move on. Anything less exact falls through to the
        # duplicate-stem check and fails, which is the difference between "already done" and "said twice".
        fingerprint = (node["node_id"], str(line.get("a", "")).strip(), str(line.get("d", "")).strip(),
                       re.sub(r"\s+", "", str(line.get("s", ""))))
        if fingerprint in bank_keys:
            reused.append((index, bank_keys[fingerprint]))
            continue
        question, errors = validate(line, index, node, seen_stems, bank_stems, cell_counts, stats)
        rejects.extend(errors)
        if question is not None:
            accepted.append(question)

    # Options are written in reading order, so the correct one drifts to A and stays there: the first
    # batch of 24 choices came out 24 A's, which makes "always pick A" a passing strategy. Rotating the
    # options is free, so the spread is a gate rather than a note.
    spread = Counter(question["correct_answer"] for question in accepted if question["question_type"] != "填空题")
    total_choices = sum(spread.values())
    if total_choices >= 6 and max(spread.values()) / total_choices > 0.6:
        worst = max(spread, key=spread.get)
        rejects.append(f"answer spread: {worst} holds {spread[worst]}/{total_choices} of this batch's "
                       f"choices ({dict(sorted(spread.items()))}) — rotate options before writing")

    if reused:
        print(f"{len(reused)} line(s) already in the bank, nothing to add: "
              + ", ".join(sorted({qid for _, qid in reused})[:6])
              + (" …" if len(reused) > 6 else ""))
    if not accepted:
        if rejects:
            for line in rejects:
                print(f"  x {line}", file=sys.stderr)
            print(f"batch: 0 written, {len(rejects)} rejected", file=sys.stderr)
            return 1
        print("batch: already applied, file untouched")
        return 0

    if rejects:
        for line in rejects:
            print(f"  x {line}", file=sys.stderr)
        print(f"batch: {len(accepted)} ok, {len(rejects)} rejected — nothing written", file=sys.stderr)
        return 1

    # Group by node so ids are handed out per node, in the order the spec listed them.
    per_node: dict[str, list[dict]] = defaultdict(list)
    for question in accepted:
        per_node[question["diagnostic_node_id"]].append(question)
    for node_id, items in per_node.items():
        for question, new_id in zip(items, next_ids(existing, node_id, len(items))):
            question["question_id"] = new_id

    if not per_node:
        print("batch: nothing to write", file=sys.stderr)
        return 1
    # The spec is one subject's worth of questions; take it from the node rather than trusting a flag.
    subject = accepted[0]["subject"]
    order = {node["node_id"]: index for index, node in enumerate(nodes)}
    for path, payload in payloads.items():
        if payload["subject"] != subject:
            continue
        merged = payload["questions"] + accepted
        merged.sort(key=lambda q: (order.get(q["diagnostic_node_id"], 999), q["question_id"]))
        payload["questions"] = merged
        if not args.dry_run:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {path.name}: +{len(accepted)} → {len(merged)} questions"
              + (" (dry run, not saved)" if args.dry_run else ""))
        break
    else:
        print(f"no question file for subject {subject!r}", file=sys.stderr)
        return 1

    by_cell: Counter = Counter((q["diagnostic_node_id"], q["ability_tag"]) for q in accepted)
    print("\nthis batch, per cell (易/中/难):")
    for (node_id, ability), total in sorted(by_cell.items(), key=lambda kv: (order.get(kv[0][0], 999), kv[0][1])):
        got = Counter(q["difficulty_preset"] for q in accepted
                      if q["diagnostic_node_id"] == node_id and q["ability_tag"] == ability)
        print(f"  {node_id:<18} {ability:<6} {total} 题  {got['易']}/{got['中']}/{got['难']}")
    if subject == "math":
        # Printed, not enforced: a choice about 方向 has no arithmetic in it and demanding one would be
        # a tautology. Seeing 30/30 or 22/30 per batch is what keeps the number from drifting down.
        print(f"\narithmetic checks: {stats['checked']}/{len(accepted)} questions carry one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
