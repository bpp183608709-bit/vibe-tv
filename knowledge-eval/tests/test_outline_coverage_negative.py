"""Negative tests for the textbook outline check.

Each test breaks one thing in a copy of the data and asserts the checker *fails* and names the item.
A checker nobody has seen fail is a checker nobody has run — and the whole point of this one is that
the previous suite (schema, content, parity, 256 acceptance cases) passed while the data described a
different edition of the textbook.

    pytest knowledge-eval/tests/test_outline_coverage_negative.py -v
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
DATA = KE / "data"
SCRIPT = KE / "scripts" / "check_outline_coverage.py"


@pytest.fixture
def workdir():
    """A scratch dir holding one subject's three files. `tmp_path` cannot be used: the sandbox shim
    this repo runs under refuses to create pytest's own base directory."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def stage(workdir: Path, subject: str) -> Path:
    for prefix in ("outline", "nodes", "questions"):
        source = DATA / f"{prefix}_{subject}_6up.json"
        shutil.copy(source, workdir / source.name)
    return workdir


def read(workdir: Path, prefix: str, subject: str) -> dict:
    return json.loads((workdir / f"{prefix}_{subject}_6up.json").read_text(encoding="utf-8"))


def write(workdir: Path, prefix: str, subject: str, payload: dict) -> None:
    (workdir / f"{prefix}_{subject}_6up.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def run(workdir: Path, subject: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--data-dir", str(workdir), "--subject", subject, *extra],
        capture_output=True, text=True,
    )


def node(payload: dict, node_id: str) -> dict:
    return next(item for item in payload["nodes"] if item["node_id"] == node_id)


def test_pristine_data_passes():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "42/42" in result.stdout, result.stdout


def test_a_text_no_node_mentions_is_a_gap_but_not_a_failure(workdir):
    """课本有、节点没提 is work not done — reported, not fatal. --strict is what makes it fatal."""
    stage(workdir, "chinese")
    payload = read(workdir, "nodes", "chinese")
    node(payload, "chinese-6up-d01")["micro_nodes"].remove("草原")
    write(workdir, "nodes", "chinese", payload)

    loose = run(workdir, "chinese")
    assert loose.returncode == 0
    assert "草原" in loose.stdout and "没有节点提到" in loose.stdout

    strict = run(workdir, "chinese", "--strict")
    assert strict.returncode != 0
    assert "gap" in strict.stderr


def test_a_title_outside_the_book_is_mispointed(workdir):
    """节点提了、课本没有: the failure mode behind the three dead 伯牙鼓琴 questions."""
    stage(workdir, "chinese")
    payload = read(workdir, "nodes", "chinese")
    node(payload, "chinese-6up-d11")["micro_nodes"].append("《桂树》")
    write(workdir, "nodes", "chinese", payload)

    result = run(workdir, "chinese")
    assert result.returncode != 0
    assert "《桂树》" in result.stderr


def test_a_retired_text_name_cannot_come_back_bare(workdir):
    """伯牙鼓琴 used to sit in micro_nodes without 《》 — the bracketed check alone would miss it."""
    stage(workdir, "chinese")
    payload = read(workdir, "nodes", "chinese")
    node(payload, "chinese-6up-d11")["micro_nodes"] = ["伯牙鼓琴", "书戴嵩画牛"]
    write(workdir, "nodes", "chinese", payload)

    result = run(workdir, "chinese")
    assert result.returncode != 0
    assert "left this volume" in result.stderr
    assert "伯牙鼓琴" in result.stderr


def test_a_question_may_not_quote_a_text_the_book_does_not_have(workdir):
    """The weak check asked for: whatever a stem quotes has to be printed in the book."""
    stage(workdir, "chinese")
    payload = read(workdir, "questions", "chinese")
    payload["questions"][0]["stem"] = "《花之歌》中“我”是（  ）"
    write(workdir, "questions", "chinese", payload)

    result = run(workdir, "chinese")
    assert result.returncode != 0
    assert "《花之歌》" in result.stderr


def test_a_chapter_name_that_differs_from_the_book_fails(workdir):
    """数学/英语的单元名是印在课本上的，聚合节点名必须逐字一致——这是「艺术之美 vs 科学与想象」那类错。"""
    stage(workdir, "math")
    payload = read(workdir, "outline", "math")
    payload["units"][0]["name"] = "位置与方向（二）"
    write(workdir, "outline", "math", payload)

    result = run(workdir, "math")
    assert result.returncode != 0
    assert "位置与方向" in result.stderr


def test_not_assessable_without_a_reason_fails(workdir):
    stage(workdir, "chinese")
    payload = read(workdir, "outline", "chinese")
    entry = next(item for item in payload["units"][0]["entries"] if item["name"].startswith("习作："))
    entry.pop("reason")
    write(workdir, "outline", "chinese", payload)

    result = run(workdir, "chinese")
    assert result.returncode != 0
    assert "gives no reason" in result.stderr
