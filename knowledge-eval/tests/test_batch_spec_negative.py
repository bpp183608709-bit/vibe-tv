"""Negative tests for the batch expander.

The expander writes to the bank, so every test runs against a copy in a scratch directory. What is
being asserted is not "the tool works" but "the tool refuses": a wrong calculation, an ability line the
node never declared, and a second application of the same batch.

    pytest knowledge-eval/tests/test_batch_spec_negative.py -v
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
SCRIPT = KE / "scripts" / "batch_spec.py"
DATA = KE / "data"
SUBJECTS = ("math", "chinese", "english")


@pytest.fixture
def sandbox():
    """A copy of the data directory, with the question banks emptied.

    Nodes come from the real files — the tests are about the expander's rules, not about fixtures. The
    questions are cleared because these tests must not depend on how full the real bank happens to be:
    when `d06` reached 9 questions per cell (稳健档), three of them started failing on the per-cell
    ceiling instead of the rule they were written to assert. A test whose meaning changes as data grows
    is a test that will one day be "fixed" by loosening the wrong thing.
    """
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        for subject in SUBJECTS:
            for kind in ("nodes", "questions"):
                shutil.copy(DATA / f"{kind}_{subject}_6up.json", target)
            path = target / f"questions_{subject}_6up.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["questions"] = []
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        yield target


def write_spec(sandbox: Path, lines: list[dict], name: str = "spec.jsonl") -> Path:
    path = sandbox / name
    path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
                    encoding="utf-8")
    return path


def run(sandbox: Path, spec: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--subject", "math", "--spec", str(spec),
         "--data-dir", str(sandbox), *extra],
        capture_output=True, text=True,
    )


def fill(**over) -> dict:
    """A valid maths fill, so a test can break exactly one field."""
    line = {"n": "d06", "a": "执行计算", "d": "易", "t": "填空题", "s": "9 的倒数是（  ）。",
            "k": "1/9", "e": "9 × 1/9 = 1，所以 9 的倒数是 1/9。", "c": "1 / 9"}
    line.update(over)
    return line


def test_a_valid_line_is_written(sandbox):
    before = json.loads((sandbox / "questions_math_6up.json").read_text(encoding="utf-8"))
    result = run(sandbox, write_spec(sandbox, [fill()]))
    assert result.returncode == 0, result.stderr

    after = json.loads((sandbox / "questions_math_6up.json").read_text(encoding="utf-8"))
    assert len(after["questions"]) == len(before["questions"]) + 1
    added = next(q for q in after["questions"] if q["stem"] == "9 的倒数是（  ）。")
    assert added["correct_answer"] == "1/9"
    assert added["accepted"] == ["1/9"]
    assert added["diagnostic_node_id"] == "math-6up-d06"


def test_a_wrong_calculation_is_rejected(sandbox):
    """`c` computes the answer, so a slip in either the expression or the answer shows up here."""
    result = run(sandbox, write_spec(sandbox, [fill(c="1 / 6")]))
    assert result.returncode != 0
    assert "the answer says '1/9'" in result.stderr


def test_a_fill_without_a_check_is_rejected(sandbox):
    line = fill()
    line.pop("c")
    result = run(sandbox, write_spec(sandbox, [line]))
    assert result.returncode != 0
    assert "needs `c`" in result.stderr


def test_an_ability_line_the_node_never_declared_is_rejected(sandbox):
    result = run(sandbox, write_spec(sandbox, [fill(a="综合表达")]))
    assert result.returncode != 0
    assert "is not declared on math-6up-d06" in result.stderr


def test_reapplying_the_same_batch_changes_nothing(sandbox):
    spec = write_spec(sandbox, [fill()])
    assert run(sandbox, spec).returncode == 0
    first = (sandbox / "questions_math_6up.json").read_bytes()

    again = run(sandbox, spec)
    assert again.returncode == 0, again.stderr
    assert "already applied" in again.stdout
    assert (sandbox / "questions_math_6up.json").read_bytes() == first


def test_one_bad_line_stops_the_whole_batch(sandbox):
    """Half a batch is harder to reason about than none: the good line must not land either."""
    before = (sandbox / "questions_math_6up.json").read_bytes()
    result = run(sandbox, write_spec(sandbox, [fill(s="12 的倒数是（  ）。", k="1/12", c="1 / 12"),
                                               fill(c="1 / 6")]))
    assert result.returncode != 0
    assert (sandbox / "questions_math_6up.json").read_bytes() == before


def test_all_answers_on_one_label_is_rejected(sandbox):
    """The first real batch came out 24 A's out of 24 choices: always picking A passed the quiz.

    Options are written in reading order, so the correct one drifts to the top and stays there. The
    fix is to rotate, and the gate is what makes anyone rotate.
    """
    # One question per cell: the per-cell ceiling would otherwise fire first and hide this check.
    cells = [("d06", "理解意义"), ("d06", "辨析变式"), ("d07", "理解意义"), ("d07", "辨析变式"),
             ("d08", "理解意义"), ("d08", "辨析变式")]
    lines = [{"n": node, "a": ability, "d": "易", "t": "选择题",
              "s": f"第 {index} 题：下面说法正确的是（  ）", "o": "甲|乙|丙|丁", "k": "A",
              "e": "甲对，其余三个都把算法说反了。"} for index, (node, ability) in enumerate(cells, 1)]
    result = run(sandbox, write_spec(sandbox, lines))
    assert result.returncode != 0
    assert "answer spread" in result.stderr


def run_subject(sandbox: Path, spec: Path, subject: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--subject", subject, "--spec", str(spec),
         "--data-dir", str(sandbox)],
        capture_output=True, text=True,
    )


def test_an_explanation_pointing_at_option_letters_is_rejected(sandbox):
    """语文解析里的“A 是‘想要’”会在选项重排后指到别的选项上，而文件里看不出任何异常。

    The 语文 bank was about to have every option rotated (61% of its answers sat on B), so this had
    to be refused at the moment the batch is written rather than found later by reading 36 questions.
    """
    before = (sandbox / "questions_chinese_6up.json").read_bytes()
    line = {"n": "d01", "a": "语言运用", "d": "易", "t": "选择题",
            "s": "《草原》中“翠色欲流”的“欲”应当理解为（  ）",
            "o": "将要|想要|欲望|需要", "k": "A",
            "e": "“欲”是“将要”；A 是“想要”，C 是“欲望”。"}
    result = run_subject(sandbox, write_spec(sandbox, [line]), "chinese")
    assert result.returncode != 0
    assert "points at options by letter" in result.stderr
    assert (sandbox / "questions_chinese_6up.json").read_bytes() == before
