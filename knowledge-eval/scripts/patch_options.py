#!/usr/bin/env python3
"""Apply option-wording patches to a question bank, with guards.

Rule B11 says the correct option must not be the only longest one. Fixing that is purely a
matter of wording — the answer, the explanation and the rotation stay as they are. But 441
small edits cannot be reviewed one by one at the end, so every patch is checked here first:

* the question must exist, and the option count must not change (a 4-option item that becomes
  3-option changes what `correct_answer` means);
* `correct_answer` must still point at the same text — lengthening a distractor is safe,
  silently rewriting the correct option is how a bank gets an answer key that disagrees with
  its explanation, so that one needs `--allow-correct-edit`;
* no duplicate options after the patch (a rewritten distractor that collides with another
  option gives the item two right answers, or none);
* the stem must not contain the old option text (disambiguation items quote their options in
  the stem — patching those would leave the stem pointing at wording that no longer exists);
* the explanation must not quote the changed option (same reason, one step later).

Patch file, one JSON object per line:

    {"q": "chinese-6up-d04-q03", "o": ["...", "...", "...", "..."]}

Run:

    python3 scripts/patch_options.py --subject chinese --patch p.jsonl --dry-run
    python3 scripts/patch_options.py --subject chinese --patch p.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
BANKS = {
    "math": DATA / "questions_math_6up.json",
    "chinese": DATA / "questions_chinese_6up.json",
    "english": DATA / "questions_english_6up.json",
}
LABELS = "ABCDEFGH"
MIN_QUOTE = 3  # shorter strings match everything; only quote-worthy text counts


def quotable(text: str) -> bool:
    """Is this option distinctive enough that finding it in the stem means a real quote?

    Two CJK characters are a word ("欲望", "需要") and do get quoted in stems and explanations;
    two Latin characters are noise and match everywhere. Hence the split by script.
    """
    if len(text) >= MIN_QUOTE:
        return True
    if len(text) == 2:
        return all("\u4e00" <= char <= "\u9fff" for char in text)
    return False


def find(questions: list[dict], question_id: str) -> dict | None:
    for question in questions:
        if question.get("question_id") == question_id:
            return question
    return None


def still_unbalanced(question: dict, options: list[str]) -> bool:
    """Would the patched item still hand a free point to a child who picks the longest option?"""
    lengths = [len(option) for option in options]
    index = LABELS.index(question["correct_answer"])
    correct = lengths[index]
    return correct == max(lengths) and lengths.count(correct) == 1


def check(question: dict, options: list[str], allow_correct: bool) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block the patch."""
    errors: list[str] = []
    warnings: list[str] = []
    old = question["options"]
    index = LABELS.index(question["correct_answer"])

    if len(options) != len(old):
        errors.append(f"{len(old)} options became {len(options)}")
        return errors, warnings
    if any(not str(option).strip() for option in options):
        errors.append("an option is blank")
    if len(set(options)) != len(options):
        errors.append("duplicate options after the patch")

    changed = [i for i, (a, b) in enumerate(zip(old, options)) if a != b]
    if not changed:
        warnings.append("no option changed")

    if index in changed and not allow_correct:
        errors.append(f"the correct option ({LABELS[index]}) was rewritten: "
                      f"{old[index]!r} -> {options[index]!r}")
    elif index in changed:
        warnings.append(f"correct option ({LABELS[index]}) rewritten, needs a human read")

    stem = question.get("stem", "")
    for i in changed:
        if quotable(old[i]) and old[i] in stem:
            errors.append(f"stem quotes the changed option {LABELS[i]} ({old[i]!r})")
    explanation = question.get("explanation", "") or ""
    for i in changed:
        if quotable(old[i]) and old[i] in explanation:
            warnings.append(f"explanation quotes the changed option {LABELS[i]} ({old[i]!r})")

    if not errors and still_unbalanced(question, options):
        errors.append("the correct option is still the unique longest — "
                      "lengthen a distractor or shorten it, the patch has to do the job")
    return errors, warnings


def load_patch(path: Path) -> list[dict]:
    items = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path}:{number}: not JSON ({error})")
        if not item.get("q") or not isinstance(item.get("o"), list):
            raise SystemExit(f"{path}:{number}: needs `q` and an `o` array")
        item["_line"] = number
        items.append(item)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", required=True, choices=sorted(BANKS))
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-correct-edit", action="store_true",
                        help="permit rewriting the correct option (a human must read the result)")
    args = parser.parse_args(argv)

    bank_path = BANKS[args.subject]
    payload = json.loads(bank_path.read_text(encoding="utf-8"))
    questions = payload["questions"]

    patches = load_patch(args.patch)
    errors = 0
    warnings = 0
    applied = 0
    seen: set[str] = set()
    touched: list[str] = []

    for item in patches:
        question_id = item["q"]
        where = f"{args.patch.name}:{item['_line']} {question_id}"
        if question_id in seen:
            print(f"FAIL {where}: patched twice in one file", file=sys.stderr)
            errors += 1
            continue
        seen.add(question_id)
        question = find(questions, question_id)
        if question is None:
            print(f"FAIL {where}: no such question in {args.subject}", file=sys.stderr)
            errors += 1
            continue
        if len(question.get("options") or []) < 2:
            print(f"FAIL {where}: not a choice question", file=sys.stderr)
            errors += 1
            continue

        problems, cautions = check(question, item["o"], args.allow_correct_edit)
        for problem in problems:
            print(f"FAIL {where}: {problem}", file=sys.stderr)
        for caution in cautions:
            print(f"warn {where}: {caution}", file=sys.stderr)
        errors += len(problems)
        warnings += len(cautions)
        if problems:
            continue

        question["options"] = item["o"]
        applied += 1
        touched.append(question_id)

    print(f"patch: {applied} applied, {errors} failed, {warnings} warnings", file=sys.stderr)
    if errors:
        print("nothing written: fix the FAIL lines first", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry run: bank untouched", file=sys.stderr)
        return 0

    # The banks are written with indent=1 and a trailing newline; match that so a patch shows up
    # as the option lines it changed, not as a whole-file reformat.
    bank_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {bank_path.name}: {len(questions)} questions, {len(touched)} patched",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
