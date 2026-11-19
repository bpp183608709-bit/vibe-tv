"""Is the bank thick enough to build all four mastery stages, per ability line?

The unit of the question is not "how many questions are there" but one cell:

    (diagnostic node D) x (ability line A)

A cell with n_e easy, n_m medium and n_h hard questions can only ever produce the weighted
accuracies reachable by answering some subset of them right. This script enumerates that set,
scores it with the *server's own* `mastery.summarise` + `mastery.level_of` (never a copy of the
arithmetic), and reports which of 待测 / 未掌握 / 基础 / 熟练 / 拓展 each cell can actually reach.

    python knowledge-eval/scripts/check_coverage_matrix.py
    python knowledge-eval/scripts/check_coverage_matrix.py --nodes data/nodes_math_6up.json \
        --questions data/questions_math_6up.json --out-reports reports/

Writes `reports/coverage-matrix.json` (every cell, machine readable) and `reports/coverage-matrix.md`
(summary, the target chapter's cell-by-cell table, and the top-up list).

File I/O only: no database, no network. Exit code 1 only when the inputs cannot be read; a thin bank
is data, not an error — it shows up in the report.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import keval  # noqa: E402
from server.app import mastery  # noqa: E402

#: Which ability lines a subject is allowed to probe. Maths asks for meaning, execution, context and
#: variation; 语文/英语 have no execution line, so their four are language use, reading, judgement and
#: expression. A subject cannot use the other subject's tags (schema allows all eight, data does not).
ABILITY_LINES = {
    "math": ("理解意义", "执行计算", "情境应用", "辨析变式"),
    "chinese": ("语言运用", "阅读理解", "辨析判断", "综合表达"),
    "english": ("语言运用", "阅读理解", "辨析判断", "综合表达"),
}

DIFFICULTIES = (("easy", "易", 1), ("medium", "中", 2), ("hard", "难", 3))

#: 最低档: one of each difficulty; 稳健档: two of each, so a pack has something to rotate.
FLOOR = {"easy": 1, "medium": 1, "hard": 1}
ROBUST = {"easy": 2, "medium": 2, "hard": 2}

STAGE_WORDS = ("0-未掌握", "1-基础", "2-熟练", "3-拓展")


def reaches(n_e: int, n_m: int, n_h: int) -> dict[int, dict]:
    """Every level this cell can produce, with one witness answer pattern per level.

    Rows go through `mastery.summarise` so the rounding is the server's, not this script's. A subset
    smaller than `MIN_SAMPLE` is skipped: it would read 待测, which is not a stage.
    """
    weights = {key: mastery.WEIGHT_X10[level] for key, _, level in DIFFICULTIES}
    counts = {"easy": n_e, "medium": n_m, "hard": n_h}
    found: dict[int, dict] = {}
    for k_e, k_m, k_h in itertools.product(range(n_e + 1), range(n_m + 1), range(n_h + 1)):
        chosen = {"easy": k_e, "medium": k_m, "hard": k_h}
        rows = []
        for key, _, _ in DIFFICULTIES:
            rows += [{"is_correct": 1, "weight_x10": weights[key]}] * chosen[key]
            rows += [{"is_correct": 0, "weight_x10": weights[key]}] * (counts[key] - chosen[key])
        if len(rows) < mastery.MIN_SAMPLE:
            continue
        summary = mastery.summarise(rows)
        level = mastery.level_of(summary["acc_x1000"], summary["hard_right"])
        # Keep the witness that uses the most questions: it is the one a real session would produce.
        if level not in found or len(rows) > found[level]["sample"]:
            found[level] = {
                "sample": len(rows),
                "rate_x1000": summary["acc_x1000"],
                "rate": round(summary["acc_x1000"] / 1000, 3),
                "hard_right": summary["hard_right"],
                "correct": {key: chosen[key] for key, _, _ in DIFFICULTIES},
            }
    return found


def gap_reason(cell: dict) -> str:
    """One line a reviewer can act on: why this cell is not covered, in that order of severity."""
    counts = cell["questions"]
    if counts["total"] == 0:
        return "无题"
    if not cell["measured"]:
        return f"待测：{counts['total']} 题，不足 {mastery.MIN_SAMPLE} 条样本"
    missing = cell["missing_levels"]
    if missing:
        causes = []
        if counts["hard"] == 0 and 3 in missing:
            causes.append("无难题")
        if counts["medium"] == 0:
            causes.append("无中题")
        if counts["easy"] == 0:
            causes.append("无易题")
        names = "/".join(str(level) for level in missing)
        return f"{names} 级不可达" + (f"（{'；'.join(causes)}）" if causes else "")
    if not cell["robust"]["ok"]:
        need = cell["robust"]["need"]
        return "稳健档不足：需补 " + "、".join(
            f"{word} {need[key]}" for key, word, _ in DIFFICULTIES if need[key])
    return ""


def deficit(counts: dict[str, int], want: dict[str, int]) -> dict[str, int]:
    return {key: max(0, want[key] - counts[key]) for key in want}


def analyse(nodes: list[dict], questions: list[dict]) -> dict:
    diagnostics = [node for node in nodes if node.get("node_type") == "diagnostic"]
    aggregates = [node for node in nodes if node.get("node_type") == "aggregate"]

    pool: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for question in questions:
        pool[(question["diagnostic_node_id"], question["ability_tag"])].append(question)

    cells: list[dict] = []
    for node in diagnostics:
        for ability in ABILITY_LINES.get(node.get("subject"), ()):
            items = pool.get((node["node_id"], ability), [])
            counts = {key: sum(1 for q in items if q["difficulty_preset"] == word)
                      for key, word, _ in DIFFICULTIES}
            total = sum(counts.values())
            stages = reaches(counts["easy"], counts["medium"], counts["hard"])
            four_stage = all(level in stages for level in range(4))
            floor_need = deficit(counts, FLOOR)
            robust_need = deficit(counts, ROBUST)
            cells.append({
                "subject": node["subject"],
                "diagnostic_node_id": node["node_id"],
                "node_name": node["name"],
                "knowledge_type": node.get("knowledge_type"),
                "parent_aggregate_node_id": node.get("parent_id"),
                "ability_tag": ability,
                "questions": {"easy": counts["easy"], "medium": counts["medium"],
                              "hard": counts["hard"], "total": total},
                "question_ids": [q["question_id"] for q in items],
                "measured": total >= mastery.MIN_SAMPLE,
                "stages": {str(level): stages.get(level) for level in range(4)},
                "reachable_levels": sorted(stages),
                "four_stage": four_stage,
                "floor": {"ok": four_stage and not any(floor_need.values()),
                          "need": floor_need},
                "robust": {"ok": four_stage and not any(robust_need.values()),
                           "need": robust_need},
                "missing_levels": [level for level in range(4) if level not in stages],
            })

    children: dict[str, list[str]] = defaultdict(list)
    for node in diagnostics:
        children[node.get("parent_id")].append(node["node_id"])
    chapters = [{
        "aggregate_node_id": node["node_id"],
        "aggregate_name": node["name"],
        "subject": node["subject"],
        "diagnostic_count": len(children.get(node["node_id"], [])),
        "diagnostic_node_ids": children.get(node["node_id"], []),
        "chapter_order": node["chapter_order"],
    } for node in aggregates]

    # The same arithmetic one level up: mastery's window is per diagnostic node, so a node whose four
    # cells are each 1E/1M/1H has nine questions and can reach every stage even though no single cell
    # can. Reported separately so the cell table is not mistaken for the whole story.
    node_rows = []
    for node in diagnostics:
        counts = {"easy": 0, "medium": 0, "hard": 0}
        for ability in ABILITY_LINES.get(node.get("subject"), ()):
            for item in pool.get((node["node_id"], ability), []):
                for key, word, _ in DIFFICULTIES:
                    if item["difficulty_preset"] == word:
                        counts[key] += 1
        total = sum(counts.values())
        stages = reaches(counts["easy"], counts["medium"], counts["hard"])
        node_rows.append({
            "subject": node["subject"],
            "diagnostic_node_id": node["node_id"],
            "node_name": node["name"],
            "parent_aggregate_node_id": node.get("parent_id"),
            "questions": {**counts, "total": total},
            "reachable_levels": sorted(stages),
            "four_stage": all(level in stages for level in range(4)),
        })

    return {"cells": cells, "nodes": node_rows, "chapters": chapters, "generated_from": {
        "diagnostic_nodes": len(diagnostics),
        "aggregate_nodes": len(aggregates),
        "questions": len(questions),
    }}


def summarise_cells(cells: list[dict], subjects=keval.SUBJECTS) -> dict:
    def block(rows: list[dict]) -> dict:
        return {
            "cells": len(rows),
            "no_question": sum(1 for c in rows if c["questions"]["total"] == 0),
            "measured": sum(1 for c in rows if c["measured"]),
            "floor_ok": sum(1 for c in rows if c["floor"]["ok"]),
            "robust_ok": sum(1 for c in rows if c["robust"]["ok"]),
            "p0": sum(1 for c in rows if not c["measured"]),
            "p1": sum(1 for c in rows if c["measured"] and not c["four_stage"]),
            "p2": sum(1 for c in rows if c["floor"]["ok"] and not c["robust"]["ok"]),
        }

    by_subject = OrderedDict((subject, block([c for c in cells if c["subject"] == subject]))
                             for subject in subjects)
    return {"all": block(cells), "by_subject": by_subject}


def topup(cells: list[dict], want: dict[str, int]) -> dict:
    """Questions to add, per (node, ability, difficulty), for one of the two standards."""
    rows = []
    for cell in cells:
        need = deficit(cell["questions"], want)
        if not any(need.values()):
            continue
        rows.append({
            "subject": cell["subject"],
            "diagnostic_node_id": cell["diagnostic_node_id"],
            "ability_tag": cell["ability_tag"],
            "easy": need["easy"], "medium": need["medium"], "hard": need["hard"],
            "total": sum(need.values()),
        })
    total = sum(row["total"] for row in rows)
    by_subject = OrderedDict(
        (subject, sum(row["total"] for row in rows if row["subject"] == subject))
        for subject in keval.SUBJECTS)
    by_difficulty = {key: sum(row[key] for row in rows) for key, _, _ in DIFFICULTIES}
    return {"rows": rows, "total": total, "by_subject": by_subject, "by_difficulty": by_difficulty}


def pick_target(chapters: list[dict], override: str | None = None) -> dict | None:
    """The chapter with the most diagnostic nodes; ties go to the earliest chapter."""
    usable = [c for c in chapters if c["diagnostic_count"] >= 3]
    if override:
        for chapter in chapters:
            if chapter["aggregate_node_id"] == override:
                return chapter
        raise SystemExit(f"no aggregate node {override!r}")
    if not usable:
        return None
    # Ties go to the earliest chapter by chapter_order, not by slicing the id: `-u-review` has no
    # number to slice, and the review unit is exactly the kind of chapter that reaches this branch.
    return max(usable, key=lambda c: (c["diagnostic_count"], -c["chapter_order"]))


def markdown(result: dict, target: dict | None) -> str:
    cells = result["cells"]
    stats = result["summary"]
    lines: list[str] = []
    add = lines.append

    add("# 覆盖矩阵（由 check_coverage_matrix.py 生成，勿手改）")
    add("")
    add(f"节点 {result['generated_from']['diagnostic_nodes']} 个诊断 / "
        f"{result['generated_from']['aggregate_nodes']} 个聚合，题目 "
        f"{result['generated_from']['questions']} 道。")
    add("")
    add("## 1. 汇总")
    add("")
    add("| 范围 | 格子 | 0 题 | n≥3 | 最低档达标 | 稳健档达标 | P0（n<3） | P1（阶段不全） | P2（仅缺稳健档） |")
    add("|---|---|---|---|---|---|---|---|---|")
    rows = [("全部", stats["all"])] + [(s, stats["by_subject"][s]) for s in stats["by_subject"]]
    for name, block in rows:
        add(f"| {name} | {block['cells']} | {block['no_question']} | {block['measured']} | "
            f"{block['floor_ok']} | {block['robust_ok']} | {block['p0']} | {block['p1']} | {block['p2']} |")
    add("")

    add("## 2. 聚合节点下的诊断节点数")
    add("")
    add("| 聚合节点 | 学科 | 诊断节点数 | ≥3 | 诊断节点 |")
    add("|---|---|---|---|---|")
    for chapter in result["chapters"]:
        add(f"| `{chapter['aggregate_node_id']}` {chapter['aggregate_name']} | {chapter['subject']} | "
            f"{chapter['diagnostic_count']} | {'是' if chapter['diagnostic_count'] >= 3 else '否'} | "
            f"{'、'.join(chapter['diagnostic_node_ids']) or '—'} |")
    add("")

    if target is None:
        add("## 3. 目标聚合节点")
        add("")
        add("没有任何聚合节点下挂 ≥3 个诊断节点。")
        add("")
    else:
        add(f"## 3. 目标聚合节点：`{target['aggregate_node_id']}` {target['aggregate_name']}"
            f"（{target['diagnostic_count']} 个诊断节点）")
        add("")
        nodes = target["diagnostic_node_ids"][:3]
        add("前三个诊断节点的逐格矩阵：")
        add("")
        add("| 诊断节点 | 能力标签 | n_e | n_m | n_h | 0级 | 1级 | 2级 | 3级 | 最低档 | 稳健档 |")
        add("|---|---|---|---|---|---|---|---|---|---|---|")
        for node_id in nodes:
            for cell in (c for c in cells if c["diagnostic_node_id"] == node_id):
                marks = ["是" if str(level) in {k for k, v in cell["stages"].items() if v}
                         else "否" for level in range(4)]
                add(f"| `{node_id}` | {cell['ability_tag']} | {cell['questions']['easy']} | "
                    f"{cell['questions']['medium']} | {cell['questions']['hard']} | "
                    f"{' | '.join(marks)} | {'是' if cell['floor']['ok'] else '否'} | "
                    f"{'是' if cell['robust']['ok'] else '否'} |")
        add("")

    nodes = result["nodes"]
    add("## 4. 节点粒度（mastery 的实际窗口单位）")
    add("")
    add("判档的窗口是「一个诊断节点的最近十条作答」，不区分能力线，所以节点级可比格子级宽松：")
    add("一个节点的四条能力线各 1 易 1 中 1 难时，节点共有 9 题，节点级四阶段可达，但没有一条能力线"
        "能单独构造。")
    add("")
    add("| 学科 | 诊断节点 | 有题（n≥1） | n≥3 | 四阶段可达 |")
    add("|---|---|---|---|---|")
    for subject in keval.SUBJECTS:
        mine = [n for n in nodes if n["subject"] == subject]
        add(f"| {subject} | {len(mine)} | {sum(1 for n in mine if n['questions']['total'] >= 1)} | "
            f"{sum(1 for n in mine if n['questions']['total'] >= mastery.MIN_SAMPLE)} | "
            f"{sum(1 for n in mine if n['four_stage'])} |")
    add("")
    add("四阶段可达的节点：" + ("、".join(f"`{n['diagnostic_node_id']}`"
                                       for n in nodes if n["four_stage"]) or "无") + "。")
    add("")

    add("## 5. 补题清单（按 节点 × 能力 × 难度）")
    add("")
    for title, key in (("为达到最低档", "floor_topup"), ("为达到稳健档", "robust_topup")):
        block = result[key]
        add(f"### {title}：共 {block['total']} 题（"
            + "，".join(f"{k} {v}" for k, v in block["by_subject"].items())
            + f"；易 {block['by_difficulty']['easy']} / 中 {block['by_difficulty']['medium']} / "
              f"难 {block['by_difficulty']['hard']}）")
        add("")
        add("| 诊断节点 | 能力标签 | 补易 | 补中 | 补难 | 小计 |")
        add("|---|---|---|---|---|---|")
        for row in block["rows"]:
            if row["subject"] not in ("math",):
                continue
            add(f"| `{row['diagnostic_node_id']}` | {row['ability_tag']} | {row['easy']} | "
                f"{row['medium']} | {row['hard']} | {row['total']} |")
        add("")
        add("（上表只列数学；语数英全量见 `coverage-matrix.json` 的 "
            f"`{key}.rows`。）")
        add("")

    add("## 6. 缺口明细（P0 / P1 / P2 逐格）")
    add("")
    for title, predicate in (
            ("P0 — n<3，连薄弱项都产生不了", lambda c: not c["measured"]),
            ("P1 — n≥3 但四阶段不全", lambda c: c["measured"] and not c["four_stage"]),
            ("P2 — 最低档达标但稳健档不达标", lambda c: c["floor"]["ok"] and not c["robust"]["ok"])):
        rows = [cell for cell in cells if predicate(cell)]
        add(f"### {title}：{len(rows)} 格")
        add("")
        if not rows:
            add("无。")
            add("")
            continue
        add("| 诊断节点 | 能力标签 | n_e | n_m | n_h | 到最低档 易/中/难 | 原因 |")
        add("|---|---|---|---|---|---|---|")
        for cell in rows:
            need = cell["floor"]["need"]
            add(f"| `{cell['diagnostic_node_id']}` | {cell['ability_tag']} | "
                f"{cell['questions']['easy']} | {cell['questions']['medium']} | "
                f"{cell['questions']['hard']} | {need['easy']}/{need['medium']}/{need['hard']} | "
                f"{gap_reason(cell)} |")
        add("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="four-stage coverage matrix")
    parser.add_argument("--nodes", nargs="*", default=None)
    parser.add_argument("--questions", nargs="*", default=None)
    parser.add_argument("--out-reports", default=str(keval.KE / "reports"))
    parser.add_argument("--target-aggregate", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    nodes = [node for _, payload in keval.load_nodes(args.nodes) for node in payload["nodes"]]
    questions = [q for _, payload in keval.load_questions(args.questions) for q in payload["questions"]]

    result = analyse(nodes, questions)
    cells = result["cells"]
    result["summary"] = summarise_cells(cells)
    result["target"] = pick_target(result["chapters"], args.target_aggregate)
    result["floor_topup"] = topup(cells, FLOOR)
    result["robust_topup"] = topup(cells, ROBUST)

    out = Path(args.out_reports)
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage-matrix.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "coverage-matrix.md").write_text(markdown(result, result["target"]), encoding="utf-8")

    if not args.quiet:
        block = result["summary"]["all"]
        print(f"cells {block['cells']}: {block['no_question']} without a question, "
              f"{block['measured']} measured (n>=3), {block['floor_ok']} floor-ok, "
              f"{block['robust_ok']} robust-ok")
        for subject, part in result["summary"]["by_subject"].items():
            print(f"  {subject:<8} {part['cells']:>3} cells  n>=3 {part['measured']:>3}  "
                  f"floor {part['floor_ok']:>3}  robust {part['robust_ok']:>3}")
        target = result["target"]
        if target:
            print(f"target chapter {target['aggregate_node_id']} {target['aggregate_name']} "
                  f"({target['diagnostic_count']} diagnostics)")
        else:
            print("no chapter has 3 diagnostic nodes")
        nodes = result["nodes"]
        print(f"node level: {sum(1 for n in nodes if n['questions']['total'] >= 1)} of {len(nodes)} "
              f"nodes have a question, {sum(1 for n in nodes if n['four_stage'])} reach four stages")
        print(f"top-up to floor: {result['floor_topup']['total']} questions; "
              f"to robust: {result['robust_topup']['total']}")
        print(f"wrote {out / 'coverage-matrix.json'} and {out / 'coverage-matrix.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
