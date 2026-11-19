"""Does the node data still describe the textbook the child actually has?

    python knowledge-eval/scripts/check_outline_coverage.py
    python knowledge-eval/scripts/check_outline_coverage.py --subject chinese
    python knowledge-eval/scripts/check_outline_coverage.py --strict
    python knowledge-eval/scripts/check_outline_coverage.py --data-dir /tmp/copy

Two directions, as agreed:

    outline - referenced    课本有、节点没提        → 漏
    referenced - outline     节点提了、课本没有      → 错指

The outline is frozen at three levels — unit, 课文标题, 板块名 — and never goes down to 小节. Every
教辅 splits sections its own way, so a section-level baseline cannot be agreed on; with no agreed
baseline the strictest checker produces the most wrong answers. `assessable: false` marks an item as
deliberately outside the quiz (习作、口语交际、附录) and has to carry a reason: "not modelled" and
"decided against" are different things, and only the second one is a decision.

Four more rules exist because the set difference alone cannot see them:

    3. a chapter node's name must equal the unit's printed title (maths / English only — 语文 units
       carry an editorial theme, not a printed title)
    4. every 《title》 in a question stem or option must be a title the book contains — this is the
       weak check that would have caught the three dead 伯牙鼓琴 questions
    5. no retired title (a text that left this volume last time the book changed) may appear
       anywhere, with or without 《》 — that catches 旧版课文名 sitting bare in micro_nodes

Only 2, 3, 4 and 5 fail the run: they say the data contradicts the textbook, which is never right.
A 漏 is a report — a textbook item nobody built a node for is work not done, not data lying. `--strict`
fails on 漏 too, for when the bank is meant to be complete.

Exit code 1 on any contradiction or on a 漏 under --strict; 0 otherwise. File I/O only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import keval  # noqa: E402

TITLE = re.compile(r"《([^》]+)》")

#: Where a 课文名 can hide. `name` and `micro_nodes` are where the node says what it covers; a question
#: is what the child is actually asked.
def _node_text(node: dict) -> str:
    return " ".join([node["name"], *node["micro_nodes"]])


def _question_text(question: dict) -> str:
    parts = [question["stem"], question.get("explanation", "")]
    parts.extend(question.get("options") or [])
    return " ".join(parts)


def load_subject(data_dir: Path, subject: str) -> tuple[dict, list[dict], list[dict]]:
    outline = keval.read_json(data_dir / f"outline_{subject}_6up.json")
    nodes = keval.read_json(data_dir / f"nodes_{subject}_6up.json")["nodes"]
    questions = keval.read_json(data_dir / f"questions_{subject}_6up.json")["questions"]
    return outline, nodes, questions


def entries_of(outline: dict) -> list[tuple[str | None, dict]]:
    """Every content entry with the unit it sits under (None for book-level boards)."""
    rows: list[tuple[str | None, dict]] = []
    for unit in outline["units"]:
        for entry in unit["entries"]:
            rows.append((unit["code"], entry))
    for entry in outline.get("book_entries") or []:
        rows.append((None, entry))
    return rows


def titles_of(entry: dict) -> list[str]:
    """The names this entry answers to: its own title and everything printed inside it."""
    return [entry["name"], *(entry.get("contains") or [])]


def known_titles(outline: dict) -> set[str]:
    """Every title the book contains, as plain strings without 《》.

    `source_work` is included because a 课文 taken from a larger work is allowed to be asked about by
    that work's name (少年闰土 ← 故乡) without 故乡 being a 课文 of this volume.
    """
    titles: set[str] = set()
    for unit in outline["units"]:
        titles.add(unit["name"])
    for _, entry in entries_of(outline):
        titles.update(titles_of(entry))
        if entry.get("source_work"):
            titles.add(entry["source_work"])
    return titles


def check_structure(outline: dict, nodes: list[dict], path: str) -> list[str]:
    """Schema, then the cross-field rules the schema subset cannot express."""
    errors = [f"{path}: {e}" for e in keval.validate(outline, keval.schema("outline.schema.json"))]

    units = outline["units"]
    codes = [unit["code"] for unit in units]
    for code, count in Counter(codes).items():
        if count > 1:
            errors.append(f"{path}: unit code {code} appears {count} times")
    orders = [unit["order"] for unit in units]
    for order, count in Counter(orders).items():
        if count > 1:
            errors.append(f"{path}: chapter_order {order} is claimed by {count} units")

    chapters = {node["node_id"]: node for node in nodes if node["node_type"] == "aggregate"}
    for unit in units:
        if unit["code"] not in chapters:
            errors.append(f"{path}: unit {unit['code']} has no aggregate node")
        if unit["assessable"] is False and not unit.get("reason"):
            errors.append(f"{path}: {unit['code']} is not assessable and gives no reason")
        if unit["assessable"] and unit.get("reason"):
            errors.append(f"{path}: {unit['code']} is assessable but carries a reason")
    for _, entry in entries_of(outline):
        if entry["assessable"] is False and not entry.get("reason"):
            errors.append(f"{path}: {entry['name']} is not assessable and gives no reason")
        if entry["assessable"] and entry.get("reason"):
            errors.append(f"{path}: {entry['name']} is assessable but carries a reason")
    return errors


def check_unit_names(outline: dict, nodes: list[dict]) -> list[str]:
    errors: list[str] = []
    if not outline["unit_names_from_textbook"]:
        return errors
    chapters = {node["node_id"]: node for node in nodes if node["node_type"] == "aggregate"}
    for unit in outline["units"]:
        node = chapters.get(unit["code"])
        if node and node["name"] != unit["name"]:
            errors.append(f"{unit['code']}: node name {node['name']!r} is not the book's {unit['name']!r}")
        if node and node["chapter_order"] != unit["order"]:
            errors.append(f"{unit['code']}: chapter_order {node['chapter_order']} is not the book's position {unit['order']}")
    return errors


def check_referenced(outline: dict, nodes: list[dict]) -> tuple[list[str], dict]:
    """Direction one: every assessable textbook item has a node that mentions it."""
    gaps: list[str] = []
    by_unit: dict[str | None, list[dict]] = {}
    for node in nodes:
        if node["node_type"] == "diagnostic":
            by_unit.setdefault(node["parent_id"], []).append(node)

    def mentions(entry: dict, pool: list[dict]) -> bool:
        names = titles_of(entry)
        return any(name in _node_text(node) for node in pool for name in names)

    stats = {"entries": 0, "assessable": 0, "covered": 0, "not_assessable": 0}
    for unit in outline["units"]:
        stats["entries"] += 1
        pool = by_unit.get(unit["code"], [])
        if unit["assessable"]:
            stats["assessable"] += 1
            if pool:
                stats["covered"] += 1
            else:
                gaps.append(f"{unit['code']} {unit['name']} — 这一章没有诊断节点")
        else:
            stats["not_assessable"] += 1
    for unit_code, entry in entries_of(outline):
        stats["entries"] += 1
        if not entry["assessable"]:
            stats["not_assessable"] += 1
            continue
        stats["assessable"] += 1
        pool = by_unit.get(unit_code, []) if unit_code else [
            node for items in by_unit.values() for node in items]
        if mentions(entry, pool):
            stats["covered"] += 1
        else:
            where = f"{unit_code} " if unit_code else ""
            gaps.append(f"{where}{entry['name']} — 没有节点提到它")
    return gaps, stats


def check_mispointed(outline: dict, nodes: list[dict], known: set[str]) -> list[str]:
    """Direction two: a title a node claims must be a title the book has."""
    errors: list[str] = []
    for node in nodes:
        if node["node_type"] != "diagnostic":
            continue
        for title in TITLE.findall(_node_text(node)):
            if title not in known:
                errors.append(f"{node['node_id']}: mentions 《{title}》, which is not in the outline")
    return errors


def check_retired(outline: dict, nodes: list[dict], questions: list[dict]) -> list[str]:
    """A text that left this volume must not come back, bracketed or bare."""
    retired = [item["name"] for item in outline.get("retired_titles") or []]
    errors: list[str] = []
    for node in nodes:
        text = _node_text(node)
        for name in retired:
            if name in text:
                errors.append(f"{node['node_id']}: still names {name!r}, which left this volume")
    for question in questions:
        text = _question_text(question)
        for name in retired:
            if name in text:
                errors.append(f"{question['question_id']}: still names {name!r}, which left this volume")
    return errors


def check_question_titles(questions: list[dict], known: set[str]) -> list[str]:
    """The weak check: whatever a question quotes must be printed in the book."""
    errors: list[str] = []
    for question in questions:
        fields = [("stem", question["stem"])]
        for index, option in enumerate(question.get("options") or []):
            fields.append((f"option {chr(ord('A') + index)}", option))
        for label, text in fields:
            for title in TITLE.findall(text):
                if title not in known:
                    errors.append(f"{question['question_id']} {label}: quotes 《{title}》, which is not in the outline")
    return errors


def run_subject(data_dir: Path, subject: str) -> tuple[list[str], list[str], dict]:
    outline, nodes, questions = load_subject(data_dir, subject)
    path = f"outline_{subject}_6up.json"
    known = known_titles(outline)

    errors = check_structure(outline, nodes, path)
    errors += check_unit_names(outline, nodes)
    errors += check_mispointed(outline, nodes, known)
    errors += check_retired(outline, nodes, questions)
    errors += check_question_titles(questions, known)
    gaps, stats = check_referenced(outline, nodes)

    counts = Counter(entry["kind"] for _, entry in entries_of(outline))
    stats.update({"units": len(outline["units"]), "text": counts["text"], "board": counts["board"]})
    return errors, gaps, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="textbook outline vs node data")
    parser.add_argument("--subject", choices=keval.SUBJECTS, action="append", default=None,
                        help="one subject (repeatable); default is all three")
    parser.add_argument("--data-dir", default=str(keval.DATA))
    parser.add_argument("--strict", action="store_true", help="a gap fails the run too")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    subjects = tuple(args.subject or keval.SUBJECTS)

    errors: list[str] = []
    gaps: list[str] = []
    print(f"outline: {len(subjects)} subject(s) from {data_dir}")
    for subject in subjects:
        subject_errors, subject_gaps, stats = run_subject(data_dir, subject)
        errors += subject_errors
        gaps += [f"{subject} {gap}" for gap in subject_gaps]
        print(f"  {subject:<8} {stats['units']:>2} units · {stats['text']:>2} texts · {stats['board']:>2} boards"
              f"   covered {stats['covered']}/{stats['assessable']}"
              f" · gap {len(subject_gaps)} · 不测 {stats['not_assessable']}")

    if gaps:
        print("gap (课本有、节点没提):")
        for gap in gaps:
            print(f"  - {gap}")
        print("  （不测的条目已带理由记在 outline 里，不计入 gap；要让它失败跑 --strict）")

    if errors:
        for error in errors:
            print(f"  x {error}", file=sys.stderr)
        print(f"outline: {len(errors)} problem(s)", file=sys.stderr)
        return 1

    if args.strict and gaps:
        print(f"outline: {len(gaps)} gap(s) under --strict", file=sys.stderr)
        return 1

    print(f"outline: ok ({len(gaps)} gap(s) reported as work not done)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
