"""薄弱项的状态机：窗口按题数、等级、立案/关闭/复发、主因线与证据题（T1–T14）。

这组用例守的是「计算行为」，不是 HTTP 形状：直接往 `mastery_event` 写账本行，再调
`weakness.sync`，所以每条用例只表达一段窗口内容，不需要真题库。

两条最关键的：
  T9  时间冻结——六十天后重算，窗口、WA、等级、状态、last_practiced_at 一个都不许动；
  T10 刷易题反例——连做易题把难题挤出窗口后，hard_correct 归零，即使 WA=1.0 也不许关闭。

Run with:  python3 -m pytest knowledge-eval/tests/test_weakness_calc.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.app import mastery, weakness  # noqa: E402
from server.app.db import Database  # noqa: E402

DEVICE = "dev-weakness-test-0000001"
TOPIC_ID = 1
NODE = "math-6up-d01"
T0 = "2026-11-21 10:00:00"
T60 = "2027-01-20 10:00:00"


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(f"sqlite:{Path(tmp) / 'weakness.sqlite3'}")
        database.connect(init_schema=True)
        database.execute("INSERT INTO subject (id, code, name_zh, sort_order)"
                         " VALUES (1, 'math', '数学', 1)")
        database.execute("INSERT INTO textbook (id, subject_id, publisher, grade, term, title_zh)"
                         " VALUES (1, 1, 'pep', 6, 'up', '六年级上册')")
        database.execute("INSERT INTO unit (id, textbook_id, ordinal, code, name_zh)"
                         " VALUES (1, 1, 1, 'math-6up-u01', '分数乘法')")
        database.execute("INSERT INTO topic (id, unit_id, ordinal, chapter_order, code, name_zh)"
                         " VALUES (?, 1, 1, 1, ?, '分数乘整数')", (TOPIC_ID, NODE))
        database.execute("INSERT INTO device (id) VALUES (?)", (DEVICE,))
        yield database
        database.close()


# ---------------------------------------------------------------------------
# helpers: one answer, one window
# ---------------------------------------------------------------------------

def next_qid(db) -> int:
    return int(db.scalar("SELECT COALESCE(MAX(id), 0) + 1 FROM question"))


def next_seq(db) -> int:
    return int(db.scalar(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM mastery_event"
        " WHERE device_id = ? AND topic_id = ?", (DEVICE, TOPIC_ID)))


def add_question(db, qid: int, difficulty: int, tag: str) -> None:
    db.execute(
        "INSERT INTO question (id, textbook_id, topic_id, code, angle, ability_tag, difficulty,"
        " qtype, stem, stem_len, template_key, param_hash)"
        " VALUES (?, 1, ?, ?, 'recall', ?, ?, 'choice', '题干', 2, ?, ?)",
        (qid, TOPIC_ID, f"q{qid}", tag, difficulty, f"t{qid}", f"h{qid}"),
    )


def put(db, qid: int, correct: bool, difficulty: int, tag: str, seq: int, at: str = T0) -> dict:
    db.execute(
        "INSERT INTO mastery_event (id, device_id, topic_id, question_id, ability_tag, kind, delta,"
        " score_after, is_correct, difficulty, weight_x10, counted, seq, ref_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 1, ?, ?, ?)",
        (f"ev-{qid}-{seq}", DEVICE, TOPIC_ID, qid, tag,
         "tv_right" if correct else "tv_wrong", 1 if correct else 0, difficulty,
         weakness.WEIGHT_X10[difficulty], seq, f"S-{seq}:{qid}", at),
    )
    with db.tx() as cur:
        return weakness.sync(cur, device_id=DEVICE, topic_id=TOPIC_ID, now_iso=at,
                             new_evidence=True)


def serve(db, plan: list[tuple[int, bool, str, int]], at: str = T0) -> dict:
    """plan entries: (difficulty, correct, tag, times). Unique questions, increasing seq."""
    qid = next_qid(db)
    seq = next_seq(db)
    result: dict = {}
    for difficulty, correct, tag, times in plan:
        for _ in range(times):
            add_question(db, qid, difficulty, tag)
            result = put(db, qid, correct, difficulty, tag, seq, at)
            qid += 1
            seq += 1
    return result


def answer_once(db, qid: int, correct: bool, difficulty: int, tag: str, at: str = T0) -> dict:
    seq = next_seq(db)
    add_question(db, qid, difficulty, tag)
    return put(db, qid, correct, difficulty, tag, seq, at)


def row(db) -> dict | None:
    return db.query_one(
        "SELECT * FROM weakness WHERE device_id = ? AND topic_id = ?", (DEVICE, TOPIC_ID))


def snapshot(db) -> dict | None:
    current = row(db)
    return None if current is None else json.loads(current["current_window_snapshot"])


# ---------------------------------------------------------------------------
# T1–T6: the arithmetic and the bands
# ---------------------------------------------------------------------------

def test_t1_wa_over_nine_answers(db):
    """T1: 易对 5、中对 3、难错 1 → 95/115 = 0.826，不立案。"""
    result = serve(db, [(1, True, "理解意义", 5), (2, True, "执行计算", 3),
                        (3, False, "辨析变式", 1)])
    assert result["n"] == 9 and result["wa"] == 826
    assert result["state"] is None
    assert row(db) is None


def test_t2_wa_exactly_four_tenths_is_level_one(db):
    """T2: WA 恰好 0.400 归 1 级，并立案。"""
    result = serve(db, [(2, True, "执行计算", 2), (2, False, "执行计算", 3)])
    assert result["wa"] == 400 and result["level"] == 1
    assert row(db) is not None and int(row(db)["resolved"]) == 0
    assert int(row(db)["mastery_level"]) == 1 and int(row(db)["initial_level"]) == 1


def test_t3_wa_exactly_six_tenths_is_level_two_and_not_weak(db):
    """T3: WA 恰好 0.600 归 2 级，不立案。"""
    result = serve(db, [(2, True, "执行计算", 3), (2, False, "执行计算", 2)])
    assert result["wa"] == 600 and result["level"] == 2
    assert row(db) is None


def test_t4_wa_exactly_eight_tenths_without_hard_stays_open(db):
    """T4: WA 0.800、hard_correct=0 → 2 级，关闭条件缺 hard，保持 active。"""
    serve(db, [(2, False, "执行计算", 3)])
    result = serve(db, [(2, True, "执行计算", 12)])
    assert result["wa"] == 800 and result["level"] == 2
    assert result["close_requirements"]["hard_ok"] is False
    assert int(row(db)["resolved"]) == 0


def test_t5_hard_right_but_two_medium_cannot_close(db):
    """T5: WA 0.800、hard=1、medium=2 → 3 级候选，但 medium<3，不关闭。"""
    serve(db, [(1, False, "执行计算", 3)])
    result = serve(db, [(3, True, "执行计算", 1), (2, True, "执行计算", 2),
                        (1, True, "执行计算", 7)])
    assert result["wa"] == 800 and result["level"] == 3
    assert result["close_requirements"] == {"wa_ok": True, "hard_ok": True, "medium_ok": False,
                                            "need_hard": 0, "need_medium": 1}
    assert int(row(db)["resolved"]) == 0


def test_t6_three_medium_and_one_hard_closes(db):
    """T6: WA ≥0.8、hard=1、medium=3 → 关闭。"""
    serve(db, [(1, False, "执行计算", 3)])
    result = serve(db, [(3, True, "执行计算", 1), (2, True, "执行计算", 3),
                        (1, True, "执行计算", 8)])
    assert result["wa"] == 829 and result["level"] == 3
    closed = row(db)
    assert int(closed["resolved"]) == 1 and closed["resolved_at"] is not None
    assert json.loads(closed["closing_snapshot"])["hard_correct"] == 1


# ---------------------------------------------------------------------------
# T7–T8: small samples and duplicate questions
# ---------------------------------------------------------------------------

def test_t7_two_wrong_answers_open_nothing(db):
    """T7: n=2 全错 → 不立案、无记录。"""
    serve(db, [(2, False, "执行计算", 2)])
    assert row(db) is None


def test_t8_same_question_three_wrong_one_right_counts_once(db):
    """T8: 同一题错 3 次对 1 次，窗口只取最近一次 → n=1，不立案。"""
    qid = 1
    add_question(db, qid, 2, "执行计算")
    for seq in range(1, 4):
        put(db, qid, False, 2, "执行计算", seq)
    result = put(db, qid, True, 2, "执行计算", 4)
    assert result["n"] == 1
    assert row(db) is None
    assert int(db.scalar("SELECT COUNT(*) FROM mastery_event")) == 4   # 历史仍在，只是不进窗口


# ---------------------------------------------------------------------------
# T9–T10: the two hard constraints
# ---------------------------------------------------------------------------

def test_t9_time_freeze_hard_constraint(db):
    """T9（硬约束验收）: 立案后 60 天无新作答，窗口、WA、等级、状态、last_practiced_at 全不变。"""
    serve(db, [(2, False, "执行计算", 2), (1, False, "理解意义", 1)])
    before = row(db)
    before_window = weakness.build_window(db, DEVICE, TOPIC_ID)
    assert before is not None and int(before["resolved"]) == 0

    later = weakness.sync(db, device_id=DEVICE, topic_id=TOPIC_ID, now_iso=T60,
                          new_evidence=False)

    after = row(db)
    assert later["n"] == len(before_window) == 3
    assert later["wa"] == int(before["weighted_accuracy"])
    assert int(after["mastery_level"]) == int(before["mastery_level"])
    assert int(after["resolved"]) == 0
    assert after["last_practiced_at"] == before["last_practiced_at"] == T0
    assert after["current_window_snapshot"] == before["current_window_snapshot"]
    assert [r["seq"] for r in weakness.build_window(db, DEVICE, TOPIC_ID)] == \
           [r["seq"] for r in before_window]


def test_t10_easy_questions_push_the_hard_one_out_hard_constraint(db):
    """T10（硬约束验收）: 连做 15 道易题把难题挤出窗口，hard_correct 归零，WA=1.0 也不关闭。"""
    serve(db, [(1, False, "执行计算", 3)])                  # 立案（WA 0）
    serve(db, [(3, True, "执行计算", 2)])                  # 两道难题答对
    result = serve(db, [(1, True, "执行计算", 15)])        # 15 道易题把难题挤出 15 条窗口

    assert result["n"] == 15 and result["wa"] == 1000
    assert result["close_requirements"]["hard_ok"] is False
    assert result["close_requirements"]["medium_ok"] is False
    kept = row(db)
    assert int(kept["resolved"]) == 0, "刷易题不能自动过关"
    assert json.loads(kept["current_window_snapshot"])["hard_correct"] == 0


# ---------------------------------------------------------------------------
# T11–T14: relapse, never-measured, level moves both ways, cause lines
# ---------------------------------------------------------------------------

def test_t11_relapse_reopens_and_counts(db):
    """T11: 已 resolved，WA 跌回 0.5 且 n≥3 → 重新 active，reopen_count=1。"""
    serve(db, [(1, False, "执行计算", 3)])                 # 3 错 → 立案
    serve(db, [(3, True, "执行计算", 4), (2, True, "执行计算", 8)])   # 关闭
    assert int(row(db)["resolved"]) == 1

    result = serve(db, [(2, False, "执行计算", 8)])        # 八道错，把窗口压到 0.54
    assert result["wa"] < 600
    reopened = row(db)
    assert int(reopened["resolved"]) == 0
    assert int(reopened["reopen_count"]) == 1
    assert reopened["opened_at"] is not None               # 首次立案时间保留
    history = json.loads(reopened["detection_history"])
    assert len(history) == 1 and history[0]["closing_snapshot"]["hard_correct"] >= 1


def test_t12_never_answered_creates_nothing(db):
    """T12: 从未作答的节点没有记录，状态机也不替它建一条。"""
    assert weakness.build_window(db, DEVICE, TOPIC_ID) == []
    result = weakness.sync(db, device_id=DEVICE, topic_id=TOPIC_ID, now_iso=T0)
    assert result["reason"] == "no-window"
    assert row(db) is None


def test_t13_level_moves_up_without_closing(db):
    """T13: 立案后补五道中题答对，等级 1→2，但没有难题 → 不关闭，仍 active。"""
    serve(db, [(2, True, "执行计算", 5), (2, False, "执行计算", 5)])
    opened = row(db)
    assert int(opened["mastery_level"]) == 1 and int(opened["resolved"]) == 0

    result = serve(db, [(2, True, "执行计算", 5)])
    assert result["level"] == 2 and result["wa"] == 667
    assert result["closed"] is not True
    assert int(row(db)["resolved"]) == 0


def test_t14_cause_lines_break_ties_by_hard_misses(db):
    """T14: 两条线都是 0 级时按难题答错数排序；样本不足则不指名。

    立案发生在第一次「n≥3 且 WA<0.6」，所以要让两条线在立案那一刻都攒够 2 条，必须先有三条
    正确作答把 WA 顶住：前三条 3 道难题答对（WA 1.0），第 4–6 条错题让 WA 掉到 0.600（仍不立案），
    第 7 条错题才越过 0.6。立案时 辨析变式 2 条（含 1 道难题）、执行计算 2 条（全易题）。
    """
    serve(db, [(3, True, "综合表达", 3),
               (3, False, "辨析变式", 1), (1, False, "辨析变式", 1),
               (1, False, "执行计算", 1), (1, False, "执行计算", 1)])
    causes = json.loads(row(db)["cause_tags"])
    assert row(db)["cause_status"] == "ok"
    assert [item["tag"] for item in causes] == ["辨析变式", "执行计算"]
    assert causes[0]["hard_wrong"] == 1 and causes[1]["hard_wrong"] == 0


def test_t14b_no_line_reaches_two_samples(db):
    """T14 的另一半：三条线各一条样本，主因线为空，报告要说「样本不足」。"""
    serve(db, [(2, False, "理解意义", 1), (2, False, "执行计算", 1),
               (2, False, "辨析变式", 1)])
    assert row(db)["cause_status"] == "insufficient_sample"
    assert json.loads(row(db)["cause_tags"]) == []


def test_evidence_is_wrong_questions_from_the_cause_line(db):
    """证据题：只取窗口内错题，主因线优先，再按难度、时间；最多三道。"""
    serve(db, [(1, False, "理解意义", 1), (3, False, "执行计算", 1),
               (2, False, "执行计算", 1), (1, True, "执行计算", 2)])
    evidence = json.loads(row(db)["evidence_question_ids"])
    assert len(evidence) == 3
    assert evidence[0] == "q2"          # 执行计算 难题
    assert evidence[1] == "q3"          # 执行计算 中题
    assert evidence[2] == "q1"          # 非主因线的易题排在最后
    assert "q4" not in evidence and "q5" not in evidence   # 答对的不进证据


def test_record_tv_answer_path_writes_the_state_machine(db):
    """接线：走 mastery.record_tv_answer（真实判分路径），三错之后应有 active 行。"""
    qid = next_qid(db)
    for seq in range(1, 4):
        add_question(db, qid, 2, "执行计算")
        with db.tx() as cur:
            mastery.record_tv_answer(
                cur, device_id=DEVICE, topic_id=TOPIC_ID, correct=False, flagged=False,
                session_id="01WEAKNESSTEST000000000001", question_id=qid,
                now_ms=1_763_700_000_000, now_iso=T0,
            )
        qid += 1
    opened = row(db)
    assert opened is not None and int(opened["resolved"]) == 0
    assert int(opened["mastery_level"]) == 0
    assert opened["diagnostic_node_id"] == NODE
    assert opened["parent_aggregate_name"] == "分数乘法"
