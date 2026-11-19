"""规格测试：一个聚合节点下前三个诊断节点，每个 (诊断节点 × 能力标签) 格子能否构出四个阶段。

这不是回归测试。题库薄的格子会失败，**这是预期结果**：每一格都按当前题库的可达性打了
`xfail(reason=...)`，理由直指缺什么难度的题。哪天题库补上了，pytest 会报 XPASS，提醒把这行标记删掉。

目标聚合节点 = 诊断节点数最多的那个（并列取靠前的），本仓库当前是 `math-6up-u01` 分数乘法。
取它下面前三个诊断节点，四个能力线，四个阶段，共 48 个用例。

每个用例做四步（提示词 §3.2）：
  1. 用覆盖矩阵枚举出的那条可达路径构造作答行；
  2. `mastery.summarise` + `mastery.level_of` 判出的等级必须等于目标阶段；
  3. 0/1 级必须进报告的 `diagnostics`（薄弱清单）；
  4. 0/1/2 级必须能给出非空的强化包题集（强化包本身还没实现，这里断言它的输入）。

Run with:  python -m pytest knowledge-eval/tests/test_four_stage_constructible.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

KE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KE / "scripts"))

import check_coverage_matrix as ccm  # noqa: E402
import keval  # noqa: E402
from server.app import mastery, report  # noqa: E402

TARGET_NODES = 3

#: ASCII slugs for test ids: pytest escapes non-ASCII ids to \uXXXX, which makes the verification
#: output unreadable in exactly the place a reviewer is supposed to read it.
ABILITY_SLUG = {"理解意义": "meaning", "执行计算": "compute", "情境应用": "context",
                "辨析变式": "variation", "语言运用": "usage", "阅读理解": "reading",
                "辨析判断": "judgement", "综合表达": "expression"}


def load():
    nodes = [node for _, payload in keval.load_nodes() for node in payload["nodes"]]
    questions = [q for _, payload in keval.load_questions() for q in payload["questions"]]
    return ccm.analyse(nodes, questions)


DATA = load()
TARGET = ccm.pick_target(DATA["chapters"])
NODE_IDS = TARGET["diagnostic_node_ids"][:TARGET_NODES]
CELLS = {(c["diagnostic_node_id"], c["ability_tag"]): c for c in DATA["cells"] if c["diagnostic_node_id"] in NODE_IDS}


def build_rows(cell: dict, witness: dict) -> list[dict]:
    """Turn a witness (how many of each difficulty answered right) into window rows."""
    weights = {key: mastery.WEIGHT_X10[level] for key, _, level in ccm.DIFFICULTIES}
    rows: list[dict] = []
    for key, _, _ in ccm.DIFFICULTIES:
        right = witness["correct"][key]
        total = cell["questions"][key]
        rows += [{"is_correct": 1, "weight_x10": weights[key]}] * right
        rows += [{"is_correct": 0, "weight_x10": weights[key]}] * (total - right)
    return rows


def weak_report_contains(node_id: str, level: int, acc_x1000: int, sample: int) -> bool:
    """The same call the parent page makes, with one row in it."""
    groups = [{
        "student_id": "spec-child",
        "aggregate_id": TARGET["aggregate_node_id"],
        "aggregate_name": TARGET["aggregate_name"],
        "diagnostic_count": 1,
        "diagnostics": [{
            "node_id": node_id, "node_name": node_id, "level": level, "acc": acc_x1000,
            "sample": sample, "evidence": ["spec"], "abilities": [], "measured": sample >= mastery.MIN_SAMPLE,
        }],
    }]
    built = report.build(groups, "spec", scope="weak")
    return any(line["diagnostic_node_id"] == node_id
               for chapter in built["aggregate_summary"] for line in chapter["diagnostics"])


def cases() -> list:
    params = []
    for node_id in NODE_IDS:
        for ability in ccm.ABILITY_LINES["math"]:
            cell = CELLS[(node_id, ability)]
            for level in range(4):
                reason = (f"题库覆盖不足：{node_id}×{ability} 只有 "
                          f"{cell['questions']['easy']}/{cell['questions']['medium']}/"
                          f"{cell['questions']['hard']} 道 易/中/难，{level} 级不可达"
                          f"（{ccm.gap_reason(cell) or '样本不足'}）")
                marks = () if cell["stages"][str(level)] else pytest.mark.xfail(reason=reason, strict=False)
                params.append(pytest.param(
                    node_id, ability, level, marks=marks,
                    id=f"{node_id}-{ABILITY_SLUG.get(ability, ability)}-L{level}"))
    return params


@pytest.mark.parametrize("node_id,ability,level", cases())
def test_stage_is_constructible(node_id, ability, level):
    cell = CELLS[(node_id, ability)]
    witness = cell["stages"][str(level)]
    assert witness, f"{node_id}×{ability} 构不出 {level} 级"

    rows = build_rows(cell, witness)
    assert len(rows) >= mastery.MIN_SAMPLE, "少于三条样本只会读「待测」"
    summary = mastery.summarise(rows)
    got = mastery.level_of(summary["acc_x1000"], summary["hard_right"])
    assert got == level, (f"构造目标是 {level} 级，判出来是 {got} 级"
                          f"（acc={summary['acc_x1000']}, hard_right={summary['hard_right']}）")

    if level <= 1:
        assert mastery.is_weak(summary["sample_count"], got) is True
        assert weak_report_contains(node_id, got, summary["acc_x1000"], summary["sample_count"])
    if level <= 2:
        pack = [q for q in cell["question_ids"]]
        assert pack, f"{node_id}×{ability} 的强化包题集为空"


def test_constructor_is_sound_on_a_complete_cell():
    """The machinery itself works: a 1 易 1 中 1 难 cell builds all four stages.

    Without this, a file of 48 xfails could equally mean "the bank is thin" or "the test is broken".
    """
    cell = {"questions": {"easy": 1, "medium": 1, "hard": 1}}
    seen = {}
    for level in range(4):
        stages = ccm.reaches(1, 1, 1)
        witness = stages[level]
        rows = build_rows(cell, witness)
        summary = mastery.summarise(rows)
        got = mastery.level_of(summary["acc_x1000"], summary["hard_right"])
        assert got == level
        seen[level] = summary["acc_x1000"]
    assert seen[3] > 800 and seen[0] < 400
