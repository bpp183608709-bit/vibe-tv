"""负样本：证明密度审计脚本会判负，而不是把任何题库都报成达标。

四条用例对应任务书第六节：题量不足、空能力线、中题不够、刚好够两轮。每条都用合成数据集走 CLI，
断言 JSON 输出——跑的是脚本本身，不是脚本里的某个函数，否则"脚本会不会判负"就没有被证明。

真实题库现在很整齐（213 格全 3/3/3、零空格子），所以这些负样本不可能从真实数据里长出来；
它们只能合成。这正是要留四条的原因。

    pytest knowledge-eval/tests/test_density_audit.py -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

KE = Path(__file__).resolve().parents[1]
ROOT = KE.parent
SCRIPT = KE / "scripts" / "audit_question_density.py"

MATH_LINES = ["理解意义", "执行计算", "情境应用", "辨析变式"]
CHINESE_LINES = ["语言运用", "阅读理解", "辨析判断", "综合表达"]
WINDOW = 15


@pytest.fixture
def tmp_path():
    """Scratch directory, hand-made: the pytest broker refuses `mkdir(exist_ok=True)` here."""
    directory = Path(tempfile.mkdtemp(prefix="density-audit-"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def dataset(nodes: list[dict], questions: list[dict]) -> dict:
    """A dataset shaped exactly like `--emit-dataset` writes, so the CLI path is the real path."""
    return {
        "schema_version": 1,
        "shape": "density-audit-dataset",
        "source": "synthetic",
        "subjects": {
            "math": {"name_zh": "数学", "ability_lines": MATH_LINES},
            "chinese": {"name_zh": "语文", "ability_lines": CHINESE_LINES},
        },
        "nodes": nodes,
        "questions": questions,
    }


def node(code: str, lines: list[str], *, subject: str = "math", order: int = 1) -> dict:
    return {
        "code": code,
        "name_zh": code,
        "subject": subject,
        "subject_name_zh": "数学" if subject == "math" else "语文",
        "unit_code": f"{code}-u",
        "unit_name_zh": "单元",
        "chapter_order": order,
        "ability_lines": lines,
    }


def questions(code: str, plan: dict[tuple[str, int], int]) -> list[dict]:
    """(ability, difficulty) -> how many questions. Ids are stable so two runs write the same bytes."""
    out: list[dict] = []
    index = 0
    for (ability, difficulty), count in plan.items():
        for _ in range(count):
            index += 1
            out.append({
                "code": f"{code}-q{index:02d}",
                "node": code,
                "ability_tag": ability,
                "difficulty": difficulty,
                "qtype": "选择题",
                "enabled": True,
            })
    return out


def audit(tmp_path: Path, data: dict) -> dict:
    """Run the script end to end and hand back its JSON."""
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(source),
         "--out-json", str(out_json), "--out-md", str(out_md), "--window", str(WINDOW)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert out_md.exists() and out_md.read_text(encoding="utf-8").startswith("# 题库密度审计报告")
    return json.loads(out_json.read_text(encoding="utf-8"))


def find(result: dict, code: str) -> dict:
    for row in result["nodes"]:
        if row["code"] == code:
            return row
    raise AssertionError(f"{code} missing from report")


def test_thin_node_is_l0(tmp_path):
    """10 题：连立案窗口（15）都装不满，判 L0，且缺口清单要把它列出来。"""
    code = "math-6up-d99"
    plan = {("理解意义", 1): 3, ("理解意义", 2): 3, ("执行计算", 3): 3, ("情境应用", 2): 1}
    result = audit(tmp_path, dataset([node(code, MATH_LINES)],
                                     questions(code, plan)))
    row = find(result, code)
    assert row["questions"] == 10
    assert row["level"] == "L0"
    assert [item["code"] for item in result["gaps"]["p0"]] == [code]
    assert row["residual"]["repeats_required"] == 5 - max(0, 10 - WINDOW)


def test_empty_declared_line_is_l0(tmp_path):
    """18 题够立案，但声明里的第三条线一道题都没有——空能力线优先于题量。"""
    code = "math-6up-d98"
    lines = ["理解意义", "执行计算", "情境应用"]
    plan = {("理解意义", 1): 3, ("理解意义", 2): 3, ("理解意义", 3): 3,
            ("执行计算", 1): 3, ("执行计算", 2): 3, ("执行计算", 3): 3}
    result = audit(tmp_path, dataset([node(code, lines)], questions(code, plan)))
    row = find(result, code)
    assert row["questions"] == 18                      # 题量越过 L1 线
    assert row["empty_lines"] == ["情境应用"]           # 但这条线是 0
    assert row["level"] == "L0"                        # 所以整节点仍是 L0
    assert row["grid"]["情境应用"] == {"1": 0, "2": 0, "3": 0}
    # 空能力线卡的是"主因线识别"（状态机按线命名主因），不是取题：剩余两条线自己就有 3/3/3，
    # 任何组合都凑得出 5 道。所以这里必须是 strong —— 若哪天真判成不可行，说明取题逻辑变了。
    assert row["elimination"]["verdict"] == "strong"


def test_twenty_two_questions_with_one_medium_is_infeasible(tmp_path):
    """22 题够一轮闭环的题量，但只有 1 道中题：任何两线组合都凑不出 2 中，消除判不可行。"""
    code = "math-6up-d97"
    plan = {
        ("理解意义", 1): 6, ("理解意义", 2): 0,
        ("执行计算", 3): 6,
        ("情境应用", 1): 5,
        ("辨析变式", 3): 4,
        ("辨析变式", 2): 1,      # 全场唯一的一道中题
    }
    result = audit(tmp_path, dataset([node(code, MATH_LINES)], questions(code, plan)))
    row = find(result, code)
    assert row["questions"] == 22
    assert row["level"] == "L2"                        # 题量档位确实到了 20–24
    assert row["elimination"]["verdict"] == "infeasible"
    assert row["elimination"]["feasible_combos"] == 0
    assert all(combo["medium"] <= 1 for combo in row["elimination"]["combos"])


def test_twenty_five_questions_evenly_spread_is_l3_and_strong(tmp_path):
    """25 题、每格约 2 道：判 L3，且任意两线组合都强可行。"""
    code = "math-6up-d96"
    plan = {(line, difficulty): 2 for line in MATH_LINES for difficulty in (1, 2, 3)}
    plan[("理解意义", 2)] = 3                          # 12 格 × 2 = 24，再补 1 道到 25
    result = audit(tmp_path, dataset([node(code, MATH_LINES)], questions(code, plan)))
    row = find(result, code)
    assert row["questions"] == 25
    assert row["level"] == "L3"
    assert row["elimination"]["verdict"] == "strong"
    assert row["elimination"]["feasible_combos"] == row["elimination"]["total_combos"] == 6
    assert row["residual"]["repeats_required"] == 0


def test_same_data_same_bytes(tmp_path):
    """同一份数据跑两次，JSON 与 Markdown 必须逐字节相同——报告要能被 diff。"""
    code = "math-6up-d95"
    plan = {(line, difficulty): 3 for line in MATH_LINES for difficulty in (1, 2, 3)}
    data = dataset([node(code, MATH_LINES)], questions(code, plan))
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    audit(first, data)
    audit(second, data)
    assert (first / "out.json").read_bytes() == (second / "out.json").read_bytes()
    assert (first / "out.md").read_bytes() == (second / "out.md").read_bytes()


def test_real_bank_parses(tmp_path):
    """真实题库也能跑通：不主张任何具体数字（补题后会变），只断言结构完整。"""
    out_json = tmp_path / "real.json"
    out_md = tmp_path / "real.md"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-json", str(out_json), "--out-md", str(out_md),
         "--window", str(WINDOW)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out_json.read_text(encoding="utf-8"))
    assert result["summary"]["questions"] > 0
    assert result["summary"]["nodes"] == len(result["nodes"])
    assert all(row["level"] in ("L0", "L1", "L2", "L3") for row in result["nodes"])
    assert result["summary"]["declared_cells"] == sum(row["cells_declared"] for row in result["nodes"])
