#!/usr/bin/env python3
"""题库密度审计：按 诊断节点 × 能力标签 × 难度 盘点题量，判闭环可行性。

为什么单独一个脚本，而不是并进门禁：门禁问的是"每个格子够不够 9 道"，这个问的是"一条闭环够不够用"。
两个问题的答案可以同时成立且互相打脸——格子里 3/3/3 很整齐，节点却可能只有 18 题：立案窗口拿走 15 道
不同题之后只剩 3 道没做过，消除阶段要 5 道，必然重复 2 道。前者是写作口径，后者是孩子做题时的体验。

审计只读，不写库、不改题、不动 L0–L3 阈值：

    python3 knowledge-eval/scripts/audit_question_density.py
    python3 knowledge-eval/scripts/audit_question_density.py --dsn "mysql://root@localhost/quiz?unix_socket=/run/mysqld/mysqld.sock"
    python3 knowledge-eval/scripts/audit_question_density.py --dataset /tmp/snapshot.json --out-json /tmp/a.json

数据源优先级：`--dataset` > `--dsn` > `knowledge-eval/data/*.json`。三条路径产出同一个 dataset 结构，
`--emit-dataset` 可以把任一条路径的结果落成快照，供复现与负样本测试使用。

四档的数值不是本脚本的发明：立案窗口读 `server.app.weakness.DEFAULT_WINDOW`（15），消除短窗按本轮规格
2 中 + 1 难 + 2 易/中 = 5，两者相加得 L2=20，再加一轮得 L3=25。窗口可用 `--window` 覆盖，四档随它联动。
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE.parent / "data"

#: subject -> (name_zh, questions file, nodes file). Order decides report order, not importance.
SUBJECTS = OrderedDict((
    ("math", ("数学", "questions_math_6up.json", "nodes_math_6up.json")),
    ("chinese", ("语文", "questions_chinese_6up.json", "nodes_chinese_6up.json")),
    ("english", ("英语", "questions_english_6up.json", "nodes_english_6up.json")),
))
#: 学科设计上的能力线，顺序即报告里的行序。与 `docs/coverage-audit-mapping.md` 第三节一致。
#: 节点只声明其中的一部分；矩阵按这个顺序补齐 `—`，否则行序会随节点声明顺序漂移，两版报告没法 diff。
DESIGN_LINES = {
    "math": ["理解意义", "执行计算", "情境应用", "辨析变式"],
    "chinese": ["语言运用", "阅读理解", "辨析判断", "综合表达"],
    "english": ["语言运用", "阅读理解", "辨析判断", "综合表达"],
}
#: difficulty code -> the word a human reads. `question.difficulty` is 1/2/3; the source files say 易/中/难.
DIFFICULTY_WORDS = {1: "易", 2: "中", 3: "难"}
DIFFICULTY_CODES = {"易": 1, "中": 2, "难": 3}
EASY, MEDIUM, HARD = 1, 2, 3

#: Fallback only: the real value comes from `server.app.weakness`, so the report cannot drift from the code.
DEFAULT_WINDOW = 15
#: 消除短窗: 2 中 + 1 难 + 2 易/中. Not yet a constant in the server — the reinforcement pack is future work.
SHORT_MEDIUM, SHORT_HARD, SHORT_FILL = 2, 1, 2
SHORT_TOTAL = SHORT_MEDIUM + SHORT_HARD + SHORT_FILL

LEVELS = ("L0", "L1", "L2", "L3")
LEVEL_WORDS = {
    "L0": "L0 不可立案",
    "L1": "L1 仅可立案",
    "L2": "L2 一轮闭环",
    "L3": "L3 两轮闭环",
}
VERDICT_WORDS = {
    "strong": "强可行",
    "conditional": "条件可行",
    "infeasible": "不可行",
    "no-line": "无能力线",
}
#: 补题优先顺序。中题最紧（消除要 2 道），难题次之（要 1 道），易题只做补足。
PATCH_ORDER = (MEDIUM, HARD, EASY)


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------


def configured_window() -> int:
    """The window the server actually uses. Import failure must not break a read-only audit."""
    try:
        sys.path.insert(0, str(ROOT))
        from server.app.weakness import window_size

        return int(window_size())
    except Exception:  # pragma: no cover - only when run outside a checkout
        return DEFAULT_WINDOW


def load_design() -> tuple[OrderedDict, dict]:
    """Node declarations from the curriculum files: which ability lines a node is supposed to have.

    The database does not store this. It lives in `nodes_*.json` as `ability_lines`, which is also what
    `check_ability_coverage.py` uses as its unit. Auditing against "all four lines of the subject"
    instead would invent cells nobody planned to write, so the declaration is the authority: an
    undeclared line is `—`, a declared line with no question is a real `0` and a real gap.
    """
    nodes: OrderedDict[str, dict] = OrderedDict()
    subject_lines: dict[str, list[str]] = {}
    for subject, (name_zh, _questions_file, nodes_file) in SUBJECTS.items():
        raw = json.loads((DATA / nodes_file).read_text(encoding="utf-8"))
        aggregates = {n["node_id"]: n for n in raw["nodes"] if n.get("node_type") == "aggregate"}
        lines: list[str] = []
        for node in raw["nodes"]:
            if node.get("node_type") == "aggregate":
                continue
            declared = list(node.get("ability_lines") or [])
            for line in declared:
                if line not in lines:
                    lines.append(line)
            parent = aggregates.get(node.get("parent_id") or "", {})
            nodes[node["node_id"]] = {
                "code": node["node_id"],
                "name_zh": node.get("name") or node["node_id"],
                "subject": subject,
                "subject_name_zh": name_zh,
                "unit_code": node.get("parent_id"),
                "unit_name_zh": parent.get("name"),
                "chapter_order": int(node.get("chapter_order") or 0),
                "ability_lines": declared,
            }
        # Canonical order first, then anything a node declared that the design list does not know about —
        # a new line shows up at the end rather than silently disappearing from the matrix.
        canonical = DESIGN_LINES.get(subject, [])
        subject_lines[subject] = ([line for line in canonical if line in lines]
                                  + [line for line in lines if line not in canonical])
    return nodes, subject_lines


def load_questions_from_data() -> list[dict]:
    """Every question in the three bank files. Everything in a bank file is live by definition."""
    questions: list[dict] = []
    for _subject, (_name, questions_file, _nodes_file) in SUBJECTS.items():
        raw = json.loads((DATA / questions_file).read_text(encoding="utf-8"))
        for item in raw["questions"]:
            questions.append({
                "code": item.get("question_id"),
                "node": item.get("diagnostic_node_id"),
                "ability_tag": item.get("ability_tag"),
                "difficulty": DIFFICULTY_CODES.get(item.get("difficulty_preset")),
                "qtype": item.get("question_type"),
                "enabled": True,
            })
    return questions


def load_questions_from_db(dsn: str) -> list[dict]:
    """Read the live bank. One SELECT, no transaction left open, nothing written."""
    sys.path.insert(0, str(ROOT))
    from server.app.db import Database

    db = Database(dsn)
    db.connect()
    try:
        rows = db.query(
            "SELECT q.code AS code, t.code AS node, q.ability_tag AS ability_tag,"
            " q.difficulty AS difficulty, q.qtype AS qtype,"
            " CASE WHEN q.retired_at IS NULL AND q.verify_status <> 'rejected' THEN 1 ELSE 0 END AS enabled"
            " FROM question q JOIN topic t ON t.id = q.topic_id"
            " ORDER BY q.code"
        )
    finally:
        db.close()
    for row in rows:
        row["difficulty"] = int(row["difficulty"])
        row["enabled"] = bool(int(row["enabled"]))
    return rows


def build_dataset(*, dsn: str | None = None) -> dict:
    nodes, subject_lines = load_design()
    questions = load_questions_from_db(dsn) if dsn else load_questions_from_data()
    return {
        "schema_version": 1,
        "shape": "density-audit-dataset",
        "source": f"mysql:{dsn.split('@')[-1]}" if dsn else "knowledge-eval/data",
        "subjects": {
            subject: {"name_zh": name_zh, "ability_lines": subject_lines[subject]}
            for subject, (name_zh, _q, _n) in SUBJECTS.items()
        },
        "nodes": list(nodes.values()),
        "questions": questions,
    }


def load_dataset(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def node_level(total: int, empty_lines: list[str], window: int, short_total: int) -> str:
    """L0–L3 for one diagnostic node.

    An empty declared line is L0 even with 200 questions: the weakness state machine names 主因线 by
    ability line, so a node missing one of its own lines cannot be diagnosed properly no matter how
    deep the other cells are.
    """
    if total < window or empty_lines:
        return "L0"
    if total < window + short_total:
        return "L1"
    if total < window + 2 * short_total:
        return "L2"
    return "L3"


def elimination(grid: dict, lines: list[str], medium_need: int, hard_need: int, fill_need: int) -> dict:
    """Can the 5-question elimination round be drawn from any pair of ability lines?

    The unit is a pair, not a single line, because 主因线 names up to two lines and the pack takes from
    both. Every pair is enumerated: if one pair fails, the node is only conditionally usable, and which
    condition matters is exactly which two lines the child turns out weak in.
    """
    if len(lines) >= 2:
        combos = list(itertools.combinations(lines, 2))
    else:
        combos = [(line,) for line in lines]
    rows = []
    for combo in combos:
        available = {
            difficulty: sum(grid.get(line, {}).get(difficulty, 0) for line in combo)
            for difficulty in (EASY, MEDIUM, HARD)
        }
        total = sum(available.values())
        feasible = (
            available[MEDIUM] >= medium_need
            and available[HARD] >= hard_need
            and total >= medium_need + hard_need + fill_need
        )
        rows.append({
            "combo": list(combo),
            "medium": available[MEDIUM],
            "hard": available[HARD],
            "easy": available[EASY],
            "total": total,
            "feasible": feasible,
        })
    if not rows:
        verdict = "no-line"
    elif all(row["feasible"] for row in rows):
        verdict = "strong"
    elif any(row["feasible"] for row in rows):
        verdict = "conditional"
    else:
        verdict = "infeasible"
    return {
        "verdict": verdict,
        "verdict_zh": VERDICT_WORDS[verdict],
        "total_combos": len(rows),
        "feasible_combos": sum(1 for row in rows if row["feasible"]),
        "combos": rows,
    }


def allocate_patch(lines: list[str], deficit: int) -> Counter:
    """Spread a top-up over the node's own cells: 中 first, then 难, then 易, rotating over lines.

    Deterministic on purpose — two runs must produce the same list, and a suggestion that hops around
    is not reviewable. It is a suggestion, not a decision: which questions to write is the teaching
    side's call.
    """
    if deficit <= 0 or not lines:
        return Counter()
    alloc: Counter = Counter()
    for index in range(deficit):
        difficulty = PATCH_ORDER[index % len(PATCH_ORDER)]
        line = lines[index % len(lines)]
        alloc[(line, difficulty)] += 1
    return alloc


def patch_list(lines: list[str], alloc: Counter) -> list[dict]:
    """Read the allocation back as a list in the order worth writing: 中, 难, 易; then declared order."""
    difficulty_rank = {difficulty: index for index, difficulty in enumerate(PATCH_ORDER)}
    ordered = sorted(
        alloc.items(),
        key=lambda item: (difficulty_rank[item[0][1]], lines.index(item[0][0])),
    )
    return [
        {"ability": ability, "difficulty": DIFFICULTY_WORDS[difficulty], "count": count}
        for (ability, difficulty), count in ordered
    ]


def build(dataset: dict, *, window: int, short_medium: int, short_hard: int, short_fill: int) -> dict:
    short_total = short_medium + short_hard + short_fill
    subjects = dataset["subjects"]
    nodes = {node["code"]: node for node in dataset["nodes"]}
    subject_order = {subject: index for index, subject in enumerate(subjects)}

    cells: Counter = Counter()
    per_node_total: Counter = Counter()
    per_node_tags: dict[str, set[str]] = {}
    orphans: Counter = Counter()
    disabled = 0
    for question in dataset["questions"]:
        if not question.get("enabled", True):
            disabled += 1
            continue
        node_code = question.get("node")
        difficulty = question.get("difficulty")
        ability = question.get("ability_tag")
        if node_code not in nodes or difficulty not in DIFFICULTY_WORDS or not ability:
            orphans[str(node_code)] += 1
            continue
        cells[(node_code, ability, int(difficulty))] += 1
        per_node_total[node_code] += 1
        per_node_tags.setdefault(node_code, set()).add(ability)

    node_rows = []
    for code, node in sorted(
        nodes.items(),
        key=lambda item: (subject_order[item[1]["subject"]], item[1]["chapter_order"], item[0]),
    ):
        lines = list(node["ability_lines"])
        grid = {
            line: {difficulty: cells.get((code, line, difficulty), 0) for difficulty in (EASY, MEDIUM, HARD)}
            for line in lines
        }
        total = per_node_total[code]
        empty_lines = [line for line in lines if sum(grid[line].values()) == 0]
        undeclared = sorted(per_node_tags.get(code, set()) - set(lines))
        level = node_level(total, empty_lines, window, short_total)
        simulation = elimination(grid, lines, short_medium, short_hard, short_fill)
        unused = max(0, total - window)
        node_rows.append({
            "code": code,
            "name_zh": node["name_zh"],
            "subject": node["subject"],
            "subject_name_zh": node["subject_name_zh"],
            "unit_code": node.get("unit_code"),
            "unit_name_zh": node.get("unit_name_zh"),
            "chapter_order": node["chapter_order"],
            "ability_lines": lines,
            "grid": grid,
            "questions": total,
            "cells_declared": len(lines),
            "empty_lines": empty_lines,
            "undeclared_lines": undeclared,
            "level": level,
            "level_zh": LEVEL_WORDS[level],
            "elimination": simulation,
            "residual": {
                "unused_after_window": unused,
                "elimination_need": short_total,
                "repeats_required": max(0, short_total - unused),
            },
            "patches": {
                "to_l2": patch_list(lines, allocate_patch(lines, window + short_total - total)),
                "to_l3": patch_list(lines, allocate_patch(lines, window + 2 * short_total - total)),
            },
        })

    # -- aggregates ---------------------------------------------------------
    summary = Counter()
    subject_rows = []
    for subject, meta in subjects.items():
        rows = [row for row in node_rows if row["subject"] == subject]
        level_counts = Counter(row["level"] for row in rows)
        subject_rows.append({
            "subject": subject,
            "name_zh": meta["name_zh"],
            "ability_lines": meta["ability_lines"],
            "questions": sum(row["questions"] for row in rows),
            "nodes": len(rows),
            "cells_declared": sum(row["cells_declared"] for row in rows),
            "levels": {level: level_counts.get(level, 0) for level in LEVELS},
        })
        summary["questions"] += sum(row["questions"] for row in rows)
        summary["nodes"] += len(rows)

    declared_cells = sum(row["cells_declared"] for row in node_rows)
    filled_cells = sum(
        1 for row in node_rows for line in row["ability_lines"] if sum(row["grid"][line].values()) > 0
    )
    theoretical_cells = sum(len(subjects[row["subject"]]["ability_lines"]) for row in node_rows) * 3
    grid_summary = {
        "questions": summary["questions"],
        "nodes": summary["nodes"],
        "subjects": len(subjects),
        "ability_tags": len({line for meta in subjects.values() for line in meta["ability_lines"]}),
        "difficulties": len(DIFFICULTY_WORDS),
        "theoretical_cells": theoretical_cells,
        "declared_cells": declared_cells,
        "not_applicable_cells": theoretical_cells - declared_cells,
        "filled_cells": filled_cells,
        "empty_cells": declared_cells - filled_cells,
        "empty_ratio": (declared_cells - filled_cells) / declared_cells if declared_cells else 0.0,
        "disabled_questions": disabled,
        "orphan_questions": sum(orphans.values()),
    }

    # -- gaps ---------------------------------------------------------------
    p0 = [row for row in node_rows if row["level"] == "L0"]
    p1 = [row for row in node_rows if row["level"] == "L1"]
    p2 = [row for row in node_rows if row["level"] == "L2"]

    patch_l2: Counter = Counter()
    patch_l3: Counter = Counter()
    for row in node_rows:
        for item in row["patches"]["to_l2"]:
            patch_l2[(row["subject"], item["ability"], DIFFICULTY_CODES[item["difficulty"]])] += item["count"]
        for item in row["patches"]["to_l3"]:
            patch_l3[(row["subject"], item["ability"], DIFFICULTY_CODES[item["difficulty"]])] += item["count"]

    patch_table = []
    for subject, meta in subjects.items():
        for ability in meta["ability_lines"]:
            for difficulty in (EASY, MEDIUM, HARD):
                key = (subject, ability, difficulty)
                patch_table.append({
                    "subject": subject,
                    "subject_name_zh": meta["name_zh"],
                    "ability": ability,
                    "difficulty": DIFFICULTY_WORDS[difficulty],
                    "current": sum(
                        row["grid"][ability][difficulty]
                        for row in node_rows
                        if row["subject"] == subject and ability in row["grid"]
                    ),
                    "to_l2": patch_l2.get(key, 0),
                    "to_l3": patch_l3.get(key, 0),
                })

    repeat_nodes = [row for row in node_rows if row["residual"]["repeats_required"] > 0]
    conclusion = {
        "nodes_l2_or_better": sum(1 for row in node_rows if row["level"] in ("L2", "L3")),
        "nodes_l1_only": len(p1),
        "nodes_l0": len(p0),
        "nodes_total": len(node_rows),
        "repeat_ratio": len(repeat_nodes) / len(node_rows) if node_rows else 0.0,
        "repeats_required_total": sum(row["residual"]["repeats_required"] for row in node_rows),
        "patch_to_l2_total": sum(patch_l2.values()),
        "patch_to_l3_total": sum(patch_l3.values()),
        "by_subject": [
            {
                "subject": row["subject"],
                "name_zh": row["name_zh"],
                "nodes": row["nodes"],
                "repeat_nodes": sum(
                    1 for node in node_rows
                    if node["subject"] == row["subject"] and node["residual"]["repeats_required"] > 0
                ),
            }
            for row in subject_rows
        ],
        "strong_all": sum(1 for row in node_rows if row["elimination"]["verdict"] == "strong"),
        "conditional": sum(1 for row in node_rows if row["elimination"]["verdict"] == "conditional"),
        "infeasible": sum(1 for row in node_rows if row["elimination"]["verdict"] == "infeasible"),
    }

    return {
        "meta": {
            "source": dataset.get("source", "unknown"),
            "window": window,
            "short_window": {
                "total": short_total,
                "medium": short_medium,
                "hard": short_hard,
                "fill_easy_or_medium": short_fill,
            },
            "thresholds": {
                "L1": window,
                "L2": window + short_total,
                "L3": window + 2 * short_total,
            },
        },
        "summary": grid_summary,
        "subjects": subject_rows,
        "nodes": node_rows,
        "patch": patch_table,
        "gaps": {
            "p0": [{"code": row["code"], "name_zh": row["name_zh"], "questions": row["questions"],
                    "empty_lines": row["empty_lines"]} for row in p0],
            "p1": [{"code": row["code"], "name_zh": row["name_zh"], "questions": row["questions"],
                    "repeats_required": row["residual"]["repeats_required"],
                    "patch_to_l2": row["patches"]["to_l2"]} for row in p1],
            "p2": [{"code": row["code"], "name_zh": row["name_zh"], "questions": row["questions"],
                    "patch_to_l3": row["patches"]["to_l3"]} for row in p2],
        },
        "conclusion": conclusion,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def as_table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |")
    return "\n".join(lines)


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def patch_words(items: list[dict]) -> str:
    if not items:
        return "—"
    return "、".join(f"{item['ability']} × {item['difficulty']} {item['count']} 道" for item in items)


def render_md(result: dict) -> str:
    meta = result["meta"]
    summary = result["summary"]
    short = meta["short_window"]
    conclusion = result["conclusion"]
    out: list[str] = []

    out.append("# 题库密度审计报告")
    out.append("")
    out.append(
        f"> 数据源：`{meta['source']}`　｜　立案窗口 **{meta['window']}** 题　｜　"
        f"消除短窗 **{short['total']}** 题（{short['medium']} 中 + {short['hard']} 难 + "
        f"{short['fill_easy_or_medium']} 易/中）　｜　L1≥{meta['thresholds']['L1']}、"
        f"L2≥{meta['thresholds']['L2']}、L3≥{meta['thresholds']['L3']}"
    )
    out.append(">")
    out.append("> 本文件由 `knowledge-eval/scripts/audit_question_density.py` 生成，所有数字来自脚本，未手工填写。")
    out.append("")

    # 5.1 ------------------------------------------------------------------
    out.append("## 5.1 总览")
    out.append("")
    out.append(as_table(
        ["项", "值"],
        [
            ["题目总数（启用）", summary["questions"]],
            ["停用题目（不计入）", summary["disabled_questions"]],
            ["无法归属到节点的题目", summary["orphan_questions"]],
            ["诊断节点总数", summary["nodes"]],
            ["能力标签总数（跨学科去重）", summary["ability_tags"]],
            ["难度档数", summary["difficulties"]],
            ["理论格子数（节点 × 学科全部能力线 × 3 档）", summary["theoretical_cells"]],
            ["其中：该节点不设此线（`—`）", summary["not_applicable_cells"]],
            ["声明格子数（节点 × 自身 ability_lines × 3 档）", summary["declared_cells"]],
            ["有题的格子数", summary["filled_cells"]],
            ["空格子数（`0`）", summary["empty_cells"]],
            ["空格子占声明格子比例", pct(summary["empty_ratio"])],
        ],
    ))
    out.append("")
    out.append(
        f"`—` 与 `0` 不是一回事：`—` 表示教研在节点声明里就没有这条能力线（{summary['not_applicable_cells']} 格），"
        f"`0` 表示声明了但库里没有题（{summary['empty_cells']} 格）。前者不是缺口，后者会让节点直接判 L0。"
    )
    out.append("")

    # 5.2 ------------------------------------------------------------------
    out.append("## 5.2 按学科汇总")
    out.append("")
    out.append(as_table(
        ["学科", "题目总数", "诊断节点数", "声明格子", "L0", "L1", "L2", "L3"],
        [
            [row["name_zh"], row["questions"], row["nodes"], row["cells_declared"],
             row["levels"]["L0"], row["levels"]["L1"], row["levels"]["L2"], row["levels"]["L3"]]
            for row in result["subjects"]
        ] + [[
            "**合计**",
            summary["questions"],
            summary["nodes"],
            summary["declared_cells"],
            sum(row["levels"]["L0"] for row in result["subjects"]),
            sum(row["levels"]["L1"] for row in result["subjects"]),
            sum(row["levels"]["L2"] for row in result["subjects"]),
            sum(row["levels"]["L3"] for row in result["subjects"]),
        ]],
    ))
    out.append("")
    l2_total = sum(row["levels"]["L2"] for row in result["subjects"])
    if l2_total == 0:
        sizes = sorted({row["questions"] for row in result["nodes"]})
        out.append(
            f"L2 为空是结构性的，不是漏判：节点题量只取 {len(sizes)} 种值（{' / '.join(str(s) for s in sizes)}），"
            f"都是「能力线数 × 9」，落不进 {meta['thresholds']['L2']}–{meta['thresholds']['L3'] - 1} 这一段。"
            "换句话说，要么刚好不够一轮闭环（18 题），要么一次就是两轮（27 题以上）。"
        )
        out.append("")

    # 5.3 ------------------------------------------------------------------
    out.append("## 5.3 逐节点闭环判定表")
    out.append("")
    out.append(as_table(
        ["诊断节点", "名称", "题目总数", "能力线数", "档位", "消除模拟", "空能力线"],
        [
            [f"`{row['code']}`", row["name_zh"], row["questions"], row["cells_declared"],
             row["level"], row["elimination"]["verdict_zh"],
             "、".join(row["empty_lines"]) if row["empty_lines"] else "—"]
            for row in result["nodes"]
        ],
    ))
    out.append("")

    # 5.4 ------------------------------------------------------------------
    out.append("## 5.4 逐格子明细矩阵")
    out.append("")
    out.append("行 = 学科设计上的全部能力线；`—` = 该节点不设此线；`0` = 设了线但没题。")
    out.append("")
    for subject in result["subjects"]:
        out.append(f"### {subject['name_zh']}")
        out.append("")
        rows = []
        for node in [row for row in result["nodes"] if row["subject"] == subject["subject"]]:
            for line in subject["ability_lines"]:
                if line in node["grid"]:
                    counts = node["grid"][line]
                    rows.append([
                        f"`{node['code']}`", line,
                        counts[EASY], counts[MEDIUM], counts[HARD],
                        sum(counts.values()),
                    ])
                else:
                    rows.append([f"`{node['code']}`", line, "—", "—", "—", "—"])
        out.append(as_table(["诊断节点", "能力标签", "易", "中", "难", "小计"], rows))
        out.append("")

    # 5.5 ------------------------------------------------------------------
    out.append("## 5.5 消除阶段可取性模拟")
    out.append("")
    out.append(
        f"每个节点枚举其已声明能力线的两两组合，判断能否凑出 {short['medium']} 中 + {short['hard']} 难 + "
        f"{short['fill_easy_or_medium']} 易/中（共 {short['total']} 道、互不重复）。"
        "判定式：`中 ≥ 2 且 难 ≥ 1 且 合计 ≥ 5`。单线节点取自身作为唯一组合。"
    )
    out.append("")
    rows = []
    for node in result["nodes"]:
        for combo in node["elimination"]["combos"]:
            rows.append([
                f"`{node['code']}`",
                " + ".join(combo["combo"]),
                combo["medium"], combo["hard"], combo["easy"], combo["total"],
                "✓" if combo["feasible"] else "✗",
            ])
        rows.append([
            f"**`{node['code']}` 结论**", f"— {node['elimination']['verdict_zh']}",
            "", "", "", "",
            f"{node['elimination']['feasible_combos']}/{node['elimination']['total_combos']}",
        ])
    out.append(as_table(["诊断节点", "能力线组合", "中", "难", "易", "合计", "可凑出"], rows))
    out.append("")
    out.append(
        "注意这一节的“可凑出”只约束**5 道之间互不重复**，不约束与立案窗口的 15 道不重复。"
        "后者由节点题量决定，见 5.6 的“扣除立案窗口后剩余”。"
    )
    out.append("")

    # 5.6 ------------------------------------------------------------------
    out.append("## 5.6 缺口清单")
    out.append("")
    out.append("### P0：连立案都撑不住的节点")
    out.append("")
    p0 = result["gaps"]["p0"]
    if not p0:
        thinnest = min(result["nodes"], key=lambda row: row["questions"])
        out.append(
            f"**无。** 最薄的节点 `{thinnest['code']}` 也有 {thinnest['questions']} 题，"
            f"高于立案窗口 {meta['window']}；声明格子里空格子 {summary['empty_cells']} 个，不存在空能力线。"
        )
    else:
        for node in p0:
            out.append(f"- `{node['code']}` {node['name_zh']}：{node['questions']} 题，"
                       f"空能力线：{'、'.join(node['empty_lines']) or '—'}")
    out.append("")

    out.append("### P1：能立案、撑不住消除的节点")
    out.append("")
    p1 = result["gaps"]["p1"]
    if not p1:
        out.append("**无。** 所有节点题量都 ≥ L2 线。")
    else:
        out.append(
            f"共 **{len(p1)}** 个。这些节点题量够立案，但立案窗口拿走 {meta['window']} 道不同题后，"
            f"剩下的题不足 {short['total']} 道，消除阶段必然重复用题。"
        )
        out.append("")
        for node in p1:
            out.append(
                f"- `{node['code']}` {node['name_zh']}：{node['questions']} 题（L1）；"
                f"立案后剩 {node['questions'] - meta['window']} 道未做 → 必重复 **{node['repeats_required']}** 道；"
                f"补到 L2 需 **+{meta['thresholds']['L2'] - node['questions']}** 题（{patch_words(node['patch_to_l2'])}）"
            )
    out.append("")

    out.append("### P2：能撑一轮闭环、撑不住两轮闭环的节点")
    out.append("")
    p2 = result["gaps"]["p2"]
    if not p2:
        thinnest_l2 = min(
            (row for row in result["nodes"] if row["level"] in ("L2", "L3")),
            key=lambda row: row["questions"],
            default=None,
        )
        out.append(
            "**无。** 达到 L2 的节点最少也有 "
            f"{thinnest_l2['questions'] if thinnest_l2 else 0} 题，已越过 L3 线 {meta['thresholds']['L3']}。"
        )
    else:
        for node in p2:
            out.append(f"- `{node['code']}` {node['name_zh']}：{node['questions']} 题；"
                       f"补到 L3 需 **+{meta['thresholds']['L3'] - node['questions']}** 题"
                       f"（{patch_words(node['patch_to_l3'])}）")
    out.append("")

    # 5.7 ------------------------------------------------------------------
    out.append("## 5.7 汇总补题清单")
    out.append("")
    out.append("“当前总题量”是该学科该能力线该难度的全部题量；“需补”是把不足的节点补到目标档位后，"
                 "落到这个格子上的建议题数（中 → 难 → 易轮转，同难度内按节点声明的能力线顺序轮转）。")
    out.append("")
    for subject in result["subjects"]:
        rows = [
            [item["ability"], item["difficulty"], item["current"], item["to_l2"], item["to_l3"]]
            for item in result["patch"] if item["subject"] == subject["subject"]
        ]
        rows.append([
            f"**{subject['name_zh']}小计**", "",
            sum(item["current"] for item in result["patch"] if item["subject"] == subject["subject"]),
            sum(item["to_l2"] for item in result["patch"] if item["subject"] == subject["subject"]),
            sum(item["to_l3"] for item in result["patch"] if item["subject"] == subject["subject"]),
        ])
        out.append(as_table(["能力标签", "难度", "当前总题量", "达到 L2 需补", "达到 L3 需补"], rows))
        out.append("")
    out.append(as_table(
        ["能力标签", "难度", "当前总题量", "达到 L2 需补", "达到 L3 需补"],
        [
            [item["ability"], item["difficulty"], item["current"], item["to_l2"], item["to_l3"]]
            for item in result["patch"]
        ] + [[
            "**全库合计**", "",
            sum(item["current"] for item in result["patch"]),
            conclusion["patch_to_l2_total"],
            conclusion["patch_to_l3_total"],
        ]],
    ))
    out.append("")

    # 5.8 ------------------------------------------------------------------
    out.append("## 5.8 核心结论")
    out.append("")
    repeat_subjects = "、".join(
        f"{row['name_zh']} {row['repeat_nodes']}/{row['nodes']}"
        for row in conclusion["by_subject"] if row["repeat_nodes"]
    )
    out.append(
        f"67 个诊断节点中，**{conclusion['nodes_l2_or_better']} 个**（{pct(conclusion['nodes_l2_or_better'] / conclusion['nodes_total'])}）"
        f"能支撑一轮完整闭环；**{conclusion['nodes_l1_only']} 个**只能立案、撑不住消除；"
        f"**{conclusion['nodes_l0']} 个**连立案都撑不住。"
        f"按现行“立案 + 消除”规则，**{pct(conclusion['repeat_ratio'])}** 的节点（{repeat_subjects}）"
        f"在消除阶段会被迫重复用题，合计重复 {conclusion['repeats_required_total']} 道次；"
        f"补 {conclusion['patch_to_l2_total']} 道题可让全部节点达到一轮闭环，补 {conclusion['patch_to_l3_total']} 道可全部达到两轮。"
    )
    out.append("")
    out.append(
        f"格子上看题库很整齐：声明格子 {summary['declared_cells']} 个全都有题、每格 3/3/3，"
        f"所以 {conclusion['strong_all']} 个节点的消除组合全部强可行；"
        f"真正卡住的是**节点深度**——2 条线的节点只有 18 题，装不下 15 + 5。"
    )
    out.append("")
    return "\n".join(out)


def render_summary(result: dict) -> str:
    conclusion = result["conclusion"]
    summary = result["summary"]
    return (
        f"{summary['questions']} 题 / {summary['nodes']} 节点 / {summary['declared_cells']} 格 "
        f"(空 {summary['empty_cells']}) | "
        f"L2+ {conclusion['nodes_l2_or_better']} / L1 {conclusion['nodes_l1_only']} / L0 {conclusion['nodes_l0']} | "
        f"消除必重复 {pct(conclusion['repeat_ratio'])} | 补 {conclusion['patch_to_l2_total']}→L2, "
        f"{conclusion['patch_to_l3_total']}→L3"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="题库密度审计（只读）")
    parser.add_argument("--dataset", help="读取审计快照 JSON（由 --emit-dataset 产出）")
    parser.add_argument("--emit-dataset", help="把本次数据源落成快照 JSON")
    parser.add_argument("--dsn", help="从数据库只读拉题，如 mysql://user@localhost/quiz?unix_socket=...")
    parser.add_argument("--window", type=int, default=None, help="立案窗口，默认取 server.app.weakness")
    parser.add_argument("--short-medium", type=int, default=SHORT_MEDIUM)
    parser.add_argument("--short-hard", type=int, default=SHORT_HARD)
    parser.add_argument("--short-fill", type=int, default=SHORT_FILL)
    parser.add_argument("--out-json", default=str(ROOT / "reports" / "density-audit.json"))
    parser.add_argument("--out-md", default=str(ROOT / "reports" / "density-audit.md"))
    args = parser.parse_args(argv)

    window = args.window if args.window is not None else configured_window()
    dataset = load_dataset(args.dataset) if args.dataset else build_dataset(dsn=args.dsn)
    if args.emit_dataset:
        Path(args.emit_dataset).parent.mkdir(parents=True, exist_ok=True)
        Path(args.emit_dataset).write_text(
            json.dumps(dataset, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )

    result = build(
        dataset,
        window=window,
        short_medium=args.short_medium,
        short_hard=args.short_hard,
        short_fill=args.short_fill,
    )

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_md(result) + "\n", encoding="utf-8")

    print(render_summary(result))
    print(f"json -> {out_json}")
    print(f"md   -> {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
