"""How wrong answers become a number the picker can act on (docs/WRONGBOOK.md §5).

One sentence holds the whole model: every event has a fixed weight, the score is their sum clamped to
0..100, and the level is read off that score. No fitting, no forgetting curve, no probability. A parent
asking "why is this at 72" must get an answer that does not need a whiteboard, and a test must be able
to assert it.

| event                                   | ΔM  | why |
|-----------------------------------------|-----|-----|
| wrong on the TV (not flagged 没学过)     | +12 | one wrong answer is one clear signal |
| right on the TV                         | −10 | 1.2 rights per wrong; the asymmetry is the point |
| flagged 没学过, answered right           | 0   | B7: telling the truth is not punished, and not rewarded |
| flagged 没学过, answered wrong           | +12 | the flag does not buy silence (§5.5) |
| second right answer in a row            | −10 | a visible speed-up: one pair is worth −30 in total |
| paper mistake, first of the day          | +20 | a mistake on paper counts for more than one on screen |
| paper mistake, second and later today    | +8  | diminishing: one worksheet must not max out a topic |
| parent adjustment                        | ±10 | the escape hatch when the parent knows better |

`delta` in the ledger is the **effective** change, not the nominal weight: when the score is already at
0 or 100 the clamp eats the difference and recording the nominal number would make the ledger fail to
add up. Replaying the deltas must always land on the stored score, which is what makes the ledger
worth keeping.

Nothing here is ever shown to the child (docs/WRONGBOOK.md §10). These numbers decide what gets asked
next; the TV sees only questions.
"""

from __future__ import annotations

import datetime
from math import ceil

from server.app.ids import new_ulid

#: score range -> level. Changing a boundary means changing this one table (docs/WRONGBOOK.md §5.2).
LEVEL_BOUNDS = ((0, 11), (12, 35), (36, 70), (71, 100))
LEVEL_NAMES = ("L0", "L1", "L2", "L3")
#: Level -> weight in the review segment, as tenths. Integers so nothing rounds twice.
LEVEL_WEIGHT_X10 = (10, 20, 30, 40)
#: Level -> how many servable questions the generator should keep for this topic (§5.2).
LEVEL_TARGET_QTY = (0, 5, 7, 10)
#: Level -> how many questions of this topic may enter the review segment in one day (§5.4).
LEVEL_DAILY_CAP = (0, 3, 3, 3)

MAX_SCORE = 100
MIN_SCORE = 0

WRONG_TV = 12
RIGHT_TV = -10
STREAK_BONUS = -10
STREAK_LEN = 2
WRONG_PAPER_FIRST = 20
WRONG_PAPER_MORE = 8
PARENT_ADJUST = 10
WRONG_STREAK_PAUSE = 5
PAUSE_MS = 3 * 24 * 60 * 60 * 1000


def clamp(score: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, int(score)))


def level_of(score: int) -> int:
    """0..3. The four bands in LEVEL_BOUNDS; a score outside 0..100 is clamped first."""
    score = clamp(score)
    for level, (low, high) in enumerate(LEVEL_BOUNDS):
        if low <= score <= high:
            return level
    return len(LEVEL_BOUNDS) - 1


def weight_x10(level: int) -> int:
    return LEVEL_WEIGHT_X10[level]


def target_qty(level: int) -> int:
    return LEVEL_TARGET_QTY[level]


def daily_cap(level: int) -> int:
    """Questions of this topic allowed into one day's review segment. L0 is never in it."""
    return LEVEL_DAILY_CAP[level]


def need_right(score: int) -> int:
    """How many more right answers until the topic leaves the weak list, for the parent page only.

    Conservative on purpose: it ignores the streak bonus, so the child finishes earlier than the
    number promised. Promising 2 and needing 3 is a lie; the other way round is a pleasant surprise.
    """
    score = clamp(score)
    if level_of(score) == 0:
        return 0
    return ceil((score - LEVEL_BOUNDS[0][1]) / 10)


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
          ref_id: str | None, note: str, now_iso: str) -> None:
    cur.execute(
        "INSERT INTO mastery_event (id, device_id, topic_id, kind, delta, score_after, ref_id, note,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new_ulid(), device_id, topic_id, kind, delta, score_after, ref_id, note or None, now_iso),
    )


def _store(cur, *, device_id: str, topic_id: int, score: int, right_streak: int, wrong_streak: int,
           paused_until: str | None, total_wrong: int, total_right: int, now_iso: str) -> None:
    level = level_of(score)
    cur.execute(
        "UPDATE topic_mastery SET score = ?, level = ?, weight_x10 = ?, target_qty = ?,"
        " right_streak = ?, wrong_streak = ?, paused_until = ?, total_wrong = ?, total_right = ?,"
        " last_event_at = ?, updated_at = ? WHERE device_id = ? AND topic_id = ?",
        (score, level, weight_x10(level), target_qty(level), right_streak, wrong_streak,
         paused_until, total_wrong, total_right, now_iso, now_iso, device_id, topic_id),
    )


def record_tv_answer(cur, *, device_id: str, topic_id: int, correct: bool, flagged: bool,
                     session_id: str, question_id: int, now_ms: int, now_iso: str,
                     lock: str = "") -> dict:
    """One answer from the television. Call inside the grading transaction, next to the answer row.

    A flagged answer is handled the way §5.5 says: wrong + flagged still counts as wrong (+12), because
    otherwise a child learns to tap "没学过" before every question; right + flagged counts as nothing,
    because it is not evidence either way. The streak counters are reset by both, since neither is a
    confident answer.
    """
    row = _row(cur, device_id, topic_id, now_iso, lock)
    score = int(row["score"])
    right_streak = int(row["right_streak"] or 0)
    wrong_streak = int(row["wrong_streak"] or 0)
    total_right = int(row["total_right"] or 0)
    total_wrong = int(row["total_wrong"] or 0)
    paused_until = row["paused_until"]
    ref = f"{session_id}:{question_id}"

    def bump(delta: int, kind: str, note: str) -> int:
        nonlocal score
        before = score
        score = clamp(score + delta)
        _emit(cur, device_id=device_id, topic_id=topic_id, kind=kind, delta=score - before,
              score_after=score, ref_id=ref, note=note, now_iso=now_iso)
        return score - before

    if correct and flagged:
        right_streak = 0
        wrong_streak = 0
        # No ledger row: the event table is a list of changes, and this changed nothing.
    elif correct:
        bump(RIGHT_TV, "tv_right", "电视答对")
        total_right += 1
        right_streak += 1
        wrong_streak = 0
        if right_streak >= STREAK_LEN:
            bump(STREAK_BONUS, "streak_bonus", "连对加成")
            right_streak = 0
    else:
        bump(WRONG_TV, "tv_wrong", "标记没学过" if flagged else "电视答错")
        total_wrong += 1
        wrong_streak += 1
        right_streak = 0
        if wrong_streak >= WRONG_STREAK_PAUSE and not _still_paused(paused_until, now_ms):
            # Three days off the review segment, and the counter restarts: without the reset the sixth
            # wrong answer would pause it again immediately, which is a permanent pause by accident.
            paused_until = to_iso(now_ms + PAUSE_MS)
            wrong_streak = 0

    _store(cur, device_id=device_id, topic_id=topic_id, score=score, right_streak=right_streak,
           wrong_streak=wrong_streak, paused_until=paused_until, total_wrong=total_wrong,
           total_right=total_right, now_iso=now_iso)
    return {"score": score, "level": level_of(score), "paused_until": paused_until}


def record_paper_wrong(cur, *, device_id: str, topic_id: int, wrong_item_id: str, day_key: str,
                       now_iso: str, lock: str = "") -> dict:
    """A mistake the parent entered from a paper. Call after the `wrong_item` row is inserted: the count
    of today's paper mistakes for this topic (this one included) decides +20 or +8."""
    today = cur.query_one(
        "SELECT COUNT(*) AS n FROM wrong_item"
        " WHERE device_id = ? AND topic_id = ? AND occurred_on = ? AND source = 'paper'"
        "   AND status IN ('pending', 'confirmed')",
        (device_id, topic_id, day_key),
    )
    first_of_day = int(today["n"] if today else 0) <= 1
    delta = WRONG_PAPER_FIRST if first_of_day else WRONG_PAPER_MORE

    row = _row(cur, device_id, topic_id, now_iso, lock)
    score = int(row["score"])
    before = score
    score = clamp(score + delta)
    _emit(cur, device_id=device_id, topic_id=topic_id, kind="paper_wrong", delta=score - before,
          score_after=score, ref_id=wrong_item_id,
          note="试卷错题（当日首道）" if first_of_day else "试卷错题（当日第 2 道起）", now_iso=now_iso)
    _store(cur, device_id=device_id, topic_id=topic_id, score=score,
           right_streak=0, wrong_streak=0, paused_until=row["paused_until"],
           total_wrong=int(row["total_wrong"] or 0) + 1,
           total_right=int(row["total_right"] or 0), now_iso=now_iso)
    return {"score": score, "level": level_of(score), "delta": score - before}


def adjust(cur, *, device_id: str, topic_id: int, delta: int, note: str, now_iso: str,
           ref_id: str | None = None, lock: str = "") -> dict:
    """The parent's ±10. Signed by the caller; the absolute value is capped at PARENT_ADJUST so a
    stray value from the H5 cannot rewrite a score in one request.

    `ref_id` carries the account that pressed the button (docs/WRONGBOOK.md §9: 必填理由，写审计). The
    reason is in `note`; the actor is an id in `ref_id`, which is what every other kind uses that
    column for — the ledger stays one table, with the meaning of the reference decided by `kind`.
    """
    step = max(-PARENT_ADJUST, min(PARENT_ADJUST, int(delta)))
    row = _row(cur, device_id, topic_id, now_iso, lock)
    score = clamp(int(row["score"]) + step)
    _emit(cur, device_id=device_id, topic_id=topic_id, kind="parent_adjust", delta=score - int(row["score"]),
          score_after=score, ref_id=ref_id, note=note or "家长手动调整", now_iso=now_iso)
    _store(cur, device_id=device_id, topic_id=topic_id, score=score,
           right_streak=0, wrong_streak=0, paused_until=row["paused_until"],
           total_wrong=int(row["total_wrong"] or 0), total_right=int(row["total_right"] or 0),
           now_iso=now_iso)
    return {"score": score, "level": level_of(score), "delta": step}


def void_question(cur, *, device_id: str, question_id: int, note: str, now_iso: str,
                  ref_id: str | None = None) -> dict:
    """Undo what one bad question did, when a parent says "这题有问题" (docs/WRONGBOOK.md §11).

    The events caused by a question are the ones whose `ref_id` is `session:question` and ends with this
    question's id. Their deltas are summed per topic and applied backwards in a single
    `bad_question_void` row, so the ledger still adds up: replaying every row lands on the stored score.

    The counters are rewound as well (`total_wrong` / `total_right`), because they are shown to the
    parent next to the score and a total that counts a withdrawn question would contradict the flow.
    Streaks are not: they are transient, and the next answer re-establishes them.
    """
    suffix = f":{int(question_id)}"
    rows = cur.query(
        "SELECT topic_id, kind, delta, ref_id FROM mastery_event"
        " WHERE device_id = ? AND ref_id LIKE ?",
        (device_id, f"%{suffix}"),
    )
    # `LIKE '%:12'` cannot match '…:112', but it can match a ref whose session id happens to end in a
    # colon; the exact suffix check keeps this from depending on that being impossible.
    rows = [row for row in rows if str(row["ref_id"] or "").endswith(suffix)]

    per_topic: dict[int, dict] = {}
    for row in rows:
        if row["kind"] == "parent_adjust" or row["kind"] == "bad_question_void":
            continue
        slot = per_topic.setdefault(int(row["topic_id"]), {"delta": 0, "wrong": 0, "right": 0})
        slot["delta"] += int(row["delta"])
        if row["kind"] == "tv_wrong":
            slot["wrong"] += 1
        elif row["kind"] == "tv_right":
            slot["right"] += 1

    touched = []
    for topic_id, slot in per_topic.items():
        row = _row(cur, device_id, topic_id, now_iso, "")
        before = int(row["score"])
        score = clamp(before - slot["delta"])
        _emit(cur, device_id=device_id, topic_id=topic_id, kind="bad_question_void",
              delta=score - before, score_after=score, ref_id=ref_id,
              note=f"{note}（题目 #{int(question_id)}）" if note else f"题目 #{int(question_id)} 下架",
              now_iso=now_iso)
        _store(cur, device_id=device_id, topic_id=topic_id, score=score,
               right_streak=0, wrong_streak=0, paused_until=row["paused_until"],
               total_wrong=max(0, int(row["total_wrong"] or 0) - slot["wrong"]),
               total_right=max(0, int(row["total_right"] or 0) - slot["right"]), now_iso=now_iso)
        touched.append({"topic_id": topic_id, "score": score, "level": level_of(score),
                        "delta": score - before})
    return {"topics": touched, "events": len(rows)}


def to_iso(now_ms: int) -> str:
    return datetime.datetime.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _still_paused(paused_until, now_ms: int) -> bool:
    """True while a pause is running, so a sixth wrong answer does not extend it by another three days."""
    if not paused_until:
        return False
    if isinstance(paused_until, datetime.datetime):
        until = paused_until
    else:
        try:
            until = datetime.datetime.strptime(str(paused_until)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
    return until > datetime.datetime.fromtimestamp(now_ms / 1000)


def state(db, device_id: str, topic_id: int) -> dict | None:
    """What the parent page reads. Not used by the TV, and not by the picker yet (P0-C)."""
    row = db.query_one(
        "SELECT * FROM topic_mastery WHERE device_id = ? AND topic_id = ?", (device_id, topic_id)
    )
    if row is None:
        return None
    score = int(row["score"])
    level = level_of(score)
    return {
        "topic_id": topic_id,
        "score": score,
        "level": level,
        "level_name": LEVEL_NAMES[level],
        "weight": weight_x10(level) / 10,
        "target_qty": target_qty(level),
        "need_right": need_right(score),
        "paused_until": row["paused_until"],
        "total_wrong": int(row["total_wrong"] or 0),
        "total_right": int(row["total_right"] or 0),
    }


def _selfcheck() -> None:
    """The arithmetic a parent could check by hand. Run: python -m server.app.mastery"""
    # bands, including the seams, because +12 has to leave L0 in one step
    assert [level_of(s) for s in (0, 11, 12, 35, 36, 70, 71, 100)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert level_of(-5) == 0 and level_of(140) == 3
    assert [weight_x10(i) for i in range(4)] == [10, 20, 30, 40]
    assert [target_qty(i) for i in range(4)] == [0, 5, 7, 10]
    assert [daily_cap(i) for i in range(4)] == [0, 3, 3, 3]

    # one wrong answer must leave L0 at once, or the parent sees "牢固" right after a mistake
    assert level_of(clamp(0 + WRONG_TV)) == 1
    # three paper mistakes on one topic in a day: 20 + 8 + 8 lands in L2, which is what the day means
    assert level_of(WRONG_PAPER_FIRST + 2 * WRONG_PAPER_MORE) == 2
    # one right is not enough to undo one wrong
    assert RIGHT_TV * -1 < WRONG_TV

    # N is what the parent reads; it must never promise fewer rights than the score needs
    assert need_right(11) == 0 and need_right(12) == 1
    assert need_right(35) == 3 and need_right(36) == 3
    assert need_right(70) == 6 and need_right(71) == 6
    assert need_right(100) == 9
    for score in range(0, 101):
        n = need_right(score)
        if n:
            assert clamp(score - 10 * n) <= 11, (score, n)   # n rights do reach L0
            assert n == 1 or clamp(score - 10 * (n - 1)) > 11, (score, n)  # n-1 do not

    # a pair of right answers is worth -30 in total (-10, then -10 with the extra -10), and the streak
    # counter starts over
    assert 2 * RIGHT_TV + STREAK_BONUS == -30
    assert clamp(30 + 2 * RIGHT_TV + STREAK_BONUS) == 0
    print("mastery: ok")


if __name__ == "__main__":
    _selfcheck()
