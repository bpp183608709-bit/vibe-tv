"""Guards for `scripts/patch_options.py`.

441 option rewrites cannot be reviewed one by one at the end, so the patch step has to refuse
the mistakes that would quietly corrupt the answer key. Each test below is one of those
mistakes; the last two are the happy path (a distractor lengthened, nothing else touched).
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "patch_options.py"

QUESTION = {
    "question_id": "chinese-6up-d01-q01",
    "diagnostic_node_id": "chinese-6up-d01",
    "subject": "chinese",
    "ability_tag": "语言运用",
    "cognitive_level": "记忆理解",
    "difficulty_preset": "易",
    "question_type": "选择题",
    "stem": "《草原》中“翠色欲流”的“欲”意思是（  ）",
    "options": ["将要", "想要", "欲望", "需要"],
    "correct_answer": "A",
    "explanation": "“欲”在这里是“将要”的意思，“翠色欲流”形容绿色浓得将要流动起来。",
    "tags": ["草原", "语境词义"],
}


@pytest.fixture()
def workspace():
    directory = Path(tempfile.mkdtemp(prefix="patch-options-"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def run(workspace, patches, question=None):
    """Run the script against a scratch bank holding one question."""
    bank = workspace / "questions_chinese_6up.json"
    payload = {"schema_version": 3, "subject": "chinese",
               "questions": [question or QUESTION]}
    bank.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    spec = workspace / "patch.jsonl"
    spec.write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in patches) + "\n", encoding="utf-8")

    # Point the script at the scratch directory by shadowing the DATA path it computes.
    code = f"""
import json, sys
sys.path.insert(0, {str(ROOT / 'scripts')!r})
import patch_options
patch_options.DATA = __import__('pathlib').Path({str(workspace)!r})
patch_options.BANKS = {{"chinese": patch_options.DATA / "questions_chinese_6up.json"}}
raise SystemExit(patch_options.main(sys.argv[1:]))
"""
    return subprocess.run(
        [sys.executable, "-c", code, "--subject", "chinese", "--patch", str(spec), "--dry-run"],
        capture_output=True, text=True)


def bank(workspace):
    return json.loads((workspace / "questions_chinese_6up.json").read_text(encoding="utf-8"))


def test_lengthening_a_distractor_is_allowed(workspace):
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["将要", "想要得到某样东西", "欲望", "需要"]}])
    assert result.returncode == 0, result.stderr
    assert "1 applied" in result.stderr


def test_rewriting_the_correct_option_is_refused(workspace):
    """Rewriting the key is how an explanation ends up arguing for a different answer."""
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["快要", "想要", "欲望", "需要"]}])
    assert result.returncode == 1
    assert "the correct option (A) was rewritten" in result.stderr


def test_changing_the_option_count_is_refused(workspace):
    """A 4-option item that loses an option changes what `correct_answer` points at."""
    result = run(workspace, [{"q": "chinese-6up-d01-q01", "o": ["将要", "想要", "欲望"]}])
    assert result.returncode == 1
    assert "became 3" in result.stderr


def test_duplicate_options_are_refused(workspace):
    """A distractor rewritten into another option gives the item two right answers."""
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["将要", "想要", "将要", "需要"]}])
    assert result.returncode == 1
    assert "duplicate options" in result.stderr


def test_stem_quoting_a_changed_option_is_refused(workspace):
    """Disambiguation items quote their options in the stem; patching those breaks the stem."""
    quoted = dict(QUESTION)
    quoted["stem"] = "“欲望”这个词在句中的意思是（  ）"
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["将要", "想要", "很想得到", "需要"]}], question=quoted)
    assert result.returncode == 1
    assert "stem quotes the changed option C" in result.stderr


def test_explanation_quoting_a_changed_option_only_warns(workspace):
    """The explanation is prose, not a key: warn loudly, but do not block."""
    annotated = dict(QUESTION)
    annotated["explanation"] = "“欲望”是名词，与“将要”的词性不同，所以不能替换。"
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["将要", "想要", "很想得到", "需要"]}], question=annotated)
    assert result.returncode == 0, result.stderr
    assert "explanation quotes the changed option C" in result.stderr


def test_unknown_question_is_refused(workspace):
    result = run(workspace, [{"q": "chinese-6up-d99-q99",
                              "o": ["将要", "想要", "欲望", "需要"]}])
    assert result.returncode == 1
    assert "no such question" in result.stderr


def test_dry_run_leaves_the_bank_alone(workspace):
    run(workspace, [])                       # first call writes the scratch bank
    original = bank(workspace)
    run(workspace, [{"q": "chinese-6up-d01-q01",
                     "o": ["将要", "想要得到某样东西", "欲望", "需要"]}])
    assert bank(workspace) == original


def test_a_clean_patch_round_trips_the_file_format(workspace):
    """The banks are indent=1 with a trailing newline; a patch must not reformat the file."""
    run(workspace, [])                       # first call writes the scratch bank
    bank_path = workspace / "questions_chinese_6up.json"
    before = bank_path.read_text(encoding="utf-8").splitlines()

    spec = workspace / "patch.jsonl"
    spec.write_text(json.dumps({"q": "chinese-6up-d01-q01",
                                "o": ["将要", "想要得到某样东西", "欲望", "需要"]},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    code = f"""
import sys
sys.path.insert(0, {str(ROOT / 'scripts')!r})
import patch_options
patch_options.DATA = __import__('pathlib').Path({str(workspace)!r})
patch_options.BANKS = {{"chinese": patch_options.DATA / "questions_chinese_6up.json"}}
raise SystemExit(patch_options.main(sys.argv[1:]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, "--subject", "chinese", "--patch", str(spec)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    after = bank_path.read_text(encoding="utf-8").splitlines()
    assert len(after) == len(before), "only the option line may change"
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(changed) == 1, f"reformat detected, {len(changed)} lines changed"
    assert "想要得到某样东西" in after[changed[0]]


def test_patch_that_does_not_balance_is_refused(workspace):
    """The whole point of the round is B11; a patch that leaves the key longest is a no-op."""
    long_key = dict(QUESTION)
    long_key["options"] = ["把句子的意思先读懂再按意思断开", "按字数分", "按标点分", "凭感觉分"]
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["把句子的意思先读懂再按意思断开", "按字数分", "按标点分", "凭感觉分吧"]}],
                 question=long_key)
    assert result.returncode == 1
    assert "still the unique longest" in result.stderr


def test_balancing_by_matching_the_key_length_passes(workspace):
    """Two ways to balance: lengthen a distractor past the key, or make one of them equal it."""
    long_key = dict(QUESTION)
    long_key["options"] = ["把句子的意思先读懂再按意思断开", "按字数分", "按标点分", "凭感觉分"]
    result = run(workspace, [{"q": "chinese-6up-d01-q01",
                              "o": ["把句子的意思先读懂再按意思断开", "按字数平均分成几段，每段长度一样", "按标点分",
                                    "凭感觉分"]}],
                 question=long_key)
    assert result.returncode == 0, result.stderr
