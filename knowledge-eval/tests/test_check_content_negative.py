"""Negative tests for the two-way content check.

Each test breaks one thing in a copy of the maths node file and asserts the checker *fails* and says
which id is wrong. "The current dataset passes" is not evidence that a check works; a check that has
never been seen to fail is a check nobody has run.

    pytest knowledge-eval/tests/test_check_content_negative.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

KE = Path(__file__).resolve().parents[1]
SCRIPT = KE / "scripts" / "check_content.py"
MATH_NODES = KE / "data" / "nodes_math_6up.json"
MATH_QUESTIONS = KE / "data" / "questions_math_6up.json"
SNAPSHOT = KE / "data" / "taxonomy" / "pep-math-6up-v3.json"


def _per_cell_ceiling() -> int:
    """Read the ceiling from the checker instead of hardcoding it.

    It has already moved once (6 → 9 when 稳健档 went from 2/2/2 to 3/3/3). A literal here would
    have made this test pass for the wrong reason or fail for the wrong reason; the constant is the
    single place the number is allowed to live.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_content_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MAX_PER_CELL


MAX_PER_CELL = _per_cell_ceiling()


@pytest.fixture
def workdir():
    """A scratch directory. `tmp_path` is not usable here: the sandbox shim this repo runs under
    refuses to create pytest's own base directory, so the tests bring their own."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def run(nodes: Path, questions: Path = MATH_QUESTIONS) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--nodes", str(nodes), "--questions", str(questions),
         "--snapshot", str(SNAPSHOT)],
        capture_output=True, text=True,
    )


def load_math() -> dict:
    return json.loads(MATH_NODES.read_text(encoding="utf-8"))


def write(tmp_path: Path, payload: dict) -> Path:
    target = tmp_path / "nodes_math_6up.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def review_node(payload: dict) -> dict:
    return next(node for node in payload["nodes"] if node["node_id"] == "math-6up-d25")


def test_pristine_data_passes():
    result = run(MATH_NODES)
    assert result.returncode == 0, result.stderr
    # Read the expected size from the snapshot: hardcoding 38 is how this test would keep passing
    # after the taxonomy legitimately changes size (2024 版移出比/统计图/数与形后是 30).
    total = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["topic_count"]
    assert f"{total}/{total}" in result.stdout


def test_missing_source_id_fails(workdir):
    """Drop the micro point the old merge lost; the reverse check must name it."""
    payload = load_math()
    node = review_node(payload)
    node["source_ids"] = [source for source in node["source_ids"] if source != "mt_g6u_036"]
    node["micro_nodes"] = [name for name in node["micro_nodes"] if name != "分数四则混合运算"]

    result = run(write(workdir, payload))
    assert result.returncode != 0
    assert "mt_g6u_036" in result.stderr
    assert "missing" in result.stderr


def test_extra_source_id_fails(workdir):
    """Cite a micro point the snapshot does not have; the check must name it as extra."""
    payload = load_math()
    node = review_node(payload)
    node["source_ids"].append("mt_g6u_999")
    node["micro_nodes"].append("一个快照里不存在的微观点")

    result = run(write(workdir, payload))
    assert result.returncode != 0
    assert "mt_g6u_999" in result.stderr


def test_coverage_counts_come_from_the_files(workdir):
    """The comparison must not depend on either side being 38: shrink the snapshot and it still holds."""
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    snapshot["topics"] = snapshot["topics"][:10]
    snapshot_path = workdir / "small_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--nodes", str(MATH_NODES), "--questions", str(MATH_QUESTIONS),
         "--snapshot", str(snapshot_path), "--quiet"],
        capture_output=True, text=True,
    )
    # the small snapshot leaves every referenced id "extra" — the check must notice, not hardcode a size
    assert result.returncode != 0
    assert "does not have" in result.stderr


def test_too_many_questions_on_one_node_fails(workdir):
    """The ceiling is per cell, not per subject and not per node.

    Three ceilings have been tried and the first two were wrong for the same reason — they were chosen
    before anyone knew how many cells a node has. `MAX_PER_SUBJECT = 18` only ever held because ten
    nodes had no questions; a flat `MAX_PER_NODE = 6` forbids the 稳健档 of a node with four ability
    lines (24 questions). Nine now means nine in one (node × line) cell, and the node ceiling is
    `ability_lines × 9`.
    """
    payload = json.loads(MATH_QUESTIONS.read_text(encoding="utf-8"))
    base = [q for q in payload["questions"] if q["diagnostic_node_id"] == "math-6up-d01"]
    assert base, "the maths bank is expected to carry questions on d01"
    hottest = base[0]
    same_cell = [q for q in base if q["ability_tag"] == hottest["ability_tag"]]
    # One more than 稳健档 in a single cell, whatever that cell already holds — the pilot batch filled
    # some cells to three, so a fixed number of clones would either overshoot or not trip the ceiling.
    extra = (MAX_PER_CELL - len(same_cell)) + 1
    for repeat in range(extra):
        clone = dict(hottest)
        clone["question_id"] = f"math-6up-d01-q{90 + repeat:02d}"
        payload["questions"].append(clone)
    questions = workdir / "questions_math_6up.json"
    questions.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run(MATH_NODES, questions)
    assert result.returncode != 0
    # The ceiling is per cell, not per node: one over 稳健档 on a single line means the cell is full
    # while the other lines of the same node may still have none.
    assert f"math-6up-d01/{hottest['ability_tag']}: {MAX_PER_CELL + 1} questions" in result.stderr

def test_banned_word_in_a_node_name_fails(workdir):
    """A diagnostic name is rendered on the television as `review[].topic`, so it must stay neutral.

    This is not hypothetical: renaming the review node to 整理复习… made acceptance.py fail on the wire.
    """
    payload = load_math()
    node = next(n for n in payload["nodes"] if n["node_id"] == "math-6up-d25")
    node["name"] = "整理复习：分数四则混合运算"

    result = run(write(workdir, payload))
    assert result.returncode != 0
    assert "复习" in result.stderr


def test_repeated_stem_fails(workdir):
    """Two questions the child cannot tell apart.

    Found in the pilot batch: five maths questions shared two generic stems (下面说法正确的是 / 下面
    计算正确的是). Nothing caught them — the options differed, so no field was wrong. A batch written
    in one pass drifts to stems like these, which is exactly when a gate is worth having.
    """
    payload = json.loads(MATH_QUESTIONS.read_text(encoding="utf-8"))
    donor = next(q for q in payload["questions"] if q["diagnostic_node_id"] == "math-6up-d01")
    clone = dict(donor)
    clone["question_id"] = "math-6up-d01-q90"
    clone["stem"] = donor["stem"]
    payload["questions"].append(clone)
    questions = workdir / "questions_math_6up.json"
    questions.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run(MATH_NODES, questions)
    assert result.returncode != 0
    assert "repeats the stem of" in result.stderr
    assert "math-6up-d01-q90" in result.stderr


CHINESE_NODES = KE / "data" / "nodes_chinese_6up.json"
CHINESE_QUESTIONS = KE / "data" / "questions_chinese_6up.json"
ENGLISH_NODES = KE / "data" / "nodes_english_6up.json"
ENGLISH_QUESTIONS = KE / "data" / "questions_english_6up.json"


def run_all(subject_questions: dict[str, Path] | None = None) -> subprocess.CompletedProcess:
    """All three subjects at once: the coverage rule wants every subject present, so a one-subject
    run fails for a reason that is not the one under test."""
    nodes = [MATH_NODES, CHINESE_NODES, ENGLISH_NODES]
    questions = {"math": MATH_QUESTIONS, "chinese": CHINESE_QUESTIONS, "english": ENGLISH_QUESTIONS}
    questions.update(subject_questions or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--nodes", *map(str, nodes),
         "--questions", *(str(questions[s]) for s in ("math", "chinese", "english")),
         "--snapshot", str(SNAPSHOT), "--quiet"],
        capture_output=True, text=True,
    )


def test_explanation_pointing_at_option_letters_fails(workdir):
    """“A 是‘想要’，C 是‘欲望’” is a reason that goes stale the moment the options rotate.

    61% of the 语文 answers sat on one label, so every option in that bank was about to move, and
    seven explanations named options by letter. Rotate first and check later and the bank ships
    seven wrong explanations that look perfectly normal in the file.
    """
    payload = json.loads(CHINESE_QUESTIONS.read_text(encoding="utf-8"))
    payload["questions"][0]["explanation"] = "“欲”是“将要”；A 是“想要”，C 是“欲望”。"
    questions = workdir / "questions_chinese_6up.json"
    questions.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run_all({"chinese": questions})
    assert result.returncode != 0
    assert "points at options by letter" in result.stderr
    assert payload["questions"][0]["question_id"] in result.stderr


def test_letter_about_the_letter_itself_passes(workdir):
    """“字母 A” names the letter, not an option. A rule that cannot tell these apart would be
    switched off the first time it got in the way, so the carve-out is tested, not just coded."""
    payload = json.loads(CHINESE_QUESTIONS.read_text(encoding="utf-8"))
    payload["questions"][0]["explanation"] = "这类题在问拼音，句子里提到 字母 A 只是指字母本身。"
    questions = workdir / "questions_chinese_6up.json"
    questions.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = run_all({"chinese": questions})
    assert result.returncode == 0, result.stderr
