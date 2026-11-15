"""Choose the five questions of one group.

The rules, in the order they bite:

1. **Where the child is in the book.** `curriculum_progress.current_unit_ordinal` per textbook, moved by
   the school calendar or by the parent. Questions from that unit come first.
2. **Two units back are fair game.** A group made only of the current unit runs dry in a week — the seed
   bank has tens of questions per unit, not hundreds — and spaced review is what makes the wrong-answer
   statistics mean anything. So the pool is: current unit, then the two before it, then the rest.
3. **Do not re-ask what was answered in the last seven days**, unless there is nothing else left, in
   which case the oldest come back first. The window is per question, not per topic: a child who got
   one fraction question wrong should meet a different one, not the same one.
4. **Weight by what went wrong.** A topic with wrong answers rises in the ordering; the wrong-answer
   count is the only signal the system has that something needs practice.
5. **At least two subjects, at most two questions per topic**, five in total (C5, C2). A group of five
   algebra questions is not a quiz, it is a punishment.

Nothing here is random by accident: the ordering is deterministic given the database, so a report can
be reproduced and a bug can be re-run.
"""

from __future__ import annotations

import datetime
import random
from collections import Counter, defaultdict

from server.app import limits


class NotEnoughQuestions(Exception):
    """The bank cannot supply a group. Better an honest error than five questions that do not exist."""


def ensure_progress(db, device_id: str, now_ms: int) -> dict[int, int]:
    """textbook_id -> current unit ordinal, creating a row at unit 1 on first use."""
    rows = db.query(
        "SELECT textbook_id, current_unit_ordinal FROM curriculum_progress WHERE device_id = ?",
        (device_id,),
    )
    progress = {int(r["textbook_id"]): int(r["current_unit_ordinal"]) for r in rows}
    for textbook in db.query("SELECT id FROM textbook WHERE is_active = 1"):
        textbook_id = int(textbook["id"])
        if textbook_id in progress:
            continue
        db.execute(
            "INSERT INTO curriculum_progress (device_id, textbook_id, current_unit_ordinal, updated_by,"
            " updated_at) VALUES (?, ?, 1, 'calendar', ?)",
            (device_id, textbook_id, limits.to_iso(now_ms)),
        )
        progress[textbook_id] = 1
    return progress


def _candidates(db, device_id: str, difficulty_cap: int) -> list[dict]:
    rows = db.query(
        "SELECT q.id, q.topic_id, q.qtype, q.stem, q.difficulty, q.angle,"
        " u.ordinal AS unit_ordinal, b.id AS textbook_id, b.subject_id,"
        " COALESCE(ts.wrong_count, 0) AS wrong_count,"
        " (SELECT MAX(a.answered_at) FROM quiz_answer a"
        "   WHERE a.question_id = q.id AND a.device_id = ?) AS last_seen"
        " FROM question q"
        " JOIN unit u ON u.id = q.unit_id"
        " JOIN textbook b ON b.id = q.textbook_id"
        " LEFT JOIN topic_signal ts ON ts.topic_id = q.topic_id AND ts.device_id = ?"
        " WHERE q.retired_at IS NULL AND q.verify_status IN ('sympy_ok', 'manual_ok')"
        "   AND q.difficulty <= ?",
        (device_id, device_id, difficulty_cap),
    )
    return [dict(r) for r in rows]


def _tier(row: dict, progress: dict[int, int]) -> int:
    current = progress.get(int(row["textbook_id"]), 1)
    ordinal = int(row["unit_ordinal"])
    if ordinal == current:
        return 0
    if current - 2 <= ordinal < current:
        return 1
    return 2


def _seen_recently(row: dict, now_ms: int) -> bool:
    last_seen = limits.parse_iso(row.get("last_seen"))
    if last_seen is None:
        return False
    cutoff = datetime.datetime.fromtimestamp(now_ms / 1000) - datetime.timedelta(
        days=limits.REPEAT_WINDOW_DAYS
    )
    return last_seen >= cutoff


def _ordered(rows: list[dict], progress: dict[int, int], now_ms: int, rng: random.Random) -> list[dict]:
    tiers = {id(row): _tier(row, progress) for row in rows}
    fresh = [row for row in rows if not _seen_recently(row, now_ms)]
    stale = [row for row in rows if _seen_recently(row, now_ms)]

    def key(row: dict) -> tuple:
        return (
            tiers[id(row)],
            -int(row["wrong_count"]),
            row.get("last_seen") or "",     # never-seen sorts first
            rng.random(),
        )

    return sorted(fresh, key=key) + sorted(stale, key=key)


def pick_group(db, device_id: str, now_ms: int, difficulty: int) -> list[dict]:
    """Return the chosen question rows, in the order they will be served."""
    progress = ensure_progress(db, device_id, now_ms)
    rows = _candidates(db, device_id, difficulty)
    if not rows:
        raise NotEnoughQuestions("the bank has no servable questions at this difficulty")

    ordered = _ordered(rows, progress, now_ms, random.Random(now_ms))
    by_subject: dict[int, list[dict]] = defaultdict(list)
    for row in ordered:
        by_subject[int(row["subject_id"])].append(row)

    picked: list[dict] = []
    per_topic: Counter[int] = Counter()

    def take(row: dict) -> None:
        picked.append(row)
        per_topic[int(row["topic_id"])] += 1
        by_subject[int(row["subject_id"])].remove(row)

    def take_from(subject_id: int) -> bool:
        for row in by_subject[subject_id]:
            if per_topic[int(row["topic_id"])] < 2:
                take(row)
                return True
        return False

    subject_order = sorted(by_subject)
    # Round one: one question from each subject that has anything, so a group is never single-subject.
    for subject_id in subject_order:
        if len(picked) >= limits.GROUP_SIZE:
            break
        take_from(subject_id)

    if len({int(row["subject_id"]) for row in picked}) < 2:
        raise NotEnoughQuestions("fewer than two subjects have servable questions")

    # Fill the rest round-robin, so the strongest subject does not swallow the group.
    while len(picked) < limits.GROUP_SIZE:
        took_any = False
        for subject_id in subject_order:
            if len(picked) >= limits.GROUP_SIZE:
                break
            if take_from(subject_id):
                took_any = True
        if not took_any:
            break

    if len(picked) < limits.MIN_GROUP_SIZE:
        raise NotEnoughQuestions(f"only {len(picked)} questions available, need {limits.MIN_GROUP_SIZE}")

    # The served order should not follow the subject order either — otherwise the child learns that the
    # third question is always English.
    random.Random(now_ms).shuffle(picked)
    return picked


def payload(db, questions: list[dict]) -> list[dict]:
    """The wire shape. Answers travel with the questions (G2): the TV grades locally for instant
    feedback, and the server grades again for real. A child who unpacks the APK can read the answers;
    that was accepted, because the alternative removes the feedback that makes the quiz bearable.

    Fill-in questions need their accepted answers here too, or "instant feedback" would only work for
    multiple choice — which is half a feature and looks like a bug to the child using it.
    """
    ids = [int(row["id"]) for row in questions]
    placeholders = ",".join("?" for _ in ids)
    options: dict[int, list[dict]] = defaultdict(list)
    for row in db.query(
        f"SELECT question_id, label, text, is_correct FROM question_option"
        f" WHERE question_id IN ({placeholders}) ORDER BY label",
        ids,
    ):
        options[int(row["question_id"])].append(
            {"label": row["label"], "text": row["text"], "correct": bool(row["is_correct"])}
        )

    accepted: dict[int, list[str]] = defaultdict(list)
    for row in db.query(
        f"SELECT question_id, text_raw FROM question_answer"
        f" WHERE question_id IN ({placeholders}) ORDER BY ordinal",
        ids,
    ):
        accepted[int(row["question_id"])].append(row["text_raw"])

    out = []
    for row in questions:
        item = {
            "id": int(row["id"]),
            "topic_id": int(row["topic_id"]),
            "qtype": row["qtype"],
            "stem": row["stem"],
            "angle": row["angle"],
            "difficulty": int(row["difficulty"]),
        }
        if row["qtype"] == "choice":
            item["options"] = options[int(row["id"])]
        else:
            # `accepted`, not `answers`: the submission carries an `answers` array, and two things by
            # that name in one protocol is how a client ends up reading the wrong one.
            item["accepted"] = accepted[int(row["id"])]
        out.append(item)
    return out
