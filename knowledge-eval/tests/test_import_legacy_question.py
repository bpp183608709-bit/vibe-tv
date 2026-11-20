"""旧题（`question.code` 仍是 NULL）重导时按去重键认领，不撞 `uk_question_dedup`。

为什么需要这组用例：2026-11 的 `flat-nodes` 迁移给 `question` 加了 `code` 列，但**回填不了**——
`param_hash` 是老名字的哈希，不是名字本身。于是老库里的每一道题 `code` 都是 NULL，而
`import_questions` 只按 `code` 判断"这道题见过没有"，把 39 道老题全当成新题去 INSERT，
撞 `uk_question_dedup (topic_id, template_key, param_hash)`：

    pymysql.err.IntegrityError: (1062, "Duplicate entry '31-hand-00411392d8184fd3' ...")

空库上同样看不到。修法不是回填 code，而是让导入器在 `code` 找不到时按去重键找一遍——
去重键本来就是"这是同一道题"的定义，认领之后这道题保留原 id，作答记录不会断。

Run with:  python -m pytest knowledge-eval/tests/test_import_legacy_question.py -q
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.app.db import Database  # noqa: E402
from server.tools import import_curriculum, import_questions  # noqa: E402

NODE = "math-6up-d01"
QID = "math-6up-d01-q01"

NODES = {
    "subject": "math",
    "subject_name_zh": "数学",
    "subject_id": 1,
    "publisher": "测试出版社",
    "grade": 6,
    "semester": "up",
    "textbook_title_zh": "测试教材",
    "nodes": [
        {"node_id": "math-6up-u01", "node_type": "aggregate", "chapter_order": 1,
         "name": "第一章", "week_start": None},
        {"node_id": NODE, "node_type": "diagnostic", "parent_id": "math-6up-u01",
         "chapter_order": 1, "name": "分数乘整数", "description": "说明",
         "knowledge_type": "concept", "micro_nodes": [NODE],
         "prerequisite_node_ids": [], "source_ids": []},
    ],
}

QUESTIONS = {
    "subject": "math",
    "questions": [
        {
            "question_id": QID,
            "diagnostic_node_id": NODE,
            "question_type": "选择题",
            "cognitive_level": "记忆理解",
            "ability_tag": "综合运用",
            "difficulty_preset": "易",
            "stem": "3/8 × 4 表示 4 个 3/8 相加，结果是（  ）",
            "options": ["3/2", "3/8", "4/8", "1/2"],
            "correct_answer": "A",
            "explanation": "四个 3/8 相加就是 3/8 × 4 = 3/2。",
        }
    ],
}


@pytest.fixture()
def workspace():
    """Scratch dir via mkdtemp: pytest's own `tmp_path` goes through a broker that fails here."""
    directory = Path(tempfile.mkdtemp(prefix="legacy-q-"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(f"sqlite:{Path(tmp) / 'legacy.sqlite3'}")
        database.connect(init_schema=True)
        import_curriculum.import_tree(database, [NODES])
        yield database
        database.close()


def _write_questions(tmp_dir: Path) -> Path:
    path = tmp_dir / "questions_math_6up.json"
    path.write_text(json.dumps(QUESTIONS, ensure_ascii=False), encoding="utf-8")
    return path


def _legacy_row(db) -> int:
    """模拟迁移后的老行：有去重键、没有 code。"""
    topic = db.query_one("SELECT id, unit_id FROM topic WHERE code = ?", (NODE,))
    textbook_id = db.query_one("SELECT textbook_id FROM unit WHERE id = ?", (topic["unit_id"],))
    return db.insert(
        "INSERT INTO question (textbook_id, topic_id, angle, difficulty, qtype, stem, stem_len,"
        " verify_status, source, template_key, param_hash)"
        " VALUES (?, ?, 'recall', 1, 'choice', ?, 3, 'manual_ok', 'manual', 'hand', ?)",
        (int(textbook_id["textbook_id"]), int(topic["id"]), "老题干",
         import_questions.param_hash(QID)),
    )


def test_legacy_row_is_adopted_not_duplicated(db, workspace):
    legacy_id = _legacy_row(db)
    path = _write_questions(workspace)

    stats = import_questions.import_file(db, path)

    assert stats["created"] == 0, "code 为 NULL 的老行应按去重键认领，不该新建"
    assert stats["updated"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM question")["n"] == 1

    row = db.query_one("SELECT id, code, stem FROM question")
    assert int(row["id"]) == legacy_id, "认领要保留原 id，作答记录才不会断"
    assert row["code"] == QID, "认领后应采纳文件里的 code，下次就按 code 命中"


def test_second_import_matches_on_code(db, workspace):
    _legacy_row(db)
    path = _write_questions(workspace)
    import_questions.import_file(db, path)

    stats = import_questions.import_file(db, path)

    assert stats["created"] == 0
    assert stats["updated"] == 1, "第二次应直接按 code 命中"
    assert db.query_one("SELECT COUNT(*) AS n FROM question")["n"] == 1
