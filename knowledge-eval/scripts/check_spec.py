"""Check one batch spec against the B-layer rules that a machine can see, before it is written.

    python knowledge-eval/scripts/check_spec.py knowledge-eval/batches/robust-english-E01.jsonl --subject english
    python knowledge-eval/scripts/check_spec.py <spec> --subject chinese --per-cell 2 --no-bank

`batch_spec.py` already refuses a spec it cannot expand (unknown keys, a stem over 120 characters, two
identical options, an answer label off the end). What it does not look at is the layer the sixteen
batches of M1–E6 actually came back on — see docs/question-rules-2026-11-20.md §2 — so those rules get
written twice: once in the doc, once here. The ones with a hard feature are errors:

    B1  a stem that says 读句子 / 读对话 / 读短文 with nothing in quotes — there is no material to read
    B2  skipped: whether a distractor contradicts the quotation is a judgement, not a string
    B4  two stems in one spec that normalise to the same text
    B6  a quotation longer than 90 characters, which leaves the question under 30
    B11 the correct option is the unique longest one — the child can ride it without reading

B11 and the 1.5× length band are printed as warnings with a count, not failures: English word-form items
("visit / visited / visits / visiting") cannot meet the band, and the point of the number is to see it
fall batch by batch, not to fail a spec that is otherwise readable.

Exit code 1 on any error, one line each on stdout, then a summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402
from check_content import MAX_STEM, option_letter_refs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BANK = {"math": "questions_math_6up.json", "chinese": "questions_chinese_6up.json",
        "english": "questions_english_6up.json"}

BASE_KEYS = {"n", "a", "d", "t", "s", "o", "k", "e", "g"}
MATH_KEYS = {"c", "x"}
DIFFICULTIES = ("易", "中", "难")

#: Stems that present material without quoting any: the child has no textbook in front of the TV.
MATERIAL_WORDS = ("读句子", "读对话", "读短文", "读下面", "读这段", "读诗", "读一读", "文中", "上文")
#: Phrasings that offload the question onto a text the child cannot open.
OFFLOAD = ("和课文一致", "与课文一致", "根据课文", "课文里", "课文中")

MAX_QUOTE = 90
BAND = 1.5


def quoted(text: str) -> list[str]:
    """Fragments inside “…”, “...” or "..." — the only shape that carries material into a stem."""
    out, buf, open_at = [], [], None
    for index, char in enumerate(text):
        if char in "“\"":
            if open_at is None:
                open_at, buf = index, []
            else:
                out.append("".join(buf))
                open_at = None
        elif char in "”" and open_at is not None:
            out.append("".join(buf))
            open_at = None
        elif open_at is not None:
            buf.append(char)
    return [item for item in out if item.strip()]


def norm(text: str) -> str:
    return "".join(str(text or "").split())


def load_bank(subject: str) -> set[str]:
    path = ROOT / "data" / BANK[subject]
    payload = json.loads(path.read_text(encoding="utf-8"))
    questions = payload["questions"] if isinstance(payload, dict) else payload
    return {norm(q.get("stem")) for q in questions}


def load_nodes(subject: str) -> dict[str, dict]:
    payload = json.loads((ROOT / "data" / f"nodes_{subject}_6up.json").read_text(encoding="utf-8"))
    return {node["node_id"]: node for node in payload["nodes"]}


def full_id(short: str, subject: str, nodes: dict[str, dict]) -> str:
    """`d05` and `english-6up-d05` both name the same node; the spec may use either."""
    if short in nodes:
        return short
    candidate = f"{subject}-6up-{short}"
    return candidate if candidate in nodes else short


def check(spec: Path, subject: str, per_cell: int, use_bank: bool,
          correct_first: bool = True) -> tuple[list[str], list[str], str]:
    errors: list[str] = []
    warnings: list[str] = []
    nodes = load_nodes(subject)
    known = load_bank(subject) if use_bank else set()

    lines: list[dict] = []
    for number, raw in enumerate(spec.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"line {number}: not JSON — {exc}")
            continue
        lines.append(line)
        label = f"line {number} ({line.get('n')}/{line.get('a')}/{line.get('d')})"

        allowed = BASE_KEYS | (MATH_KEYS if subject == "math" else set())
        unknown = set(line) - allowed
        if unknown:
            errors.append(f"{label}: unknown key(s) {sorted(unknown)}")
        missing = [key for key in ("n", "a", "d", "t", "s", "e") if key not in line]
        if missing:
            errors.append(f"{label}: missing {missing}")

        node_id = full_id(str(line.get("n", "")).strip(), subject, nodes)
        node = nodes.get(node_id)
        if node is None:
            errors.append(f"{label}: unknown node {line.get('n')!r}")
        else:
            if node.get("node_type") != "diagnostic":
                errors.append(f"{label}: {node_id} is not a diagnostic node")
            if line.get("a") not in node.get("ability_lines", []):
                errors.append(f"{label}: ability {line.get('a')!r} is not one of {node.get('ability_lines')}")
        if line.get("d") not in DIFFICULTIES:
            errors.append(f"{label}: difficulty {line.get('d')!r} is not one of {DIFFICULTIES}")

        stem = str(line.get("s", ""))
        if len(stem) > MAX_STEM:
            errors.append(f"{label}: stem is {len(stem)} characters, over {MAX_STEM}")
        quotes = quoted(stem)
        longest = max((len(item) for item in quotes), default=0)
        if longest > MAX_QUOTE:
            errors.append(f"{label}: quotation is {longest} characters, over {MAX_QUOTE} — "
                          "leave the question under 30")
        if any(word in stem for word in MATERIAL_WORDS) and not quotes:
            errors.append(f"{label}: stem asks the child to read something it does not quote (B1)")
        for word in OFFLOAD:
            if word in stem:
                errors.append(f"{label}: stem says {word!r} — the child has no textbook (B1)")

        refs = option_letter_refs(str(line.get("e", "")))
        if refs and subject != "math":
            errors.append(f"{label}: explanation points at options by letter {sorted(set(refs))}")
        if any(word in str(line.get("e", "")) for word in OFFLOAD):
            errors.append(f"{label}: explanation says {word!r} — the child has no textbook (B1)")

        kind = line.get("t")
        if kind == "填空题" and subject != "math":
            errors.append(f"{label}: {subject} cannot use a fill — the TV keypad has no letters")
        options = [item.strip() for item in str(line.get("o", "")).split("|") if item.strip()]
        if kind != "填空题":
            if len(options) < 2:
                errors.append(f"{label}: a choice needs at least two options")
            if kind == "判断题" and len(options) != 2:
                errors.append(f"{label}: a judgement item needs exactly two options")
            if len(set(options)) != len(options):
                errors.append(f"{label}: two options are identical")
            if correct_first and str(line.get("k", "")).strip() != "A":
                errors.append(f"{label}: the spec is written correct-option-first, so k must be A, "
                              f"not {line.get('k')!r} — rotation happens afterwards")

    # B4 and the whole-bank duplicate rule, on the same normalised form the gate uses.
    seen: dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        key = norm(line.get("s"))
        if key in seen:
            errors.append(f"line {number}: the same stem already appears on line {seen[key]}")
        else:
            seen[key] = number
        if key in known:
            errors.append(f"line {number}: repeats the stem of a question already in the {subject} bank")

    # Every cell the spec touches must be filled to the same depth, and each line to the same total:
    # a cell short of a difficulty cannot be judged, and the next batch then has to come back for it.
    cells: dict[tuple[str, str, str], int] = Counter()
    per_line: dict[tuple[str, str], int] = Counter()
    for line in lines:
        node_id = full_id(str(line.get("n", "")).strip(), subject, nodes)
        cells[(node_id, line.get("a"), line.get("d"))] += 1
        per_line[(node_id, line.get("a"))] += 1
    for (node_id, ability, difficulty), count in sorted(cells.items(), key=lambda kv: str(kv[0])):
        if count != per_cell:
            errors.append(f"{node_id}/{ability}/{difficulty}: {count} question(s), want {per_cell}")
    for (node_id, ability), count in sorted(per_line.items(), key=lambda kv: str(kv[0])):
        if count != per_cell * len(DIFFICULTIES):
            errors.append(f"{node_id}/{ability}: {count} question(s) in the line, "
                          f"want {per_cell * len(DIFFICULTIES)}")

    # B11: the correct option is first in the spec, so this is measurable before rotation.
    unique_longest = 0
    choices = 0
    widest = []
    for line in lines:
        options = [item.strip() for item in str(line.get("o", "")).split("|") if item.strip()]
        if len(options) < 2:
            continue
        choices += 1
        sizes = [len(item) for item in options]
        label = str(line.get("k", "A")).strip()
        index = "ABCDEFGH".index(label) if label in "ABCDEFGH"[:len(options)] else 0
        correct = sizes[index]
        if correct == max(sizes) and sizes.count(correct) == 1:
            unique_longest += 1
        if min(sizes) and max(sizes) / min(sizes) > BAND:
            widest.append(f"{line.get('n')}:{'/'.join(map(str, sizes))}")
    share = unique_longest / choices if choices else 0.0
    if share > 0.35:
        warnings.append(f"B11: {unique_longest}/{choices} correct options are the unique longest "
                        f"({share:.0%}) — trim the correct option or lengthen a distractor")
    if widest:
        warnings.append(f"length band: {len(widest)} choice(s) over {BAND}× — {' '.join(widest[:6])}")

    summary = (f"{spec.name}: {len(lines)} questions · {len(cells)} cells · "
               f"{len({k[0] for k in cells})} node(s) · B11 {unique_longest}/{choices}")
    return errors, warnings, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument("--subject", default="english", choices=sorted(BANK))
    parser.add_argument("--per-cell", type=int, default=2,
                        help="questions wanted per (node, ability, difficulty); floor is 1, robust 3")
    parser.add_argument("--no-bank", action="store_true",
                        help="skip the whole-bank duplicate check (dry runs in a sandbox)")
    parser.add_argument("--rotated", action="store_true",
                        help="the spec has already been through rotate_options, so k is no longer A")
    args = parser.parse_args(argv)

    errors, warnings, summary = check(args.spec, args.subject, args.per_cell, not args.no_bank,
                                      correct_first=not args.rotated)
    for warning in warnings:
        print(f"warn  {warning}")
    for error in errors:
        print(f"FAIL  {error}")
    print(summary)
    print("spec: ok" if not errors else f"spec: {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
