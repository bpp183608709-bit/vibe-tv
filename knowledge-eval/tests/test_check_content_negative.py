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
SNAPSHOT = KE / "data" / "taxonomy" / "pep-math-6up-v2.json"


@pytest.fixture
def workdir():
    """A scratch directory. `tmp_path` is not usable here: the sandbox shim this repo runs under
    refuses to create pytest's own base directory, so the tests bring their own."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def run(nodes: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--nodes", str(nodes), "--questions", str(MATH_QUESTIONS),
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
    assert "38/38" in result.stdout


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
    # the small snapshot leaves 28 referenced ids "extra" — the check must notice, not hardcode 38
    assert result.returncode != 0
    assert "does not have" in result.stderr
