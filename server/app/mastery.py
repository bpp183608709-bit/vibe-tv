"""How answers become a level the picker can act on (docs/RULES.md §2, docs/WRONGBOOK.md §5).

One sentence holds the whole model: take the last ten answers on a topic, weight them by difficulty
(easy 1, medium 1.5, hard 2), and the weighted accuracy is the mastery — 0.4/0.6/0.8 are the bands.
No fitting, no forgetting curve, no probability. A parent asking "为什么是 62" gets an answer that is a
SUM over ten rows, which is what makes the ledger worth keeping.

| band                      | level | what the parent reads |
|---------------------------|-------|-----------------------|
| accuracy < 0.4            | 0     | 未掌握                |
| 0.4 ≤ accuracy < 0.6      | 1     | 基础                  |
| 0.6 ≤ accuracy ≤ 0.8      | 2     | 熟练                  |
| accuracy > 0.8 AND one hard question right | 3 | 拓展     |
| fewer than 3 answers      | —     | 待测（不判薄弱）      |

`score` is that accuracy as 0..100 where higher is better, plus the parent's bias. `delta` in the ledger
is still the effective change of `score`, so replaying the deltas lands on the stored score; the accuracy
itself is in `acc_after` / `sample_after`, which is what explains "why 62".

What counts as evidence (`counted=1`): every TV answer except "right but flagged 没学过" (B7: telling the
truth is neither punished nor rewarded), and at most three paper mistakes per topic per day (a parent
typing ten rows from one worksheet must not erase the ten real answers). Weights are integers in tenths
so nothing rounds twice.

Nothing here is ever shown to the child (docs/WRONGBOOK.md §10). These numbers decide what gets asked
next; the TV sees only questions.
"""

from __future__ import annotations

import datetime
import json

from server.app import weakness
from server.app.ids import new_ulid

#: How many answers the accuracy is computed over. Rolling, not cumulative.
MASTERY_WINDOW = 10
#: Difficulty -> weight in tenths. 易 1, 中 1.5, 难 2.
WEIGHT_X10 = {1: 10, 2: 15, 3: 20}
#: A paper mistake has no difficulty and no machine grading; one easy-weight row is all it is worth.
PAPER_WEIGHT_X10 = 10
#: A worksheet typed in one go may contribute at most this many rows to the window.
PAPER_PER_DAY = 3
#: Below this many answers the node reads 待测 and is never called weak.
MIN_SAMPLE = 3

#: Accuracy bands, in thousandths. Strict > for the top one (docs/RULES.md §2.3).
BAND_BASIC = 400
BAND_SKILLED = 600
BAND_EXTENDED = 800

LEVEL_NAMES = ("L0", "L1", "L2", "L3")
LEVEL_WORDS = ("0-未掌握", "1-基础", "2-熟练", "3-拓展")
#: Level -> weight in the review segment, as tenths. The weaker the topic, the more it deserves the slot.
LEVEL_WEIGHT_X10 = (40, 20, 10, 0)
#: Level -> how many servable questions the bank should keep for this topic.
LEVEL_TARGET_QTY = (10, 7, 5, 3)
#: Level -> how many of this topic's questions may enter one day's review segment.
LEVEL_DAILY_CAP = (3, 3, 3, 3)

MAX_SCORE = 100
MIN_SCORE = 0

PARENT_ADJUST = 10
PARENT_BIAS_MAX = 30
WRONG_STREAK_PAUSE = 5
PAUSE_MS = 3 * 24 * 60 * 60 * 1000


def clamp(score: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, int(score)))


def level_of(acc_x1000: int, hard_right: int = 0) -> int:
    """0..3 from the weighted accuracy. 拓展 needs a hard question actually answered right."""
    acc = max(0, min(1000, int(acc_x1000)))
    if acc < BAND_BASIC:
        return 0
    if acc < BAND_SKILLED:
        return 1
    if acc <= BAND_EXTENDED:
        return 2
    return 3 if hard_right >= 1 else 2


def score_of(acc_x1000: int, bias: int = 0) -> int:
    """0..100 for display and for the ledger's replay contract. Higher is better."""
    return clamp(round((acc_x1000 + 10 * bias) / 10))


def weight_x10(level: int) -> int:
    return LEVEL_WEIGHT_X10[level]


def target_qty(level: int) -> int:
    return LEVEL_TARGET_QTY[level]


def daily_cap(level: int) -> int:
    return LEVEL_DAILY_CAP[level]


def is_weak(sample_count: int, level: int) -> bool:
    """What the mix and the parent's weak list both mean by weak."""
    return int(sample_count) >= MIN_SAMPLE and int(level) <= 1


def still_paused(paused_until: str | None, now_ms: int) -> bool:
    """A pause is stored as an ISO timestamp and asked about in epoch milliseconds.

    Comparing the two as strings is the bug this function exists to prevent: `paused_until` is local
    time with no zone, so it has to go back through the same parser that wrote it.
    """
    if not paused_until:
        return False
    return _now_ms(paused_until) > int(now_ms)


def need_sample(sample_count: int) -> int:
    """Answers still missing before the node is judged at all."""
    return max(0, MIN_SAMPLE - int(sample_count))


def need_right(acc_x1000: int, sample_count: int, hard_right: int = 0, bias: int = 0,
               window: list[dict] | None = None) -> int:
    """How many more medium questions, answered right, until the next band. 11 means "10 或更多".

    Simulated rather than solved: with a full window the oldest row falls out as the newest comes in,
    and the row that falls out may itself be a wrong answer, so a closed form overstates. Ten steps of
    the same arithmetic the model uses is both shorter to read and exactly right.

    The band is strict `>`: three right of five is 0.6, which is still 熟练 with nothing left over, so
    reaching 拓展 costs six of them, not five.
    """
    if int(sample_count) < MIN_SAMPLE:
        return 0
    level = level_of(acc_x1000, hard_right)
    if level >= 3:
        return 0
    target = {0: BAND_BASIC, 1: BAND_SKILLED, 2: BAND_EXTENDED}[level]
    target = max(0, min(1000, target - 10 * bias))

    if window is not None:
        rows = [(int(row["is_correct"] or 0), int(row["weight_x10"])) for row in window][:MASTERY_WINDOW]
    else:  # no window at hand: rebuild an average one from the two numbers we do have
        total = max(1, int(sample_count) * WEIGHT_X10[2])
        got = round(acc_x1000 * total / 1000)
        rows = [(1, WEIGHT_X10[2])] * (got // WEIGHT_X10[2])
        rows += [(0, WEIGHT_X10[2])] * (int(sample_count) - len(rows))

    for k in range(1, MASTERY_WINDOW + 2):
        simulated = ([(1, WEIGHT_X10[2])] * k + rows)[:MASTERY_WINDOW]
        total = sum(weight for _, weight in simulated)
        got = sum(weight for correct, weight in simulated if correct)
        if total and got * 1000 > target * total:
            return k
    return MASTERY_WINDOW + 1


def window_of(cur, device_id: str, topic_id: int, ability_tag: str | None = None) -> list[dict]:
    """The rolling window, newest first. Ordering by `seq`, never by created_at: one submit writes five
    rows with the same timestamp.

    `ability_tag` narrows it to one cell. This is the difference the ability_mastery table exists for:
    a node has one ten-row window, but a cell has its own. Filtering the node's window instead would
    reproduce the bug this fixes — four lines cannot each hold three rows inside ten.
    """
    if ability_tag is None:
        return cur.query(
            "SELECT is_correct, weight_x10, seq FROM mastery_event"
            " WHERE device_id = ? AND topic_id = ? AND counted = 1"
            " ORDER BY seq DESC LIMIT ?",
            (device_id, topic_id, MASTERY_WINDOW),
        )
    return cur.query(
        "SELECT is_correct, weight_x10, seq FROM mastery_event"
        " WHERE device_id = ? AND topic_id = ? AND ability_tag = ? AND counted = 1"
        " ORDER BY seq DESC LIMIT ?",
        (device_id, topic_id, ability_tag, MASTERY_WINDOW),
    )


def summarise(rows: list[dict]) -> dict:
    """acc_x1000, sample and hard_right over window rows, newest first.

    Public because the offline report (knowledge-eval/scripts/evaluate.py) computes the same numbers
    from a JSON file. One model, one implementation of the arithmetic: if this is ever wrong, the
    database report and the file report are wrong together, which is the only kind of wrong that gets
    noticed.
    """
    total = sum(int(row["weight_x10"]) for row in rows)
    got = sum(int(row["weight_x10"]) for row in rows if int(row["is_correct"] or 0))
    hard = sum(1 for row in rows
               if int(row["is_correct"] or 0) and int(row["weight_x10"]) == WEIGHT_X10[3])
    return {
        "sample_count": len(rows),
        "weight_sum_x10": total,
        "correct_weight_x10": got,
        "acc_x1000": round(1000 * got / total) if total else 0,
        "hard_right": hard,
    }


def _summarise(rows: list[dict]) -> dict:
    return summarise(rows)


def _row(cur, device_id: str, topic_id: int, now_iso: str, lock: str) -> dict:
    """Read the topic's row, creating a zeroed one on first contact."""
    sql = "SELECT * FROM topic_mastery WHERE device_id = ? AND topic_id = ?"
    row = cur.query_one(sql + lock, (device_id, topic_id))
    if row is not None:
        return row
    cur.execute(
        "INSERT INTO topic_mastery (device_id, topic_id, updated_at) VALUES (?, ?, ?)",
        (device_id, topic_id, now_iso),
    )
    return cur.query_one(sql, (device_id, topic_id))


def _emit(cur, *, device_id: str, topic_id: int, kind: str, delta: int, score_after: int,
          now_iso: str, seq: int, counted: bool, is_correct: bool | None = None,
          difficulty: int | None = None, weight: int | None = None, acc_after: int | None = None,
          sample_after: int | None = None, ref_id: str | None = None, note: str = "",
          question_id: int | None = None, ability_tag: str | None = None) -> None:
    cur.execute(
        "INSERT INTO mastery_event (id, device_id, topic_id, question_id, ability_tag, kind, delta,"
        " score_after, is_correct, difficulty, weight_x10, counted, acc_after, sample_after, seq,"
        " ref_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_ulid(), device_id, topic_id, question_id, ability_tag, kind, delta, score_after,
         None if is_correct is None else (1 if is_correct else 0), difficulty, weight,
         1 if counted else 0, acc_after, sample_after, seq, ref_id, note or None, now_iso),
    )


def _store(cur, *, device_id: str, topic_id: int, summary: dict, bias: int, wrong_streak: int,
           paused_until: str | None, total_wrong: int, total_right: int, weak_since: str | None,
           seq: int, now_iso: str, new_evidence: bool = True) -> dict:
    """Write the derived columns, the drill-down and the weakness state machine, then hand back what
    callers return.

    All four writes are in the caller's transaction: the level, the ability split, the weakness row and
    the ledger entry it came from move together or not at all. A report that reads a level from one and
    a weakness from another is how a parent ends up looking at two different answers to the same question.

    `new_evidence` separates "the child answered something" from "the window was recomputed" (a voided
    question, a parent's ±10): the weakness row's `last_practiced_at` must not move for the latter.
    """
    level = level_of(summary["acc_x1000"], summary["hard_right"])
    score = score_of(summary["acc_x1000"], bias)
    abilities = _ability_breakdown(cur, device_id, topic_id)
    cur.execute(
        "UPDATE topic_mastery SET score = ?, level = ?, weight_x10 = ?, target_qty = ?,"
        " sample_count = ?, weight_sum_x10 = ?, correct_weight_x10 = ?, acc_x1000 = ?, hard_right = ?,"
        " seq = ?, bias = ?, right_streak = 0, wrong_streak = ?, paused_until = ?, total_wrong = ?,"
        " total_right = ?, weak_since = ?, ability_breakdown_json = ?, last_event_at = ?, updated_at = ?"
        " WHERE device_id = ? AND topic_id = ?",
        (score, level, weight_x10(level), target_qty(level), summary["sample_count"],
         summary["weight_sum_x10"], summary["correct_weight_x10"], summary["acc_x1000"],
         summary["hard_right"], seq, bias, wrong_streak, paused_until, total_wrong, total_right,
         weak_since, json.dumps(abilities, ensure_ascii=False) if abilities else None,
         now_iso, now_iso, device_id, topic_id),
    )
    # The weakness row is its own model (15 distinct questions, persisted state machine): see
    # server/app/weakness.py and docs/weakness-calc-mapping.md. This is the only place it is written.
    weakness.sync(cur, device_id=device_id, topic_id=topic_id, now_iso=now_iso,
                  new_evidence=new_evidence)
    return {"score": score, "level": level, "acc": summary["acc_x1000"] / 1000,
            "sample_count": summary["sample_count"], "paused_until": paused_until}


def _refresh_ability(cur, *, device_id: str, topic_id: int, ability_tag: str, now_iso: str,
                     seq: int | None = None) -> dict:
    """Recompute one cell from its own window and store it. Never writes the ledger.

    Called after every counted answer, so the cell moves with the node, and again after a question is
    voided, when the row leaves the window and the level has to fall back. The window is the cell's own
    `counted=1` rows, never a slice of the node's ten.
    """
    existing = cur.query_one(
        "SELECT level, sample_count, seq, weak_since FROM ability_mastery"
        " WHERE device_id = ? AND topic_id = ? AND ability_tag = ?",
        (device_id, topic_id, ability_tag),
    )
    rows = window_of(cur, device_id, topic_id, ability_tag=ability_tag)
    summary = summarise(rows)
    level = level_of(summary["acc_x1000"], summary["hard_right"])
    score = score_of(summary["acc_x1000"])
    last_seq = max([int(row["seq"]) for row in rows]
                   + [int(seq or 0), int(existing["seq"] or 0) if existing else 0])
    old_level = (int(existing["level"]) if existing is not None
                 and int(existing["sample_count"] or 0) >= MIN_SAMPLE else None)
    weak_since = _weak_since(old_level, level,
                             existing["weak_since"] if existing else None, now_iso)
    values = (score, level, summary["sample_count"], summary["weight_sum_x10"],
              summary["correct_weight_x10"], summary["acc_x1000"], summary["hard_right"],
              last_seq, weak_since, now_iso)
    if existing is None:
        cur.execute(
            "INSERT INTO ability_mastery (device_id, topic_id, ability_tag, score, level,"
            " sample_count, weight_sum_x10, correct_weight_x10, acc_x1000, hard_right, seq,"
            " weak_since, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (device_id, topic_id, ability_tag, *values),
        )
    else:
        cur.execute(
            "UPDATE ability_mastery SET score = ?, level = ?, sample_count = ?, weight_sum_x10 = ?,"
            " correct_weight_x10 = ?, acc_x1000 = ?, hard_right = ?, seq = ?, weak_since = ?,"
            " updated_at = ? WHERE device_id = ? AND topic_id = ? AND ability_tag = ?",
            (*values, device_id, topic_id, ability_tag),
        )
    return {"tag": ability_tag, "level": level, "acc": summary["acc_x1000"],
            "sample": summary["sample_count"]}


def _window_questions(cur, device_id: str, topic_id: int) -> list[dict]:
    """The counted rows of the current node window that name a question, newest first.

    Kept for the per-cell tests, which contrast this ten-row slice with the cells' own windows:
    `question_id` is stored on the event rather than parsed back out of `ref_id`, so it is one join
    instead of string surgery on '<session>:<question>'.
    """
    return cur.query(
        "SELECT e.question_id, e.is_correct, e.weight_x10, q.ability_tag, q.code AS question_code"
        "  FROM mastery_event e JOIN question q ON q.id = e.question_id"
        " WHERE e.device_id = ? AND e.topic_id = ? AND e.counted = 1 AND e.question_id IS NOT NULL"
        " ORDER BY e.seq DESC LIMIT ?",
        (device_id, topic_id, MASTERY_WINDOW),
    )


def _ability_breakdown(cur, device_id: str, topic_id: int) -> list[dict]:
    """Per ability line: its own level, acc and sample, weakest first.

    Read from ability_mastery rather than carved out of the node's window. The old version grouped the
    same ten rows by tag, which is why every line read "1-2 samples": ten rows cannot be split four ways
    and still give each part MIN_SAMPLE. Now each line has ten rows of its own.

    Two answers is still the floor for *showing* a line: one answer is an anecdote. The level is stored
    regardless, because a caller that asks for the cell's band (a targeted re-ask) wants it.
    """
    rows = cur.query(
        "SELECT ability_tag, sample_count, acc_x1000, level FROM ability_mastery"
        " WHERE device_id = ? AND topic_id = ? AND sample_count >= 2"
        " ORDER BY acc_x1000, ability_tag",
        (device_id, topic_id),
    )
    return [{"tag": row["ability_tag"], "sample": int(row["sample_count"]),
             "acc": int(row["acc_x1000"]), "level": int(row["level"])} for row in rows]


def _weak_since(old_level: int | None, new_level: int, current: str | None, now_iso: str) -> str | None:
    """Stamped when a node becomes weak, cleared when it stops. The day's "newcomer" slot keys on it."""
    weak_now = new_level <= 1
    if weak_now and (old_level is None or old_level > 1):
        return now_iso
    if not weak_now:
        return None
    return current


def _apply(cur, *, device_id: str, topic_id: int, row: dict, kind: str, is_correct: bool,
           weight: int, difficulty: int | None, counted: bool, ref_id: str | None, note: str,
           now_iso: str, wrong_streak: int, question_id: int | None = None,
           ability_tag: str | None = None) -> dict:
    """One evidence row: put it in the window, recompute, store, and write the ledger line."""
    seq = int(row["seq"] or 0) + 1
    bias = int(row["bias"] or 0)
    old_level = int(row["level"]) if int(row["sample_count"] or 0) >= MIN_SAMPLE else None
    old_score = int(row["score"] or 0)

    previous = window_of(cur, device_id, topic_id)
    candidate = {"is_correct": 1 if is_correct else 0, "weight_x10": weight, "seq": seq}
    rows = ([candidate] + previous)[:MASTERY_WINDOW] if counted else previous
    summary = _summarise(rows)
    level = level_of(summary["acc_x1000"], summary["hard_right"])
    score = score_of(summary["acc_x1000"], bias)

    if wrong_streak >= WRONG_STREAK_PAUSE:
        paused_until = to_iso(_now_ms(now_iso) + PAUSE_MS)
        wrong_streak = 0
    else:
        paused_until = row["paused_until"]

    weak_since = _weak_since(old_level, level, row["weak_since"], now_iso)
    total_wrong = int(row["total_wrong"] or 0) + (0 if is_correct else 1)
    total_right = int(row["total_right"] or 0) + (1 if is_correct else 0)

    _emit(cur, device_id=device_id, topic_id=topic_id, kind=kind, delta=score - old_score,
          score_after=score, now_iso=now_iso, seq=seq, counted=counted, is_correct=is_correct,
          difficulty=difficulty, weight=weight, acc_after=summary["acc_x1000"],
          sample_after=summary["sample_count"], ref_id=ref_id, note=note, question_id=question_id,
          ability_tag=ability_tag)
    # The cell moves before `_store` reads it: `_store` persists ability_breakdown_json, and a breakdown
    # written one answer behind the level it explains is exactly the drift this table exists to stop.
    if ability_tag and counted:
        _refresh_ability(cur, device_id=device_id, topic_id=topic_id, ability_tag=ability_tag,
                         now_iso=now_iso, seq=seq)
    stored = _store(cur, device_id=device_id, topic_id=topic_id, summary=summary, bias=bias,
                    wrong_streak=wrong_streak, paused_until=paused_until, total_wrong=total_wrong,
                    total_right=total_right, weak_since=weak_since, seq=seq, now_iso=now_iso,
                    new_evidence=counted)
    # The caller's `delta` and the ledger's `delta` are the same number, by construction: replaying the
    # events must land on the stored score. `acc` is what explains it, `delta` is what the toast shows.
    stored["delta"] = score - old_score
    return stored


def record_tv_answer(cur, *, device_id: str, topic_id: int, correct: bool, flagged: bool,
                     session_id: str, question_id: int, now_ms: int, now_iso: str,
                     difficulty: int = 2, lock: str = "") -> dict:
    """One answer from the television. Call inside the grading transaction, next to the answer row.

    A flagged answer keeps §5.5's meaning: wrong + flagged is still evidence (otherwise a child learns to
    tap 没学过 before every question), right + flagged is not evidence either way and writes no row.
    """
    row = _row(cur, device_id, topic_id, now_iso, lock)
    ref = f"{session_id}:{question_id}"
    weight = WEIGHT_X10.get(int(difficulty or 2), WEIGHT_X10[2])
    # Read once, from the question itself: the line is a property of what was asked, and the caller
    # grades an id, not a tag. A question imported before `ability_tag` existed reads NULL and moves
    # only the node row, which is the honest outcome — we do not know which line it tested.
    asked = cur.query_one("SELECT ability_tag FROM question WHERE id = ?", (int(question_id),))
    ability_tag = asked.get("ability_tag") if asked is not None else None

    if correct and flagged:
        # Nothing changes but the pause bookkeeping: reset the streak, keep the score.
        _store(cur, device_id=device_id, topic_id=topic_id, summary=_summarise(
            window_of(cur, device_id, topic_id)), bias=int(row["bias"] or 0), wrong_streak=0,
            paused_until=row["paused_until"], total_wrong=int(row["total_wrong"] or 0),
            total_right=int(row["total_right"] or 0),
            weak_since=row["weak_since"], seq=int(row["seq"] or 0), now_iso=now_iso,
            new_evidence=False)
        return {"score": int(row["score"] or 0), "level": int(row["level"] or 0),
                "acc": int(row["acc_x1000"] or 0) / 1000,
                "sample_count": int(row["sample_count"] or 0), "paused_until": row["paused_until"]}

    wrong_streak = 0 if correct else int(row["wrong_streak"] or 0) + 1
    return _apply(cur, device_id=device_id, topic_id=topic_id, row=row,
                  kind="tv_right" if correct else "tv_wrong", is_correct=correct, weight=weight,
                  difficulty=int(difficulty or 2), counted=True, ref_id=ref,
                  note="标记没学过" if flagged else ("电视答对" if correct else "电视答错"),
                  now_iso=now_iso, wrong_streak=wrong_streak, question_id=question_id,
                  ability_tag=ability_tag)


def record_paper_wrong(cur, *, device_id: str, topic_id: int, wrong_item_id: str, day_key: str,
                       now_iso: str, lock: str = "") -> dict:
    """A mistake the parent typed in from a paper. Call after the `wrong_item` row is inserted.

    The first three of the day count as evidence; the rest are still ledger rows (`counted=0`) so the
    parent can see them, but they do not push real answers out of the window.
    """
    today = cur.query_one(
        "SELECT COUNT(*) AS n FROM wrong_item"
        " WHERE device_id = ? AND topic_id = ? AND occurred_on = ? AND source = 'paper'"
        "   AND status IN ('pending', 'confirmed')",
        (device_id, topic_id, day_key),
    )
    position = int(today["n"] if today else 0)
    counted = position <= PAPER_PER_DAY
    row = _row(cur, device_id, topic_id, now_iso, lock)
    return _apply(cur, device_id=device_id, topic_id=topic_id, row=row, kind="paper_wrong",
                  is_correct=False, weight=PAPER_WEIGHT_X10, difficulty=None, counted=counted,
                  ref_id=wrong_item_id,
                  note="试卷错题" if counted else f"试卷错题（今日第 {position} 道，不计入窗口）",
                  now_iso=now_iso, wrong_streak=int(row["wrong_streak"] or 0))


def adjust(cur, *, device_id: str, topic_id: int, delta: int, note: str, now_iso: str,
           ref_id: str | None = None, lock: str = "") -> dict:
    """The parent's ±10, cumulative and clamped to ±30. Expressed in accuracy points, so a tap moves the
    displayed score by 10 and the band the same way ten percentage points of accuracy would.

    `ref_id` carries the account that pressed the button (docs/WRONGBOOK.md §9: 必填理由，写审计).
    """
    step = max(-PARENT_ADJUST, min(PARENT_ADJUST, int(delta)))
    row = _row(cur, device_id, topic_id, now_iso, lock)
    bias = max(-PARENT_BIAS_MAX, min(PARENT_BIAS_MAX, int(row["bias"] or 0) + step))
    summary = _summarise(window_of(cur, device_id, topic_id))
    old_score = int(row["score"] or 0)
    new_score = score_of(summary["acc_x1000"], bias)
    level = level_of(summary["acc_x1000"], summary["hard_right"])

    _emit(cur, device_id=device_id, topic_id=topic_id, kind="parent_adjust", delta=new_score - old_score,
          score_after=new_score, now_iso=now_iso, seq=int(row["seq"] or 0), counted=False,
          ref_id=ref_id, note=note or "家长手动调整")
    _store(cur, device_id=device_id, topic_id=topic_id, summary=summary, bias=bias,
           wrong_streak=int(row["wrong_streak"] or 0), paused_until=row["paused_until"],
           total_wrong=int(row["total_wrong"] or 0), total_right=int(row["total_right"] or 0),
           weak_since=_weak_since(int(row["level"]) if int(row["sample_count"] or 0) >= MIN_SAMPLE else None,
                                  level, row["weak_since"], now_iso),
           seq=int(row["seq"] or 0), now_iso=now_iso, new_evidence=False)
    return {"score": new_score, "level": level, "delta": new_score - old_score, "bias": bias}


def void_question(cur, *, device_id: str, question_id: int, note: str, now_iso: str,
                  ref_id: str | None = None) -> dict:
    """Take one bad question's answers out of the evidence, when a parent says "这题有问题".

    The rows are not subtracted — under a rolling window that would be meaningless. They are marked
    `counted=0` and the window is recomputed, then the score difference is written as one
    `bad_question_void` row so the ledger still replays to the stored score.
    """
    suffix = f":{int(question_id)}"
    rows = cur.query(
        "SELECT id, topic_id, kind, ref_id, ability_tag FROM mastery_event"
        " WHERE device_id = ? AND ref_id LIKE ? AND counted = 1",
        (device_id, f"%{suffix}"),
    )
    rows = [row for row in rows if str(row["ref_id"] or "").endswith(suffix)]

    per_topic: dict[int, dict] = {}
    for row in rows:
        cur.execute("UPDATE mastery_event SET counted = 0 WHERE id = ?", (row["id"],))
        slot = per_topic.setdefault(int(row["topic_id"]),
                                    {"wrong": 0, "right": 0, "abilities": set()})
        if row["kind"] == "tv_wrong":
            slot["wrong"] += 1
        elif row["kind"] == "tv_right":
            slot["right"] += 1
        if row.get("ability_tag"):
            slot["abilities"].add(row["ability_tag"])

    touched = []
    for topic_id, slot in per_topic.items():
        row = _row(cur, device_id, topic_id, now_iso, "")
        old_score = int(row["score"] or 0)
        summary = _summarise(window_of(cur, device_id, topic_id))
        bias = int(row["bias"] or 0)
        new_score = score_of(summary["acc_x1000"], bias)
        level = level_of(summary["acc_x1000"], summary["hard_right"])
        _emit(cur, device_id=device_id, topic_id=topic_id, kind="bad_question_void",
              delta=new_score - old_score, score_after=new_score, now_iso=now_iso,
              seq=int(row["seq"] or 0), counted=False, ref_id=ref_id,
              note=f"{note}（题目 #{int(question_id)}）" if note else f"题目 #{int(question_id)} 下架")
        # The voided rows left each cell's window too, so every cell they touched has to fall back.
        # Before `_store`, which is what persists the breakdown.
        for ability in sorted(slot["abilities"]):
            _refresh_ability(cur, device_id=device_id, topic_id=topic_id, ability_tag=ability,
                             now_iso=now_iso)
        _store(cur, device_id=device_id, topic_id=topic_id, summary=summary, bias=bias,
               wrong_streak=int(row["wrong_streak"] or 0), paused_until=row["paused_until"],
               total_wrong=max(0, int(row["total_wrong"] or 0) - slot["wrong"]),
               total_right=max(0, int(row["total_right"] or 0) - slot["right"]),
               weak_since=_weak_since(
                   int(row["level"]) if int(row["sample_count"] or 0) >= MIN_SAMPLE else None,
                   level, row["weak_since"], now_iso),
               seq=int(row["seq"] or 0), now_iso=now_iso, new_evidence=False)
        touched.append({"topic_id": topic_id, "score": new_score, "level": level,
                        "delta": new_score - old_score})
    return {"topics": touched, "events": len(rows)}


def to_iso(now_ms: int) -> str:
    return datetime.datetime.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _now_ms(now_iso: str) -> int:
    return int(datetime.datetime.strptime(str(now_iso)[:19], "%Y-%m-%d %H:%M:%S").timestamp() * 1000)


def state(db, device_id: str, topic_id: int) -> dict | None:
    """What the parent page reads. Not used by the TV."""
    row = db.query_one(
        "SELECT * FROM topic_mastery WHERE device_id = ? AND topic_id = ?", (device_id, topic_id)
    )
    if row is None:
        return None
    summary = _summarise(window_of(db, device_id, topic_id))
    level = level_of(summary["acc_x1000"], summary["hard_right"])
    bias = int(row["bias"] or 0)
    window = window_of(db, device_id, topic_id)
    return {
        "topic_id": topic_id,
        "score": score_of(summary["acc_x1000"], bias),
        "level": level,
        "level_name": LEVEL_NAMES[level],
        "level_word": LEVEL_WORDS[level],
        "weighted_accuracy": summary["acc_x1000"] / 1000,
        "sample_count": summary["sample_count"],
        "hard_right": summary["hard_right"],
        "bias": bias,
        "weight": weight_x10(level) / 10,
        "target_qty": target_qty(level),
        "need_sample": need_sample(summary["sample_count"]),
        "need_right": need_right(summary["acc_x1000"], summary["sample_count"],
                                 summary["hard_right"], bias, window),
        "paused_until": row["paused_until"],
        "total_wrong": int(row["total_wrong"] or 0),
        "total_right": int(row["total_right"] or 0),
        # The drill-down. Each entry carries its own level, because a line can be 未掌握 while the node
        # it belongs to is 基础 — averaging four lines into one band is what hid that until now.
        "ability_breakdown": _ability_breakdown(db, device_id, topic_id),
    }


def ability_lines_of(db, device_id: str, topic_id: int) -> dict[str, dict]:
    """Every measured cell of one node, keyed by tag, weakest first when iterated by `acc`.

    The picker uses this to aim a review question at the line that is actually weak instead of at
    whichever question happened to sort first. Returned with `sample_count` so a caller can decline to
    act on a cell with one answer in it.
    """
    rows = db.query(
        "SELECT ability_tag, level, score, sample_count, acc_x1000, hard_right"
        " FROM ability_mastery WHERE device_id = ? AND topic_id = ?"
        " ORDER BY acc_x1000, ability_tag",
        (device_id, topic_id),
    )
    return {row["ability_tag"]: {
        "tag": row["ability_tag"], "level": int(row["level"]), "score": int(row["score"]),
        "sample_count": int(row["sample_count"]), "acc_x1000": int(row["acc_x1000"]),
        "hard_right": int(row["hard_right"]),
    } for row in rows}


def _selfcheck() -> None:
    """The arithmetic a parent could check by hand. Run: python -m server.app.mastery"""
    # bands, and the seams that the four rules name
    assert [level_of(a) for a in (0, 399, 400, 599, 600, 800, 801)] == [0, 0, 1, 1, 2, 2, 2]
    assert level_of(801, hard_right=1) == 3
    assert level_of(1000, hard_right=0) == 2          # 全部答对但没有一道难题：封顶 2
    assert level_of(-50) == 0 and level_of(2000) == 2

    assert [weight_x10(i) for i in range(4)] == [40, 20, 10, 0]
    assert [target_qty(i) for i in range(4)] == [10, 7, 5, 3]
    assert [daily_cap(i) for i in range(4)] == [3, 3, 3, 3]
    assert is_weak(3, 0) and is_weak(3, 1) and not is_weak(2, 0) and not is_weak(3, 2)

    # one wrong answer on a fresh topic is 待测, not 未掌握: nothing is judged on one sample
    assert need_sample(0) == 3 and need_sample(2) == 1 and need_sample(3) == 0
    one_wrong = _summarise([{"is_correct": 0, "weight_x10": 15}])
    assert one_wrong["acc_x1000"] == 0 and one_wrong["sample_count"] == 1
    assert not is_weak(one_wrong["sample_count"], level_of(one_wrong["acc_x1000"]))

    # 2 wrong + 2 right of the same weight is exactly half
    half = _summarise([{"is_correct": 1, "weight_x10": 15}, {"is_correct": 1, "weight_x10": 15},
                       {"is_correct": 0, "weight_x10": 15}, {"is_correct": 0, "weight_x10": 15}])
    assert half["acc_x1000"] == 500 and level_of(half["acc_x1000"]) == 1
    assert score_of(half["acc_x1000"]) == 50

    # hard questions weigh double, and one right hard question is what unlocks 拓展
    hard = _summarise([{"is_correct": 1, "weight_x10": 20}, {"is_correct": 1, "weight_x10": 20},
                       {"is_correct": 1, "weight_x10": 20}, {"is_correct": 1, "weight_x10": 20},
                       {"is_correct": 0, "weight_x10": 10}])
    assert hard["acc_x1000"] == 889 and hard["hard_right"] == 4
    assert level_of(hard["acc_x1000"], hard["hard_right"]) == 3

    # the window keeps ten rows: an eleventh pushes the oldest out
    eleven = [{"is_correct": 0, "weight_x10": 10}] * 1 + [{"is_correct": 1, "weight_x10": 10}] * 10
    assert _summarise(eleven[:MASTERY_WINDOW])["acc_x1000"] == 900

    # the parent's bias moves the displayed score by ten and is clamped
    assert score_of(500, bias=10) == 60 and score_of(500, bias=-10) == 40
    assert score_of(0, bias=-30) == 0 and score_of(1000, bias=30) == 100

    # need_right: three right of five medium answers is 0.6; two wrong of five is 0.4 and needs three more
    three_right = [{"is_correct": 1, "weight_x10": 15}] * 3 + [{"is_correct": 0, "weight_x10": 15}] * 2
    assert _summarise(three_right)["acc_x1000"] == 600
    assert need_right(600, 5, window=three_right) == 6      # 0.6 → 0.8 是严格大于，五点五才刚好 0.8
    five = [{"is_correct": 1, "weight_x10": 15}] * 2 + [{"is_correct": 0, "weight_x10": 15}] * 3
    assert _summarise(five)["acc_x1000"] == 400
    assert need_right(400, 5, window=five) == 3             # 0.4 → 0.6 needs three more
    # already at the top band: nothing to promise
    assert need_right(900, 5, hard_right=1) == 0
    # too few answers: the page asks for samples, not rights
    assert need_right(0, 2) == 0
    print("mastery: ok")


if __name__ == "__main__":
    _selfcheck()
