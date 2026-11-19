"""The rotation moves options, not labels.

The failure this guards against is quiet: a tool that reassigns `k` from the position instead of
moving the option text leaves the explanation and the key disagreeing, and every gate still passes —
`k` is a valid label either way. So the assertion is on the text.

    pytest knowledge-eval/tests/test_rotate_options.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pytest

KE = Path(__file__).resolve().parents[1]
SCRIPT = KE / "scripts" / "rotate_options.py"
LABELS = "ABCDEFGH"


@pytest.fixture
def spec():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "spec.jsonl"
        lines = [
            {"n": "d01", "a": "执行计算", "d": "易", "t": "选择题", "s": f"1/2 + 1/{i} = ?",
             "o": "5/6|2/5|1/5|3/4", "k": "A", "e": "通分后相加。", "c": "True"}
            for i in range(3, 11)
        ]
        lines.append({"n": "d01", "a": "执行计算", "d": "中", "t": "填空题", "s": "1/2 + 1/4 =（  ）。",
                      "k": "3/4", "e": "通分。", "c": "1/2 + 1/4"})
        path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
                        encoding="utf-8")
        yield path


def run(spec: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(spec), *extra],
                          capture_output=True, text=True)


def test_the_correct_text_survives_the_move(spec):
    before = [json.loads(line) for line in spec.read_text(encoding="utf-8").splitlines() if line.strip()]
    correct_before = []
    for line in before:
        options = line.get("o")
        correct_before.append(options.split("|")[LABELS.index(line["k"])] if options else line["k"])

    assert run(spec).returncode == 0

    after = [json.loads(line) for line in spec.read_text(encoding="utf-8").splitlines() if line.strip()]
    correct_after = []
    for line in after:
        options = line.get("o")
        correct_after.append(options.split("|")[LABELS.index(line["k"])] if options else line["k"])
    assert correct_after == correct_before
    assert [line["s"] for line in after] == [line["s"] for line in before]


def test_choices_are_spread_not_all_on_one_label(spec):
    assert run(spec).returncode == 0
    after = [json.loads(line) for line in spec.read_text(encoding="utf-8").splitlines() if line.strip()]
    spread = Counter(line["k"] for line in after if line.get("o"))
    assert max(spread.values()) <= 4, spread
    assert set(spread) == {"A", "B", "C", "D"}, spread


def test_a_label_beyond_the_options_is_refused_without_writing(spec):
    lines = [json.loads(line) for line in spec.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines[0]["k"] = "D"          # four options, so D is legal; make it illegal instead
    lines[0]["o"] = "5/6|2/5"
    spec.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
                    encoding="utf-8")
    original = spec.read_bytes()

    result = run(spec)
    assert result.returncode == 1
    assert "not one of" in result.stderr
    assert spec.read_bytes() == original


def test_dry_run_reports_without_writing(spec):
    original = spec.read_bytes()
    result = run(spec, "--dry-run")
    assert result.returncode == 0
    assert "dry run" in result.stdout
    assert spec.read_bytes() == original
