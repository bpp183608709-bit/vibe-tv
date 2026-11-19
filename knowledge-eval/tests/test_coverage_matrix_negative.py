"""Negative tests for the coverage matrix: prove the checker can say no.

A report that always reads "coverage is fine" is worthless. Each case here builds a bank that is
deliberately thin in one specific way and asserts the script names exactly that defect:

    1. a cell with only easy + medium questions  -> stage 3 must be reported unreachable
    2. a cell with two questions                 -> 待测, no stage at all, counted as P0
    3. every cell with one of each difficulty     -> floor ok everywhere, robust nowhere

Run with:  python -m pytest knowledge-eval/tests/test_coverage_matrix_negative.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

KE = Path(__file__).resolve().parent.parent
SCRIPTS = KE / "scripts"
ABILITIES = ("理解意义", "执行计算", "情境应用", "辨析变式")

sys.path.insert(0, str(SCRIPTS))
import check_coverage_matrix as ccm  # noqa: E402


@pytest.fixture
def workdir():
    """pytest's own tmp base is refused by the sandbox shim this repo runs under."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def node(node_id: str, node_type: str, name: str, parent: str | None) -> dict:
    return {
        "node_id": node_id, "name": name, "subject": "math", "grade": 6, "semester": "up",
        "node_type": node_type, "parent_id": parent, "chapter_name": "分数乘法",
        "micro_nodes": [name], "prerequisite_node_ids": [], "description": name,
        "status": "active", "chapter_order": 1,
        **({"knowledge_type": "procedural"} if node_type == "diagnostic" else {}),
    }


def question(index: int, node_id: str, ability: str, difficulty: str) -> dict:
    return {
        "question_id": f"math-6up-d01-q{index:02d}",
        "diagnostic_node_id": node_id,
        "subject": "math",
        "ability_tag": ability,
        "difficulty_preset": difficulty,
        "question_type": "选择题",
        "stem": "1/2 + 1/3 = ?",
        "options": ["5/6", "2/5", "1/5"],
        "correct_answer": "A",
    }


def bank(workdir: Path, cells: dict[tuple[str, str], list[str]]) -> tuple[Path, Path]:
    """`cells` maps (node_id, ability) -> list of difficulties, in display order."""
    nodes = {"schema_version": 3, "subject": "math",
             "nodes": [node("math-6up-u01", "aggregate", "分数乘法", None)]}
    node_ids = []
    for node_id in dict.fromkeys(key[0] for key in cells):
        node_ids.append(node_id)
        nodes["nodes"].append(node(node_id, "diagnostic", node_id, "math-6up-u01"))
    questions = []
    for (node_id, ability), difficulties in cells.items():
        for difficulty in difficulties:
            questions.append(question(len(questions) + 1, node_id, ability, difficulty))
    node_path = workdir / "nodes.json"
    question_path = workdir / "questions.json"
    node_path.write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
    question_path.write_text(json.dumps({"schema_version": 3, "subject": "math",
                                         "questions": questions}, ensure_ascii=False),
                             encoding="utf-8")
    return node_path, question_path


def run(nodes: Path, questions: Path, workdir: Path) -> dict:
    reports = workdir / "reports"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_coverage_matrix.py"),
         "--nodes", str(nodes), "--questions", str(questions),
         "--out-reports", str(reports), "--quiet"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads((reports / "coverage-matrix.json").read_text(encoding="utf-8"))


def cell_of(report: dict, node_id: str, ability: str) -> dict:
    for cell in report["cells"]:
        if cell["diagnostic_node_id"] == node_id and cell["ability_tag"] == ability:
            return cell
    raise AssertionError(f"no cell {node_id} x {ability}")


def test_missing_hard_question_makes_stage_3_unreachable(workdir):
    """Easy and medium only: 拓展 needs a hard question answered right, so it cannot be reached."""
    cells = {(f"math-6up-d0{n}", ability): [] for n in (1, 2, 3) for ability in ABILITIES}
    cells[("math-6up-d01", "执行计算")] = ["易", "易", "中", "中"]
    report = run(*bank(workdir, cells), workdir)

    cell = cell_of(report, "math-6up-d01", "执行计算")
    assert cell["measured"] is True
    assert cell["stages"]["3"] is None, cell["stages"]
    assert cell["four_stage"] is False
    assert cell["floor"]["ok"] is False
    assert cell["floor"]["need"]["hard"] == 1
    assert 3 in cell["missing_levels"]
    # and the report says so in words a human can act on
    text = (workdir / "reports" / "coverage-matrix.md").read_text(encoding="utf-8")
    assert "3 级不可达" in text
    assert "无难题" in text


def test_two_questions_never_produce_a_weak_item(workdir):
    """Below MIN_SAMPLE the node reads 待测, so the cell is a P0 gap however easy the questions are."""
    cells = {(f"math-6up-d0{n}", ability): [] for n in (1, 2, 3) for ability in ABILITIES}
    cells[("math-6up-d02", "情境应用")] = ["易", "中"]
    report = run(*bank(workdir, cells), workdir)

    cell = cell_of(report, "math-6up-d02", "情境应用")
    assert cell["questions"]["total"] == 2
    assert cell["measured"] is False
    assert cell["reachable_levels"] == []
    assert cell["floor"]["need"] == {"easy": 0, "medium": 0, "hard": 1}
    assert report["summary"]["all"]["p0"] == len(report["cells"])
    assert report["summary"]["all"]["measured"] == 0


def test_one_of_each_difficulty_meets_the_floor_but_not_robust(workdir):
    """The 3-question minimum: every cell can reach all four stages, none can rotate a pack."""
    cells = {(node_id, ability): ["易", "中", "难"]
             for node_id in ("math-6up-d01", "math-6up-d02") for ability in ABILITIES}
    report = run(*bank(workdir, cells), workdir)

    assert len(report["cells"]) == 8
    assert report["summary"]["all"]["floor_ok"] == 8
    assert report["summary"]["all"]["robust_ok"] == 0
    assert report["summary"]["all"]["p0"] == 0
    assert report["summary"]["all"]["p1"] == 0
    assert report["summary"]["all"]["p2"] == 8
    for cell in report["cells"]:
        assert cell["four_stage"] is True, cell["diagnostic_node_id"]
        assert cell["stages"]["3"]["hard_right"] >= 1
        assert cell["stages"]["3"]["rate_x1000"] > 800
        assert cell["stages"]["0"]["rate_x1000"] < 400
    assert report["floor_topup"]["total"] == 0
    # From one of each to robust. Read the target from the constant: this assertion was `8 * 3` when
    # 稳健档 meant 2/2/2, and stayed 3 after the target moved to 3/3/3 (D9=B) — a stale number here
    # would let the report under-count by half without any test going red.
    per_cell = sum(ccm.ROBUST.values()) - len(ccm.ROBUST)
    assert report["robust_topup"]["total"] == 8 * per_cell
