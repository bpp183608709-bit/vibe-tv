"""The report is a weak list. The chapter view is a separate field, off by default.

    pytest knowledge-eval/tests/test_report_weak_only.py -v

The fixture holds four measured states on purpose: 未掌握 (three wrong), 熟练 (three right, none hard),
拓展 (hard questions right), and one node with a single answer that must not be judged at all. If the
default output ever prints 熟练 or 拓展, this test fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

KE = Path(__file__).resolve().parents[1]
SCRIPTS = KE / "scripts"
EVALUATE = SCRIPTS / "evaluate.py"
ASSERT = SCRIPTS / "assert_report_weak_only.py"
FIXTURE = KE / "tests" / "fixtures" / "sample_answers_mixed_levels.json"
NODES = KE / "data" / "nodes_math_6up.json"
QUESTIONS = KE / "data" / "questions_math_6up.json"


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def evaluate(workdir: Path, *extra: str) -> dict:
    out = workdir / "report.json"
    result = subprocess.run(
        [sys.executable, str(EVALUATE), "--nodes", str(NODES), "--questions", str(QUESTIONS),
         "--answers", str(FIXTURE), "--out", str(out), *extra],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def lines(report: dict) -> list[dict]:
    return [line for chapter in report["aggregate_summary"] for line in chapter["diagnostics"]]


def full_lines(report: dict) -> list[dict]:
    return [line for chapter in report["aggregate_summary"] for line in chapter.get("full_view", [])]


def test_default_lists_only_weak_nodes(workdir):
    report = evaluate(workdir)
    printed = lines(report)
    assert [line["diagnostic_node_id"] for line in printed] == ["math-6up-d01"]
    assert {line["mastery_level"] for line in printed} == {"0-未掌握"}
    assert report["scope"] == "weak"
    assert "full_view" not in report["aggregate_summary"][0]


def test_weak_count_equals_lines_printed(workdir):
    report = evaluate(workdir)
    for chapter in report["aggregate_summary"]:
        assert chapter["weak_diagnostic_count"] == len(chapter["diagnostics"])


def test_unmeasured_node_is_nowhere(workdir):
    report = evaluate(workdir, "--include-full-view")
    ids = {line["diagnostic_node_id"] for line in lines(report) + full_lines(report)}
    assert "math-6up-d15" not in ids, "a node with one answer must not be judged"


def test_full_view_carries_the_strong_nodes(workdir):
    report = evaluate(workdir, "--include-full-view")
    levels = {line["mastery_level"] for line in lines(report)}
    full = {line["mastery_level"] for line in full_lines(report)}
    assert levels == {"0-未掌握"}
    assert {"2-熟练", "3-拓展"} <= full
    assert "2-熟练" not in levels and "3-拓展" not in levels
    assert report["scope"] == "all"


def test_assert_script_accepts_the_default_and_rejects_a_doctored_report(workdir):
    report = evaluate(workdir)
    out = workdir / "report.json"
    ok = subprocess.run([sys.executable, str(ASSERT), str(out)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr

    # negative sample: move a 熟练 node into diagnostics and the assertion must fail
    doctored = json.loads(out.read_text(encoding="utf-8"))
    strong = next(line for line in full_lines(evaluate(workdir, "--include-full-view"))
                  if line["mastery_level"] == "2-熟练")
    doctored["aggregate_summary"][0]["diagnostics"].append(strong)
    doctored["aggregate_summary"][0]["weak_diagnostic_count"] += 1
    bad = workdir / "doctored.json"
    bad.write_text(json.dumps(doctored, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run([sys.executable, str(ASSERT), str(bad)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "2-熟练" in result.stderr
