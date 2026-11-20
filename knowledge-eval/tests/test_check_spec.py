"""Negative tests for the spec checker.

`check_spec.py` reads and never writes, so these run against the real node file: the point is the rule,
not a fixture. Each test breaks exactly one field of an otherwise valid 9-line spec (one question per
cell, `--per-cell 1`), because a rule that fires on two problems at once cannot be told from a rule that
fires on the wrong one.

    pytest knowledge-eval/tests/test_check_spec.py -v
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
SCRIPT = KE / "scripts" / "check_spec.py"

@pytest.fixture
def tmp_path():
    """A scratch directory under the system temp root.

    pytest's own `tmp_path` goes through a broker that refuses `mkdir(exist_ok=True)` on an existing
    path here; `mkdtemp` creates the directory in one call and never asks again.
    """
    directory = Path(tempfile.mkdtemp(prefix="check-spec-"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


LINES = [("语言运用", "易"), ("语言运用", "中"), ("语言运用", "难"),
         ("辨析判断", "易"), ("辨析判断", "中"), ("辨析判断", "难"),
         ("阅读理解", "易"), ("阅读理解", "中"), ("阅读理解", "难")]


def line(ability: str = "语言运用", difficulty: str = "易", **over) -> dict:
    item = {"n": "d01", "a": ability, "d": difficulty, "t": "选择题",
            "s": f"{ability}（{difficulty}）：I ___ my homework yesterday.（选择正确形式）",
            "o": "did|do|does|doing", "k": "A",
            "e": "yesterday 指过去，所以用过去式 did。"}
    item.update(over)
    return item


def full(**over) -> list[dict]:
    """One question per cell, so `--per-cell 1` is satisfied unless a test removes a line."""
    items = [line(ability, difficulty) for ability, difficulty in LINES]
    if over:
        items[0] = line(**over)
    return items


def run(tmp_path: Path, lines: list[dict], *extra: str) -> subprocess.CompletedProcess:
    spec = tmp_path / "spec.jsonl"
    spec.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n",
                    encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(spec), "--subject", "english",
                           "--per-cell", "1", *extra], capture_output=True, text=True)


def test_a_valid_spec_passes(tmp_path):
    result = run(tmp_path, full())
    assert result.returncode == 0, result.stdout
    assert "spec: ok" in result.stdout


def test_a_stem_over_the_limit_is_rejected(tmp_path):
    result = run(tmp_path, full(s="I ___ my homework yesterday. " + "很长" * 60))
    assert result.returncode != 0
    assert "over 120" in result.stdout


def test_asking_the_child_to_read_what_it_does_not_quote_is_rejected(tmp_path):
    """B1: 读句子 with nothing in quotes is a question about material the screen does not show."""
    result = run(tmp_path, full(s="读句子，判断下面哪一句的时态用对了（  ）"))
    assert result.returncode != 0
    assert "does not quote" in result.stdout


def test_a_quotation_over_ninety_characters_is_rejected(tmp_path):
    long_quote = "Last summer I went to Beijing with my parents and we visited the Great Wall " * 2
    result = run(tmp_path, full(s=f"读句子：“{long_quote}” 这句话说的是（  ）"))
    assert result.returncode != 0
    assert "quotation is" in result.stdout


def test_a_duplicated_stem_is_rejected(tmp_path):
    items = full()
    items[1] = line("语言运用", "中", s=items[0]["s"])
    result = run(tmp_path, items)
    assert result.returncode != 0
    assert "already appears on line" in result.stdout


def test_an_answer_not_written_first_is_rejected(tmp_path):
    """The spec is the only order a person can check content in; rotation comes later."""
    result = run(tmp_path, full(k="B"))
    assert result.returncode != 0
    assert "k must be A" in result.stdout


def test_a_rotated_spec_is_accepted(tmp_path):
    result = run(tmp_path, full(k="B"), "--rotated", "--no-bank")
    assert result.returncode == 0, result.stdout


def test_a_cell_short_of_one_difficulty_is_rejected(tmp_path):
    items = full()[:-1]
    result = run(tmp_path, items)
    assert result.returncode != 0
    assert "in the line, want 3" in result.stdout


def test_a_bare_letter_in_the_explanation_is_rejected(tmp_path):
    result = run(tmp_path, full(e="B 是过去式，其余不是。"))
    assert result.returncode != 0
    assert "by letter" in result.stdout


def test_a_unique_longest_correct_option_is_reported(tmp_path):
    """B11 is a warning, not a failure: English word-form items cannot meet the length band at all."""
    items = [line(ability, difficulty, o="did not do it|did|do|does")
             for ability, difficulty in LINES]
    result = run(tmp_path, items)
    assert result.returncode == 0, result.stdout
    assert "unique longest" in result.stdout


def test_a_reference_to_the_textbook_is_rejected(tmp_path):
    result = run(tmp_path, full(s="根据课文，下面哪一句是对的（  ）"))
    assert result.returncode != 0
    assert "no textbook" in result.stdout
