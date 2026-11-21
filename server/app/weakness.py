"""Weakness as a state machine over a per-question window (docs/weakness-calc-mapping.md).

Why this file exists beside `mastery.py`: the two answer two different questions.

* `mastery.py` — "how is this node right now", for the picker. A rolling window of the last **10
  answers**, each answer one row, repeats count again. It also writes the ledger.
* `weakness.py` — "is this node a weakness, and since when", for the parent. A rolling window of the
  last **15 distinct questions**, the newest answer per question, and a persisted state machine
  (`active` / `resolved`) whose snapshots are frozen at the moment they are taken.

They must not share a constant. Changing `mastery.MASTERY_WINDOW` to 15 would silently move the
picker's band, the daily mix, "再答对 N 次" and the pause threshold. So this module builds its own
window and leaves the picker's arithmetic untouched. The cost is two levels for one node until the
report round unifies them; that is written down in the mapping doc rather than hidden here.

No time decay, by design: the window only moves when a question is answered. A node untouched for
60 days recomputes to exactly the same numbers (test T9).
"""

from __future__ import annotations

import json
import os

from server.app.ids import new_ulid

#: Distinct questions in the window. Overridable for experiments, never at runtime in production.
DEFAULT_WINDOW = 15
#: Below this many answered questions nothing is judged — the same floor `mastery` uses.
MIN_SAMPLE = 3
#: difficulty -> weight in tenths: easy 1.0, medium 1.5, hard 2.0.
WEIGHT_X10 = {1: 10, 2: 15, 3: 20}
#: A paper mistake carries no difficulty; it counts as easy, same as `mastery.PAPER_WEIGHT_X10`.
PAPER_WEIGHT_X10 = 10

#: Bands in thousandths. A WA of exactly 0.800 is a level-3 *candidate* (then needs a hard right),
#: which is deliberately different from `mastery.level_of` (it stops at 2 on the boundary).
BAND_BASIC = 400
BAND_SKILLED = 600
BAND_EXTENDED = 800

#: 立案: measured and below 0.6.
OPEN_BELOW = BAND_SKILLED
#: 关闭: all three at once, counted **inside the window** so easy questions cannot flush the hard ones out.
CLOSE_AT = BAND_EXTENDED
CLOSE_HARD = 1
CLOSE_MEDIUM = 3
#: 主因线: a line needs this many answers of its own to be named, and at most two are named.
CAUSE_MIN_SAMPLE = 2
CAUSE_MAX = 2
#: Evidence: at most three wrong questions, never a correct one.
EVIDENCE_MAX = 3

STATE_ACTIVE = 0
STATE_RESOLVED = 1


def window_size() -> int:
    """The configured window. Env override exists so a test or an experiment can shrink it."""
    raw = os.environ.get("QUIZ_WEAKNESS_WINDOW")
    try:
        value = int(raw) if raw else DEFAULT_WINDOW
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW
    return max(MIN_SAMPLE, min(200, value))


# ---------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------

def build_window(cur, device_id: str, topic_id: int, size: int | None = None) -> list[dict]:
    """Newest-first rows, one per distinct question, capped at `size`.

    `counted = 1` drops voided questions and paper rows past the day's third — both are "not
    evidence" for different reasons, and neither belongs in the window. `is_correct IS NOT NULL`
    drops the bookkeeping rows (`parent_adjust`, `bad_question_void`) that have no answer in them.

    A paper mistake has no `question_id`; its `ref_id` (the `wrong_item.id`) stands in as the key,
    so each recorded mistake appears once by construction.
    """
    limit = window_size() if size is None else max(1, int(size))
    rows = cur.query(
        "SELECT e.question_id, e.ref_id, e.is_correct, e.difficulty, e.ability_tag, e.seq,"
        "       q.code AS question_code"
        "  FROM mastery_event e LEFT JOIN question q ON q.id = e.question_id"
        " WHERE e.device_id = ? AND e.topic_id = ?"
        "   AND e.counted = 1 AND e.is_correct IS NOT NULL"
        " ORDER BY e.seq DESC",
        (device_id, topic_id),
    )
    seen: set[tuple[str, object]] = set()
    window: list[dict] = []
    for row in rows:
        question_id = row.get("question_id")
        key = ("q", int(question_id)) if question_id is not None else ("p", str(row.get("ref_id")))
        if key in seen:
            continue
        seen.add(key)
        window.append(row)
        if len(window) >= limit:
            break
    return window


def weight_of(row: dict) -> int:
    """Difficulty weight in tenths; a paper mistake has no difficulty and counts as easy."""
    return WEIGHT_X10.get(int(row.get("difficulty") or 1), PAPER_WEIGHT_X10)


def summarise(rows: list[dict]) -> dict:
    """WA, sample and the two hard counts the close rule needs — all inside the window."""
    total = 0
    correct_weight = 0
    hard_correct = 0
    medium_correct = 0
    hard_wrong = 0
    distribution = {1: 0, 2: 0, 3: 0}
    for row in rows:
        weight = weight_of(row)
        difficulty = int(row.get("difficulty") or 1)
        total += weight
        distribution[difficulty] = distribution.get(difficulty, 0) + 1
        if int(row.get("is_correct") or 0):
            correct_weight += weight
            if difficulty == 3:
                hard_correct += 1
            elif difficulty == 2:
                medium_correct += 1
        elif difficulty == 3:
            hard_wrong += 1
    return {
        "n": len(rows),
        "weight_sum_x10": total,
        "correct_weight_x10": correct_weight,
        "wa_x1000": round(1000 * correct_weight / total) if total else 0,
        "hard_correct": hard_correct,
        "medium_correct": medium_correct,
        "hard_wrong": hard_wrong,
        "difficulty": {str(key): value for key, value in sorted(distribution.items())},
    }


def band_of(wa_x1000: int, hard_correct: int) -> int:
    """0..3 from WA alone, no sample floor. Used for the cause lines, which may hold two answers."""
    if wa_x1000 < BAND_BASIC:
        return 0
    if wa_x1000 < BAND_SKILLED:
        return 1
    if wa_x1000 < BAND_EXTENDED:
        return 2
    return 3 if hard_correct >= 1 else 2


def level_of(wa_x1000: int, hard_correct: int, n: int) -> int | None:
    """The node's level, or None when there are fewer than three answers to judge by."""
    if int(n) < MIN_SAMPLE:
        return None
    return band_of(wa_x1000, hard_correct)


# ---------------------------------------------------------------------------
# frozen explanations
# ---------------------------------------------------------------------------

def cause_lines(rows: list[dict]) -> tuple[list[dict], str]:
    """The one or two ability lines that explain the weakness, frozen at 立案.

    Sorted by level, then WA, then how many hard questions were missed — a line that is weak *and*
    fails the hard ones is a better story than one that is merely weak. Ties keep the tag's existing
    order (the query is grouped in encounter order, then `tag` breaks the last tie) so the result is
    stable across runs.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        tag = row.get("ability_tag")
        if tag:
            groups.setdefault(str(tag), []).append(row)

    candidates = []
    for tag, group in groups.items():
        if len(group) < CAUSE_MIN_SAMPLE:
            continue
        summary = summarise(group)
        candidates.append({
            "tag": tag,
            "level": band_of(summary["wa_x1000"], summary["hard_correct"]),
            "wa": summary["wa_x1000"],
            "sample": summary["n"],
            "hard_wrong": summary["hard_wrong"],
        })
    if not candidates:
        return [], "insufficient_sample"
    candidates.sort(key=lambda item: (item["level"], item["wa"], -item["hard_wrong"], item["tag"]))
    return candidates[:CAUSE_MAX], "ok"


def evidence_of(rows: list[dict], cause_tags: list[str]) -> list[str]:
    """Up to three wrong questions: cause line first, then harder, then more recent.

    Only wrong answers ever become evidence, and only questions still in the window — a correct
    answer cannot explain a weakness, and an old mistake that has been pushed out is no longer part
    of the number. A paper mistake has no question code, so it cannot be cited here.
    """
    tags = set(cause_tags)
    wrong = [row for row in rows if not int(row.get("is_correct") or 0)]
    wrong.sort(key=lambda row: (
        0 if str(row.get("ability_tag")) in tags else 1,
        -int(row.get("difficulty") or 1),
        -int(row.get("seq") or 0),
    ))
    out: list[str] = []
    for row in wrong:
        code = row.get("question_code")
        if code:
            out.append(str(code))
        if len(out) >= EVIDENCE_MAX:
            break
    return out


# ---------------------------------------------------------------------------
# the state machine
# ---------------------------------------------------------------------------

def _json_load(raw) -> list | dict | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _snapshot(summary: dict) -> dict:
    return {
        "wa": summary["wa_x1000"],
        "n": summary["n"],
        "medium_correct": summary["medium_correct"],
        "hard_correct": summary["hard_correct"],
        "difficulty": summary["difficulty"],
        "close_requirements": close_requirements(summary),
    }


def close_requirements(summary: dict) -> dict:
    """What is still missing before this row may close. Recomputed for the report, never stored as truth."""
    return {
        "wa_ok": summary["wa_x1000"] >= CLOSE_AT,
        "hard_ok": summary["hard_correct"] >= CLOSE_HARD,
        "medium_ok": summary["medium_correct"] >= CLOSE_MEDIUM,
        "need_hard": max(0, CLOSE_HARD - summary["hard_correct"]),
        "need_medium": max(0, CLOSE_MEDIUM - summary["medium_correct"]),
    }


def _place(cur, topic_id: int) -> dict | None:
    return cur.query_one(
        "SELECT t.code AS node_code, u.code AS aggregate_code, u.name_zh AS aggregate_name"
        "  FROM topic t JOIN unit u ON u.id = t.unit_id WHERE t.id = ?",
        (topic_id,),
    )


def sync(cur, *, device_id: str, topic_id: int, now_iso: str,
         new_evidence: bool = True) -> dict:
    """Move the row for (device, node) to where the current window says it should be.

    Called from `mastery._store`, the one place every piece of evidence passes through, so the
    ledger, the node level and the weakness row move in the same transaction. `new_evidence=False`
    is used when the window changed without a new answer (a question was voided) and when a write
    is bookkeeping only (parent adjust): the numbers are recomputed either way, but
    `last_practiced_at` only moves for a real answer.

    Returned for tests and callers; nothing here is read by the television.
    """
    rows = build_window(cur, device_id, topic_id)
    summary = summarise(rows)
    existing = cur.query_one(
        "SELECT * FROM weakness WHERE device_id = ? AND topic_id = ?", (device_id, topic_id)
    )
    state = None if existing is None else int(existing["resolved"])
    base = {"state": state, "changed": False, "n": summary["n"], "wa": summary["wa_x1000"],
            "level": None}

    # §7: no answers — nothing to say. An active row keeps every number it had (time freeze).
    if summary["n"] == 0:
        return {**base, "reason": "no-window", "opened": False, "reopened": False, "closed": False}
    # §7: one or two answers neither opens nor perturbs an open row.
    if summary["n"] < MIN_SAMPLE:
        return {**base, "reason": "too-few-samples",
                "opened": False, "reopened": False, "closed": False}

    level = level_of(summary["wa_x1000"], summary["hard_correct"], summary["n"])
    should_open = summary["wa_x1000"] < OPEN_BELOW
    snapshot = _snapshot(summary)
    flags = {"opened": False, "reopened": False, "closed": False}
    measured = {**base, "level": level, "close_requirements": close_requirements(summary)}

    if existing is None:
        if not should_open:
            return {**measured, "reason": "not-weak", **flags}
        _open(cur, device_id=device_id, topic_id=topic_id, rows=rows, summary=summary,
              level=level, snapshot=snapshot, now_iso=now_iso)
        return {**measured, "state": STATE_ACTIVE, "changed": True, "opened": True, **flags}

    if state == STATE_RESOLVED:
        if not should_open:
            return {**measured, "reason": "still-clear", **flags}
        _reopen(cur, existing=existing, topic_id=topic_id, rows=rows, summary=summary,
                level=level, snapshot=snapshot, now_iso=now_iso)
        return {**measured, "state": STATE_ACTIVE, "changed": True, "reopened": True, **flags}

    # active
    closes = (summary["wa_x1000"] >= CLOSE_AT
              and summary["hard_correct"] >= CLOSE_HARD
              and summary["medium_correct"] >= CLOSE_MEDIUM)
    last_practiced = now_iso if new_evidence else existing.get("last_practiced_at")
    if closes:
        cur.execute(
            "UPDATE weakness SET mastery_level = ?, weighted_accuracy = ?, sample_count = ?,"
            " current_window_snapshot = ?, resolved = ?, resolved_at = ?, closing_snapshot = ?,"
            " last_practiced_at = ?, updated_at = ? WHERE weakness_id = ?",
            (level, summary["wa_x1000"], summary["n"], _dumps(snapshot), STATE_RESOLVED,
             now_iso, _dumps(snapshot), last_practiced, now_iso, existing["weakness_id"]),
        )
        return {**measured, "state": STATE_RESOLVED, "changed": True, "closed": True, **flags}

    cur.execute(
        "UPDATE weakness SET mastery_level = ?, weighted_accuracy = ?, sample_count = ?,"
        " current_window_snapshot = ?, last_practiced_at = ?, updated_at = ?"
        " WHERE weakness_id = ?",
        (level, summary["wa_x1000"], summary["n"], _dumps(snapshot), last_practiced, now_iso,
         existing["weakness_id"]),
    )
    return {**measured, "state": STATE_ACTIVE, "changed": True, **flags}


def _open(cur, *, device_id: str, topic_id: int, rows: list[dict], summary: dict,
          level: int, snapshot: dict, now_iso: str) -> None:
    where = _place(cur, topic_id)
    if where is None or not where.get("node_code"):
        return
    causes, status = cause_lines(rows)
    evidence = evidence_of(rows, [item["tag"] for item in causes])
    cur.execute(
        "INSERT INTO weakness (weakness_id, device_id, topic_id, diagnostic_node_id, mastery_level,"
        " weighted_accuracy, sample_count, evidence_question_ids, parent_aggregate_node_id,"
        " parent_aggregate_name, resolved, opened_at, updated_at, initial_level, detection_snapshot,"
        " detection_history, current_window_snapshot, closing_snapshot, cause_tags, cause_status,"
        " resolved_at, reopen_count, reopened_at, last_practiced_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, NULL, 0, NULL, ?)",
        (new_ulid(), device_id, topic_id, where["node_code"], level, summary["wa_x1000"],
         summary["n"], _dumps(evidence), where["aggregate_code"], where["aggregate_name"],
         STATE_ACTIVE, now_iso, now_iso, level, _dumps(snapshot), _dumps(snapshot),
         _dumps(causes), status, now_iso),
    )


def _reopen(cur, *, existing: dict, topic_id: int, rows: list[dict], summary: dict,
            level: int, snapshot: dict, now_iso: str) -> None:
    where = _place(cur, topic_id)
    if where is None or not where.get("node_code"):
        return
    causes, status = cause_lines(rows)
    evidence = evidence_of(rows, [item["tag"] for item in causes])
    history = _json_load(existing.get("detection_history")) or []
    if not isinstance(history, list):
        history = []
    history.append({
        "detection_snapshot": _json_load(existing.get("detection_snapshot")),
        "initial_level": existing.get("initial_level"),
        "cause_tags": _json_load(existing.get("cause_tags")),
        "evidence": _json_load(existing.get("evidence_question_ids")),
        "opened_at": existing.get("opened_at"),
        "resolved_at": existing.get("resolved_at"),
        "closing_snapshot": _json_load(existing.get("closing_snapshot")),
        "reopened_at": now_iso,
    })
    cur.execute(
        "UPDATE weakness SET resolved = ?, reopened_at = ?, reopen_count = reopen_count + 1,"
        " mastery_level = ?, weighted_accuracy = ?, sample_count = ?, initial_level = ?,"
        " detection_snapshot = ?, detection_history = ?, evidence_question_ids = ?,"
        " diagnostic_node_id = ?, parent_aggregate_node_id = ?, parent_aggregate_name = ?,"
        " current_window_snapshot = ?, cause_tags = ?, cause_status = ?,"
        " last_practiced_at = ?, updated_at = ? WHERE weakness_id = ?",
        (STATE_ACTIVE, now_iso, level, summary["wa_x1000"], summary["n"], level,
         _dumps(snapshot), _dumps(history), _dumps(evidence), where["node_code"],
         where["aggregate_code"], where["aggregate_name"], _dumps(snapshot), _dumps(causes),
         status, now_iso, now_iso, existing["weakness_id"]),
    )


def _selfcheck() -> None:
    """Run: python -m server.app.weakness"""
    def row(correct: bool, difficulty=2, seq=1, tag="执行计算", qid=None, code=None):
        return {"is_correct": 1 if correct else 0, "difficulty": difficulty, "seq": seq,
                "ability_tag": tag, "question_id": qid, "question_code": code}

    # bands: 0.4 and 0.6 belong to the upper band, 0.8 is a level-3 candidate only with a hard right
    assert band_of(399, 0) == 0 and band_of(400, 0) == 1
    assert band_of(599, 0) == 1 and band_of(600, 0) == 2
    assert band_of(800, 0) == 2 and band_of(800, 1) == 3
    assert level_of(800, 1, 2) is None            # sample floor wins over the band

    # weights: one hard question equals two easy ones
    three = summarise([row(True, 3, 1), row(False, 3, 2), row(True, 3, 3)])
    assert three["wa_x1000"] == 667 and three["hard_correct"] == 2
    assert three["n"] == 3 and three["difficulty"]["3"] == 3

    # a paper mistake weighs easy
    paper = summarise([{"is_correct": 0, "difficulty": None, "ability_tag": None, "seq": 1}])
    assert paper["weight_sum_x10"] == 10 and paper["wa_x1000"] == 0

    # close needs all three, counted inside the window
    easy_only = summarise([row(True, 1, i) for i in range(1, 13)])
    assert easy_only["wa_x1000"] == 1000 and easy_only["hard_correct"] == 0
    assert close_requirements(easy_only)["hard_ok"] is False

    mixed = summarise([row(True, 3, i) for i in range(1, 3)]
                      + [row(True, 2, i) for i in range(3, 6)]
                      + [row(False, 1, i) for i in range(6, 8)])
    assert mixed["wa_x1000"] == 810 and mixed["hard_correct"] == 2
    assert close_requirements(mixed) == {"wa_ok": True, "hard_ok": True, "medium_ok": True,
                                         "need_hard": 0, "need_medium": 0}

    # cause lines: need two answers, weakest first, ties broken by missed hard questions
    rows = ([row(False, 3, 1, "辨析变式"), row(False, 1, 2, "辨析变式")]
            + [row(False, 2, 3, "执行计算"), row(False, 1, 4, "执行计算")]
            + [row(True, 2, 5, "理解意义")])
    causes, status = cause_lines(rows)
    assert status == "ok" and len(causes) == 2
    assert causes[0]["tag"] == "辨析变式" and causes[0]["hard_wrong"] == 1
    assert cause_lines([row(False, 2, 1, "执行计算")])[1] == "insufficient_sample"

    # evidence: wrong only, cause line first, harder first, at most three
    evidence_rows = [
        row(True, 3, 9, "执行计算", 9, "q-right"),
        row(False, 1, 8, "理解意义", 8, "q-easy-other"),
        row(False, 3, 7, "执行计算", 7, "q-hard-cause"),
        row(False, 2, 6, "执行计算", 6, "q-medium-cause"),
        row(False, 3, 5, "理解意义", 5, "q-hard-other"),
    ]
    codes = evidence_of(evidence_rows, ["执行计算"])
    assert codes == ["q-hard-cause", "q-medium-cause", "q-hard-other"], codes

    print("weakness: ok")


if __name__ == "__main__":
    _selfcheck()
