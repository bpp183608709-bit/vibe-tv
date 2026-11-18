"""The schema must be applied to the file as written, with no shape-shifting in between.

The previous revision validated a *derived* object: `flat_nodes()` renamed `name_zh` to `name`,
built `parent_id` from the nesting, and turned `micro_nodes` from objects into strings before the
schema ever saw them. The schema therefore constrained the translator, not the data — which is how a
tree with a missing node passed every check for a month.

Three tests, in increasing strength:

  1. drop `node_type` from a diagnostic node -> validation fails;
  2. make `micro_nodes` a list of objects -> validation fails;
  3. the object handed to the validator is the object read from the file, and no function named
     `flat_nodes` exists in the scripts at all.

    pytest knowledge-eval/tests/test_schema_direct.py -v
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
MATH_NODES = KE / "data" / "nodes_math_6up.json"

sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def workdir():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def run_schema(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "check_schema.py"), "--nodes", str(path)],
        capture_output=True, text=True,
    )


def broken(workdir: Path, mutate) -> Path:
    payload = json.loads(MATH_NODES.read_text(encoding="utf-8"))
    mutate(payload)
    target = workdir / "nodes_math_6up.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def test_pristine_data_validates_directly():
    result = run_schema(MATH_NODES)
    assert result.returncode == 0, result.stderr
    assert "schema: ok" in result.stdout


def test_missing_node_type_fails(workdir):
    def mutate(payload):
        node = next(n for n in payload["nodes"] if n["node_type"] == "diagnostic")
        del node["node_type"]

    result = run_schema(broken(workdir, mutate))
    assert result.returncode != 0
    assert "node_type" in result.stderr


def test_micro_nodes_as_objects_fails(workdir):
    def mutate(payload):
        node = next(n for n in payload["nodes"] if n["node_type"] == "diagnostic")
        node["micro_nodes"] = [{"source_id": "mt_g6u_001", "name": "分数乘整数的意义"}]

    result = run_schema(broken(workdir, mutate))
    assert result.returncode != 0
    assert "micro_nodes" in result.stderr


def test_aggregate_with_knowledge_type_fails(workdir):
    """The old checker invented one per subject to satisfy a non-null column. Now it is a violation.

    Schema-level, `knowledge_type` is optional on every node; the rule "aggregates do not carry one"
    is a content rule, so it is enforced by check_content (the schema cannot express a conditional
    without a keyword the repo's validator does not implement).
    """
    def mutate(payload):
        node = next(n for n in payload["nodes"] if n["node_type"] == "aggregate")
        node["knowledge_type"] = "procedural"

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_content.py"), "--nodes", str(broken(workdir, mutate)),
         "--questions", str(KE / "data" / "questions_math_6up.json")],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert "knowledge_type" in result.stderr


def test_validator_receives_the_raw_objects(monkeypatch):
    import check_schema
    import keval

    seen: list = []
    real = keval.validate

    def spy(value, spec, path="$"):
        if path == "$":                      # top-level calls only; nested items recurse through here
            seen.append(value)
        return real(value, spec, path)

    monkeypatch.setattr(keval, "validate", spy)
    errors, count = check_schema.check_nodes([str(MATH_NODES)])
    raw = json.loads(MATH_NODES.read_text(encoding="utf-8"))["nodes"]

    assert not errors
    assert count == len(raw)
    assert seen[:count] == raw, "the validator saw something other than the file's own objects"


def test_no_translation_layer_exists():
    assert not hasattr(__import__("keval"), "flat_nodes")
    offenders = [path.name for path in SCRIPTS.glob("*.py") if "flat_nodes" in path.read_text(encoding="utf-8")]
    assert offenders == [], f"a field-renaming layer is back in {offenders}"
