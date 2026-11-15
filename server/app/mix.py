"""How many of the five questions come from each of the three segments (docs/WRONGBOOK.md §6).

The picker's three-layer shape does not change: current unit, the units before it, and questions from
topics the child got wrong. What changes is only the **split**, and it moves in four steps:

| weak topics n | deepest level | state | current C | previous P | review R |
|---------------|---------------|-------|-----------|------------|----------|
| 0             | -             | S0    | 5         | 0          | 0        |
| 1-2           | L1            | S1    | 4         | 0          | 1        |
| 3-4           | L2            | S2    | 3         | 0          | 2        |
| 5+            | L3            | S3    | 2         | 1 (rest)   | 2        |

Two dimensions, take the heavier: three weak topics where one is only L1 gives S2 from the count and
S1 from the depth, and the answer is S2. The reason depth beats an average is that one L3 among nine
L1s averages out to "medium", and the L3 is the actual problem.

Only two numbers are chosen (C and R); P is 5 minus them. A fifth question slot cannot be invented,
and pretending otherwise would produce a table whose rows do not add up.

**A weak topic is only counted if it can actually be served**: score >= 12, at least one question not
asked in the last seven days, and not paused. Counting a topic with no questions would promise the
child a review slot that the picker then cannot fill.

**The split is decided once a day** (`daily_state`, same row as the 3-groups/90-minutes counters). If
it were recomputed per group, one wrong answer would move R from 0 to 1 and back within an hour, and
the child would see the set of questions change shape for no visible reason. It also makes a report
reproducible. One exception, in §6.6: something that became weak *today* gets one slot even when the
snapshot says R = 0 — otherwise "record a mistake on the phone, see the next group contain it" would
not work until tomorrow.

Nothing here is ever sent to the television (§10). These numbers shape the group; the TV receives
questions and nothing else.
"""

from __future__ import annotations

from server.app import limits, mastery

S0, S1, S2, S3 = "S0", "S1", "S2", "S3"
STATES = (S0, S1, S2, S3)

#: state -> (current unit, review segment). Previous units get the rest; never a third knob.
QUOTA = {S0: (5, 0), S1: (4, 1), S2: (3, 2), S3: (2, 2)}
# The window that used to live here (`how many units back count as previous`) is gone: the band is the
# whole term now, so it no longer depends on the state (docs/RULES.md §3.2).

#: score at which a topic enters the weak list; the top of L0 plus one (§5.2).
WEAK_MIN_SCORE = mastery.LEVEL_BOUNDS[0][1] + 1
#: R stops at two of five. At three the per-subject rotation in the picker starts dropping course
#: questions, and the group turns into "the same topic again" with no explanation on screen (§6.3).
R_CAP = 2
#: how many slots something that went wrong today may take when the day's snapshot says none (§6.6).
NEWCOMER_SLOTS = 1


def scale_state(n: int) -> str:
    if n <= 0:
        return S0
    if n <= 2:
        return S1
    if n <= 4:
        return S2
    return S3


def depth_state(deepest: int) -> str:
    """L0/L1 -> S1, L2 -> S2, L3 -> S3, and no weak topic at all -> S0."""
    if deepest <= 0:
        return S0
    return (S0, S1, S2, S3)[min(deepest, 3)]


def state_for(n: int, deepest: int) -> str:
    # No weak topics means no depth to read: `assess` already guarantees deepest == 0 in that case,
    # and returning S2 from a contradictory pair (0, 2) would be a silent lie in the state table.
    if n <= 0:
        return S0
    return STATES[max(STATES.index(scale_state(n)), STATES.index(depth_state(deepest)))]


def weight_of(topic: dict, supply: int) -> float:
    """Chance weight inside the review segment: 2 / 3 / 4 by level, discounted when the bank is thin.

    The discount is `available / target`, capped at 1. Without it a topic with two questions left
    would win the draw every day and the child would meet the same two questions repeatedly, which
    damages trust more than asking fewer questions (§5.4).
    """
    level = int(topic["level"])
    target = mastery.target_qty(level)
    if target <= 0 or supply <= 0:
        return 0.0
    return (mastery.weight_x10(level) / 10.0) * min(1.0, supply / target)


def assess(db, device_id: str, now_ms: int, supply: dict[int, int]) -> dict:
    """Which topics are weak enough to be practised, and how many.

    `supply` maps topic_id -> questions that are servable right now (not asked in the last week).
    A topic with none is left out on purpose: it would take a review slot and return nothing.
    """
    rows = db.query(
        "SELECT topic_id, score, level, paused_until FROM topic_mastery"
        " WHERE device_id = ? AND score >= ?",
        (device_id, WEAK_MIN_SCORE),
    )
    weak: dict[int, dict] = {}
    for row in rows:
        topic_id = int(row["topic_id"])
        if int(supply.get(topic_id, 0)) < 1:
            continue
        if mastery._still_paused(row["paused_until"], now_ms):
            continue
        score = int(row["score"])
        level = mastery.level_of(score)          # stored level can lag a parent adjustment
        weak[topic_id] = {"score": score, "level": level, "topic_id": topic_id}
    deepest = max((t["level"] for t in weak.values()), default=0)
    return {"weak": weak, "n": len(weak), "deepest": deepest}


def snapshot(db, device_id: str, day_key: str, now_ms: int, supply: dict[int, int]) -> dict:
    """Today's split, taken on the first group of the day and then reused verbatim.

    Returned keys: state, c, r, p, n, deepest, weak, note. `weak` is recomputed on every
    call (a topic can fall out of the list mid-day); the quota is not.
    """
    row = limits.get_or_create_daily(db, device_id, day_key, now_ms)
    info = assess(db, device_id, now_ms, supply)

    if row.get("mix_state") is None:
        state = state_for(info["n"], info["deepest"])
        c, r = QUOTA[state]
        p = limits.GROUP_SIZE - c - r
        note = f"{state}：{info['n']} 个可练知识点，最深 {mastery.LEVEL_NAMES[info['deepest']]}"
        _store(db, device_id, day_key, state, c, r, info["n"], note, now_ms)
        stored = {"state": state, "c": c, "r": r, "n": info["n"], "note": note}
    else:
        stored = {
            "state": row["mix_state"],
            "c": int(row["mix_c"]),
            "r": int(row["mix_r"]),
            "n": int(row["mix_n"] or 0),
            "note": row.get("mix_note") or "",
        }

    if stored["r"] == 0 and _newcomer_today(db, device_id, day_key, now_ms):
        # One slot for something that went wrong today, so the parent's freshly recorded mistake
        # appears in the very next group rather than tomorrow morning (§6.6).
        r = min(R_CAP, NEWCOMER_SLOTS)
        c = limits.GROUP_SIZE - r
        note = f"{stored['note']}；今日新增薄弱点占 {r} 位"
        _store(db, device_id, day_key, stored["state"], c, r, info["n"], note, now_ms)
        stored.update(c=c, r=r, note=note)

    plan = dict(stored)
    plan["p"] = limits.GROUP_SIZE - plan["c"] - plan["r"]
    plan["weak"] = info["weak"]
    plan["deepest"] = info["deepest"]
    return plan


def _store(db, device_id: str, day_key: str, state: str, c: int, r: int, n: int, note: str,
           now_ms: int) -> None:
    db.execute(
        "UPDATE daily_state SET mix_state = ?, mix_c = ?, mix_r = ?, mix_n = ?, mix_note = ?,"
        " updated_at = ? WHERE device_id = ? AND day_key = ?",
        (state, c, r, n, note, limits.to_iso(now_ms), device_id, day_key),
    )


def _newcomer_today(db, device_id: str, day_key: str, now_ms: int) -> bool:
    """Did any topic become weak today? Both sources land in `topic_mastery.last_event_at`: a wrong
    answer on the TV, and a mistake the parent entered from a paper."""
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM topic_mastery"
        " WHERE device_id = ? AND score >= ? AND last_event_at >= ?",
        (device_id, WEAK_MIN_SCORE, f"{day_key} 00:00:00"),
    )
    return int(row["n"] if row else 0) > 0


def _selfcheck() -> None:
    """The table in §6.2, asserted row by row. Run: python -m server.app.mix"""
    assert [scale_state(n) for n in (0, 1, 2, 3, 4, 5, 9)] == [S0, S1, S1, S2, S2, S3, S3]
    assert [depth_state(d) for d in (0, 1, 2, 3)] == [S0, S1, S2, S3]
    # the worked example in §6.2: three topics, the deepest only L1 -> the count decides
    assert state_for(3, 1) == S2
    # and the other way round: two topics, one of them L3 -> the depth decides
    assert state_for(2, 3) == S3
    assert state_for(0, 0) == S0
    # a contradictory pair cannot invent a state: no topics means nothing is deep
    assert [state_for(0, d) for d in (1, 2, 3)] == [S0, S0, S0]

    for state in STATES:
        c, r = QUOTA[state]
        p = limits.GROUP_SIZE - c - r
        assert c + p + r == limits.GROUP_SIZE, state
        assert 0 <= p <= 1, state          # P never needs more than the one slot S3 frees
        assert 0 <= r <= R_CAP, state
        assert 2 <= c <= limits.GROUP_SIZE, state   # C never drops below 2: it is still a quiz
    assert [QUOTA[s] for s in STATES] == [(5, 0), (4, 1), (3, 2), (2, 2)]

    # S0 has to look exactly like the picker did before this feature: no review slot, and the current
    # unit so cheap that all five come from it.
    assert QUOTA[S0] == (5, 0)

    # weight: one L3 against one L1 is 4:2, not 1:1 — this is the whole point of §6.7
    l3 = {"level": 3}
    l1 = {"level": 1}
    assert weight_of(l3, mastery.target_qty(3)) == 4.0
    assert weight_of(l1, mastery.target_qty(1)) == 2.0
    # a thin bank is discounted, and a dry topic cannot be drawn at all
    assert weight_of(l3, mastery.target_qty(3) // 2) == 2.0
    assert weight_of(l3, 0) == 0.0
    assert weight_of({"level": 0}, 5) == 0.0
    print("mix: ok")


if __name__ == "__main__":
    _selfcheck()
