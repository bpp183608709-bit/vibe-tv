"""How many of the five questions come from each of the three segments (docs/WRONGBOOK.md §6).

The picker's three-layer shape does not change: current unit, the units before it, and questions from
topics the child has not mastered. What changes is only the **split**, and it moves in four steps:

| weak topics n | depth (3 = 未掌握) | state | current C | previous P | review R |
|---------------|--------------------|-------|-----------|------------|----------|
| 0             | -                  | S0    | 5         | 0          | 0        |
| 1-2           | 1 (基础)           | S1    | 4         | 0          | 1        |
| 3-4           | 1 (基础)           | S2    | 3         | 0          | 2        |
| 5+            | 3 (未掌握)         | S3    | 2         | 1 (rest)   | 2        |

Two dimensions, take the heavier: three weak topics where the weakest is only 基础 gives S2 from the
count and S1 from the depth, and the answer is S2. Depth exists because one 未掌握 among nine 基础
averages out to "medium", and that one node is the actual problem.

`depth` is not the level: the level counts mastery (0 未掌握 … 3 拓展), the depth counts weakness
(3 = 未掌握, 1 = 基础). Only a weak node has a depth at all.

Only two numbers are chosen (C and R); P is 5 minus them. A fifth question slot cannot be invented,
and pretending otherwise would produce a table whose rows do not add up.

**A topic is only weak if it has been measured**: at least MASTERY_SAMPLE (3) answers, level ≤ 1, at
least one question not asked in the last seven days, and not paused. Counting a topic with no questions
would promise the child a review slot that the picker then cannot fill.

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

#: The deepest a weak node can be (未掌握). The mix reads "how weak", not "which level".
WEAK_DEPTH_NOT_MASTERED = 3
WEAK_DEPTH_BASIC = 1
#: level ≤ this counts as weak, once the node has enough samples (mastery.is_weak).
WEAK_MAX_LEVEL = 1
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


def depth_of(level: int) -> int:
    """Level -> how weak: 未掌握 is 3, 基础 is 1, and anything above is not weak at all (0)."""
    if int(level) <= 0:
        return WEAK_DEPTH_NOT_MASTERED
    if int(level) == 1:
        return WEAK_DEPTH_BASIC
    return 0


def depth_state(deepest: int) -> str:
    """depth 1 -> S1, 2 -> S2, 3 -> S3, and no weak topic at all -> S0."""
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
    """Chance weight inside the review segment: 4 for 未掌握, 2 for 基础, discounted when the bank is
    thin, and zero for anything not weak.

    The discount is `available / target`, capped at 1. Without it a topic with two questions left
    would win the draw every day and the child would meet the same two questions repeatedly, which
    damages trust more than asking fewer questions (§5.4).
    """
    level = int(topic["level"])
    if level > WEAK_MAX_LEVEL:
        return 0.0
    target = mastery.target_qty(level)
    if target <= 0 or supply <= 0:
        return 0.0
    return (mastery.weight_x10(level) / 10.0) * min(1.0, supply / target)


def assess(db, device_id: str, now_ms: int, supply: dict[int, int]) -> dict:
    """Which topics are weak enough to be practised, and how weak the weakest is.

    `supply` maps topic_id -> questions that are servable right now (not asked in the last week).
    A topic with none is left out on purpose: it would take a review slot and return nothing.

    A node with fewer than MASTERY_SAMPLE answers is 待测 and stays out: nothing is judged on one
    sample, which is the whole point of the ≥3 rule.
    """
    rows = db.query(
        "SELECT topic_id, score, level, sample_count, paused_until FROM topic_mastery"
        " WHERE device_id = ? AND level <= ? AND sample_count >= ?",
        (device_id, WEAK_MAX_LEVEL, mastery.MIN_SAMPLE),
    )
    weak: dict[int, dict] = {}
    for row in rows:
        topic_id = int(row["topic_id"])
        if int(supply.get(topic_id, 0)) < 1:
            continue
        if mastery.still_paused(row["paused_until"], now_ms):
            continue
        level = int(row["level"])
        weak[topic_id] = {"score": int(row["score"]), "level": level, "topic_id": topic_id}
    deepest = max((depth_of(t["level"]) for t in weak.values()), default=0)
    return {"weak": weak, "n": len(weak), "deepest": deepest}


def snapshot(db, device_id: str, day_key: str, now_ms: int, supply: dict[int, int]) -> dict:
    """Today's split, taken on the first group of the day and then reused verbatim.

    Returned keys: state, c, r, p, n, deepest, weak, note. `weak` is recomputed on every call (a topic
    can fall out of the list mid-day); the quota and the depth are not — a node that recovers at noon
    must not make the evening group thinner without the parent being told.
    """
    row = limits.get_or_create_daily(db, device_id, day_key, now_ms)
    info = assess(db, device_id, now_ms, supply)

    if row.get("mix_state") is None:
        state = state_for(info["n"], info["deepest"])
        c, r = QUOTA[state]
        note = f"{state}：{info['n']} 个待练知识点，最深 {_depth_word(info['deepest'])}"
        _store(db, device_id, day_key, state, c, r, info["n"], info["deepest"], note, now_ms)
        stored = {"state": state, "c": c, "r": r, "n": info["n"],
                  "deepest": info["deepest"], "note": note}
    else:
        stored = {
            "state": row["mix_state"],
            "c": int(row["mix_c"]),
            "r": int(row["mix_r"]),
            "n": int(row["mix_n"] or 0),
            "deepest": int(row["mix_deepest"] or 0),
            "note": row.get("mix_note") or "",
        }

    if stored["r"] == 0 and _newcomer_today(db, device_id, day_key):
        # One slot for something that went wrong today, so the parent's freshly recorded mistake
        # appears in the very next group rather than tomorrow morning (§6.6).
        r = min(R_CAP, NEWCOMER_SLOTS)
        c = limits.GROUP_SIZE - r
        note = f"{stored['note']}；今日新增薄弱点占 {r} 位"
        _store(db, device_id, day_key, stored["state"], c, r, info["n"], stored["deepest"], note, now_ms)
        stored.update(c=c, r=r, note=note)

    plan = dict(stored)
    plan["p"] = limits.GROUP_SIZE - plan["c"] - plan["r"]
    plan["weak"] = info["weak"]
    return plan


def _depth_word(depth: int) -> str:
    return {0: "无", 1: mastery.LEVEL_WORDS[1], 3: mastery.LEVEL_WORDS[0]}.get(depth, "无")


def _store(db, device_id: str, day_key: str, state: str, c: int, r: int, n: int, deepest: int,
           note: str, now_ms: int) -> None:
    db.execute(
        "UPDATE daily_state SET mix_state = ?, mix_c = ?, mix_r = ?, mix_n = ?, mix_deepest = ?,"
        " mix_note = ?, updated_at = ? WHERE device_id = ? AND day_key = ?",
        (state, c, r, n, deepest, note, limits.to_iso(now_ms), device_id, day_key),
    )


def _newcomer_today(db, device_id: str, day_key: str) -> bool:
    """Did any topic become weak today? Keyed on `weak_since`, never on `last_event_at`: a right answer
    refreshes that one too, and would make an old weakness look new every afternoon."""
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM topic_mastery"
        " WHERE device_id = ? AND level <= ? AND sample_count >= ? AND weak_since >= ?",
        (device_id, WEAK_MAX_LEVEL, mastery.MIN_SAMPLE, f"{day_key} 00:00:00"),
    )
    return int(row["n"] if row else 0) > 0


def _selfcheck() -> None:
    """The table in §6.2, asserted row by row. Run: python -m server.app.mix"""
    assert [scale_state(n) for n in (0, 1, 2, 3, 4, 5, 9)] == [S0, S1, S1, S2, S2, S3, S3]
    assert [depth_state(d) for d in (0, 1, 2, 3)] == [S0, S1, S2, S3]
    # the worked example in §6.2: three topics, the weakest only 基础 -> the count decides
    assert state_for(3, 1) == S2
    # and the other way round: two topics, one of them 未掌握 -> the depth decides
    assert state_for(2, 3) == S3
    assert state_for(0, 0) == S0
    # a contradictory pair cannot invent a state: no topics means nothing is deep
    assert [state_for(0, d) for d in (1, 2, 3)] == [S0, S0, S0]

    # level counts mastery, depth counts weakness; the two must not be confused
    assert [depth_of(level) for level in (0, 1, 2, 3)] == [3, 1, 0, 0]
    assert depth_state(depth_of(0)) == S3 and depth_state(depth_of(1)) == S1

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

    # weight: one 未掌握 against one 基础 is 4:2, not 1:1 — this is the whole point of §6.7
    not_mastered = {"level": 0}
    basic = {"level": 1}
    assert weight_of(not_mastered, mastery.target_qty(0)) == 4.0
    assert weight_of(basic, mastery.target_qty(1)) == 2.0
    # a thin bank is discounted, a dry topic cannot be drawn, and a mastered one never enters R
    assert weight_of(not_mastered, mastery.target_qty(0) // 2) == 2.0
    assert weight_of(not_mastered, 0) == 0.0
    assert weight_of({"level": 2}, 10) == 0.0
    assert weight_of({"level": 3}, 10) == 0.0
    print("mix: ok")


if __name__ == "__main__":
    _selfcheck()
