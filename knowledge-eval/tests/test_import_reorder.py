"""章节重排之后重导节点：不能撞 `topic.uk_topic`。

为什么需要这组用例：聚合节点的 `code` 是**位置**（`math-6up-u01`），而 2024 版课本把「确定位置」
提到第 1 章、「分数乘法」挪到第 2 章。于是 `u01`/`u02` 在一次导入里互换，诊断节点按 `code` 匹配到
旧行后要搬到新的 `(unit_id, ordinal)`，那个位置却还被另一条尚未搬走的节点占着：

    pymysql.err.IntegrityError: (1062, "Duplicate entry '17-1' for key 'topic.uk_topic'")

空库上永远看不到这个问题——没有旧行可撞。所以它只能由用例守住，而且必须**先导入一版再导入重排版**，
只导入一次是测不出来的。

Run with:  python -m pytest knowledge-eval/tests/test_import_reorder.py -q
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.app.db import Database  # noqa: E402
from server.tools import import_curriculum  # noqa: E402


def _node(node_id: str, parent: str, ordinal: int) -> dict:
    return {
        "node_id": node_id,
        "node_type": "diagnostic",
        "parent_id": parent,
        "chapter_order": ordinal,
        "name": node_id,
        "description": f"{node_id} 的说明",
        "knowledge_type": "concept",
        "micro_nodes": [node_id],
        "prerequisite_node_ids": [],
        "source_ids": [],
    }


def _unit(node_id: str, ordinal: int, name: str) -> dict:
    return {
        "node_id": node_id,
        "node_type": "aggregate",
        "chapter_order": ordinal,
        "name": name,
        "week_start": None,
    }


def _payload(first: str, second: str) -> dict:
    """`first` 挂在 u01，`second` 挂在 u02。调换两个参数就等于把两章对调。"""
    return {
        "subject": "math",
        "subject_name_zh": "数学",
        "subject_id": 1,
        "publisher": "测试出版社",
        "grade": 6,
        "semester": "up",
        "textbook_title_zh": "测试教材",
        "nodes": [
            _unit("math-6up-u01", 1, "第一章"),
            _unit("math-6up-u02", 2, "第二章"),
            _node(first, "math-6up-u01", 1),
            _node(second, "math-6up-u02", 2),
        ],
    }


def _positions(db) -> dict[str, tuple[int, int]]:
    rows = db.query(
        "SELECT t.code, u.code AS unit, t.ordinal FROM topic t JOIN unit u ON u.id = t.unit_id"
        " WHERE t.code IS NOT NULL"
    )
    return {row["code"]: (row["unit"], int(row["ordinal"])) for row in rows}


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(f"sqlite:{Path(tmp) / 'reorder.sqlite3'}")
        database.connect(init_schema=True)
        yield database
        database.close()


def test_swapping_two_chapters_does_not_collide(db):
    """重排后再导一次：两章对调，不得抛 IntegrityError，且节点跟到新位置。"""
    import_curriculum.import_tree(db, [_payload("math-6up-d01", "math-6up-d02")])
    assert _positions(db) == {"math-6up-d01": ("math-6up-u01", 1),
                              "math-6up-d02": ("math-6up-u02", 1)}

    # 没有这一步修复时，这条 import_tree 会撞 topic.uk_topic（unit_id=1, ordinal=1）。
    stats = import_curriculum.import_tree(db, [_payload("math-6up-d02", "math-6up-d01")])

    assert stats["topics"] == 0, "两条节点都应被 code 匹配到，不该新建"
    assert _positions(db) == {"math-6up-d02": ("math-6up-u01", 1),
                              "math-6up-d01": ("math-6up-u02", 1)}


def test_reimport_is_idempotent(db):
    """同样的文件导三次，位置、行数都不变（+100 的让位必须可重复）。"""
    payload = _payload("math-6up-d01", "math-6up-d02")
    for _ in range(3):
        import_curriculum.import_tree(db, [payload])

    assert _positions(db) == {"math-6up-d01": ("math-6up-u01", 1),
                              "math-6up-d02": ("math-6up-u02", 1)}
    assert db.query_one("SELECT COUNT(*) AS n FROM topic")["n"] == 2


def test_moving_between_units_is_not_reported_as_stale(db, capsys):
    """`math-6up-d01` 从 u01 搬到 u02 是"搬家"，不是"删掉了"。

    判定必须等所有单元摆完再做。原来在单元循环里判，u01 处理时 d01 还躺在 u01，
    于是被报成 gone from the file —— 真实迁移里刷了 14 行，实际只有 7 行是真的。
    """
    import_curriculum.import_tree(db, [_payload("math-6up-d01", "math-6up-d02")])
    capsys.readouterr()

    stats = import_curriculum.import_tree(db, [_payload("math-6up-d02", "math-6up-d01")])

    assert stats["skipped_topics"] == 0
    assert "still in the database" not in capsys.readouterr().err


def test_dropped_node_is_reported_once(db, capsys):
    """真被删掉的节点要报，且只报一次。"""
    import_curriculum.import_tree(db, [_payload("math-6up-d01", "math-6up-d02")])
    capsys.readouterr()

    only_first = _payload("math-6up-d01", "math-6up-d02")
    only_first["nodes"] = [n for n in only_first["nodes"] if n["node_id"] != "math-6up-d02"]
    stats = import_curriculum.import_tree(db, [only_first])

    assert stats["skipped_topics"] == 1
    err = capsys.readouterr().err
    assert "math-6up-d02" in err
    assert err.count("still in the database") == 1


def test_new_node_does_not_adopt_a_coded_row(db, capsys):
    """新节点占的序号如果被旧节点占着，要新建，不能把旧节点改名覆盖掉。

    `ordinal + 100` 的兜底只对 `code IS NULL` 的老行有效。少了这个条件，`math-6up-d27` 会在
    序号 1 上认领 `math-6up-d10`（它已被让位到 101），把「比的意义」变成「负数」，而且不再报残留。
    """
    import_curriculum.import_tree(db, [_payload("math-6up-d01", "math-6up-d02")])
    capsys.readouterr()

    replaced = _payload("math-6up-d99", "math-6up-d02")
    stats = import_curriculum.import_tree(db, [replaced])

    assert stats["topics"] == 1, "d99 是全新节点，应新建一行"
    assert stats["skipped_topics"] == 1, "d01 应作为残留被报出来，而不是被 d99 覆盖"
    assert "math-6up-d01" in capsys.readouterr().err
    codes = {row["code"] for row in db.query("SELECT code FROM topic WHERE code IS NOT NULL")}
    assert codes == {"math-6up-d01", "math-6up-d02", "math-6up-d99"}, "d01 必须原样留着，不能被改名"
