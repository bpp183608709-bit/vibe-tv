"""能力线判档：每条线有自己的窗口，节点级行为不变。

为什么需要这组用例：在 `ability_mastery` 之前，四条能力线共用节点的十行窗口。四条线各要
MIN_SAMPLE(3) 条就是十二行，装不进十行，滚动窗口还会把最早的挤出去。这不是题量问题，是结构问题，
所以它必须由用例守住，而不是由文档保证。

三个断言：
  1. 十二条作答之后节点窗口只留十条（`执行计算` 被挤到 2 条，读不了档），而每条线各留 4 条；
  2. 试卷错题没有能力线，只动节点行；
  3. 题目被下架后，它所在单元格的样本数要跟着掉下来，不能停在下架前的档位。

Run with:  python -m pytest knowledge-eval/tests/test_ability_mastery.py -q
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.app import mastery  # noqa: E402
from server.app.db import Database  # noqa: E402
from server.tools import import_curriculum, import_questions  # noqa: E402

NODE = "math-6up-d01"
DEVICE = "dev-ability-test-00000001"


@pytest.fixture(scope="module")
def db():
    with tempfile.TemporaryDirectory() as tmp:
        dsn = f"sqlite:{Path(tmp) / 'ability.sqlite3'}"
        database = Database(dsn)
        database.connect(init_schema=True)
        import_curriculum.import_tree(database, import_curriculum.load_files())
        import_questions.main(["--dsn", dsn])
        database.execute("INSERT INTO device (id) VALUES (?)", (DEVICE,))
        yield database
        database.close()


def topic_id(db) -> int:
    return int(db.query_one("SELECT id FROM topic WHERE code = ?", (NODE,))["id"])


def answer(db, question: dict, index: int, correct: bool) -> None:
    mastery.record_tv_answer(
        db, device_id=DEVICE, topic_id=topic_id(db), correct=correct, flagged=False,
        session_id=f"s{index:04d}", question_id=int(question["id"]),
        now_ms=1763000000000 + index * 1000, now_iso=f"2026-11-19 10:{index % 60:02d}:00",
        difficulty=int(question["difficulty"]),
    )


def cells(db) -> dict[str, dict]:
    return {row["ability_tag"]: dict(row) for row in db.query(
        "SELECT ability_tag, sample_count, acc_x1000, level FROM ability_mastery"
        " WHERE device_id = ? AND topic_id = ?", (DEVICE, topic_id(db)))}


def test_each_line_keeps_its_own_window(db):
    """Twelve answers fill the node's window to ten; the lines must still hold all twelve."""
    questions = {}
    for row in db.query("SELECT id, ability_tag, difficulty FROM question WHERE topic_id = ?",
                        (topic_id(db),)):
        questions.setdefault(row["ability_tag"], []).append(row)
    assert len(questions) >= 3, "the maths bank should give d01 at least three lines"

    index = 0
    for tag in sorted(questions):
        for k in range(4):
            index += 1
            answer(db, questions[tag][k % len(questions[tag])], index, correct=(k != 3))

    node_window = mastery.window_of(db, DEVICE, topic_id(db))
    assert len(node_window) == mastery.MASTERY_WINDOW == 10, "the node window is still ten rows"

    # The old drill-down sliced this window by tag, so one line would read 2 — under MIN_SAMPLE and
    # therefore unjudgeable. This is the failure the table exists to remove.
    sliced: dict[str, int] = {}
    for row in mastery._window_questions(db, DEVICE, topic_id(db)):
        sliced[row["ability_tag"]] = sliced.get(row["ability_tag"], 0) + 1
    assert 2 in sliced.values(), "the fixture must reproduce the squeeze, or it proves nothing"

    stored = cells(db)
    assert set(stored) == set(questions)
    for tag, row in stored.items():
        assert int(row["sample_count"]) == 4, tag
        assert int(row["level"]) in (0, 1, 2, 3)


def test_paper_mistake_moves_only_the_node(db):
    before = cells(db)
    node = db.query_one("SELECT sample_count FROM topic_mastery WHERE device_id = ? AND topic_id = ?",
                        (DEVICE, topic_id(db)))
    db.execute(
        "INSERT INTO wrong_item (id, device_id, topic_id, source, status, occurred_on)"
        " VALUES (?, ?, ?, 'paper', 'pending', '2026-11-19')",
        ("wi-ability-test-00000001", DEVICE, topic_id(db)),
    )
    mastery.record_paper_wrong(db, device_id=DEVICE, topic_id=topic_id(db),
                               wrong_item_id="wi-ability-test-00000001", day_key="2026-11-19",
                               now_iso="2026-11-19 12:00:00")
    after = cells(db)
    assert {tag: row["sample_count"] for tag, row in after.items()} == \
           {tag: row["sample_count"] for tag, row in before.items()}, \
        "a paper mistake has no ability line, so no cell may move"
    node_after = db.query_one(
        "SELECT sample_count FROM topic_mastery WHERE device_id = ? AND topic_id = ?",
        (DEVICE, topic_id(db)))
    # The window was already full, so the node keeps ten rows (one falls out as one comes in). What
    # matters is that the row exists, is counted, and carries no line.
    assert int(node_after["sample_count"]) == int(node["sample_count"]) == mastery.MASTERY_WINDOW
    event = db.query_one(
        "SELECT ability_tag, counted FROM mastery_event"
        " WHERE device_id = ? AND kind = 'paper_wrong' ORDER BY seq DESC LIMIT 1", (DEVICE,))
    assert event is not None and event["ability_tag"] is None


def test_voiding_a_question_falls_the_cell_back(db):
    """A retired question's answers leave the window, so the cell's band must be recomputed."""
    victim = db.query_one(
        "SELECT q.id, q.ability_tag FROM question q WHERE q.topic_id = ? ORDER BY q.id LIMIT 1",
        (topic_id(db),),
    )
    before = cells(db)[victim["ability_tag"]]["sample_count"]
    assert before >= 1
    mastery.void_question(db, device_id=DEVICE, question_id=int(victim["id"]),
                          note="题目下架", now_iso="2026-11-19 13:00:00")
    after = cells(db)[victim["ability_tag"]]["sample_count"]
    assert after < before, "the voided rows are out of this cell's window too"
