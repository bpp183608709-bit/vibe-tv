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

On top of that shape sits a quota (docs/WRONGBOOK.md §6): of the five slots, `c` belong to the current
unit and `r` to a topic the child got wrong; previous units take what is left. The quota comes from
`mix.snapshot`, which freezes it for the day. It changes *how many* of each, never *whether* the three
layers exist.

Two ordering constraints that look arbitrary and are not:

* **The review slots are filled first.** If the course rotation filled them, the per-subject round-robin
  would push the review question out on any day where one subject is rich, and the child would get the
  practice the score asked for "tomorrow, maybe".
* **The quota is a target, not a wall.** A current unit with no fresh questions may exceed `c`; a review
  segment with nothing left hands its slot back to the course. Five questions is the invariant; the
  split is a preference.

Nothing here is random by accident: the ordering is deterministic given the database and `now_ms`, so a
report can be reproduced and a bug can be re-run.
"""

from __future__ import annotations

import datetime
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from server.app import limits, mastery, mix


class NotEnoughQuestions(Exception):
    """The bank cannot supply a group. Better an honest error than five questions that do not exist."""


#: at most this many questions on one topic inside one group, review segment included (§6.3, C2).
PER_TOPIC = 2


@dataclass
class Group:
    """The picked questions plus the two things the caller must not put on the wire.

    `review_ids` goes into `quiz_session.review_question_ids` so tomorrow's day-cap count and the
    parent's mix metric do not have to guess which question came from which segment. `plan` is the
    day's quota, kept for tests and logs — the TV never sees either (§10).
    """

    questions: list[dict]
    review_ids: list[int] = field(default_factory=list)
    plan: dict = field(default_factory=dict)


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


def _tier(row: dict, progress: dict[int, int], window: int) -> int:
    current = progress.get(int(row["textbook_id"]), 1)
    ordinal = int(row["unit_ordinal"])
    if ordinal == current:
        return 0
    if current - window <= ordinal < current:
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


def _ordered(rows: list[dict], progress: dict[int, int], now_ms: int, rng: random.Random,
             window: int) -> list[dict]:
    tiers = {id(row): _tier(row, progress, window) for row in rows}
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


def _review_used_today(db, device_id: str, day_key: str) -> Counter:
    """How many review questions each topic has already been given today.

    Counted from the sessions themselves rather than from answers: a group that was issued and then
    abandoned still took the slot, and re-rolling the day until an easy question appears is exactly
    what the cap exists to prevent (§5.4).
    """
    rows = db.query(
        "SELECT review_question_ids FROM quiz_session WHERE device_id = ? AND day_key = ?",
        (device_id, day_key),
    )
    ids: list[int] = []
    for row in rows:
        raw = row.get("review_question_ids")
        if not raw:
            continue
        try:
            ids.extend(int(value) for value in json.loads(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return Counter()
    placeholders = ",".join("?" for _ in ids)
    found = db.query(f"SELECT id, topic_id FROM question WHERE id IN ({placeholders})", ids)
    topic_of = {int(row["id"]): int(row["topic_id"]) for row in found}
    used: Counter = Counter()
    # Counted from `ids`, not from the query result: the same question may be asked again within the
    # day (the seven-day window only starts once it has been answered), and one IN(...) would collapse
    # two picks of it into one.
    for question_id in ids:
        if question_id in topic_of:
            used[topic_of[question_id]] += 1
    return used


def _review_pick(fresh: list[dict], weak: dict[int, dict], supply: Counter, rng: random.Random,
                 limit: int, used_today: Counter) -> list[dict]:
    """Weighted draw without replacement inside the review segment (§6.7).

    Weight is the level's 2/3/4, discounted by how much of the topic's target the bank still holds.
    Drawn one at a time rather than by shuffling weights: the per-topic constraints change the
    available set after every pick, and a weighted shuffle would quietly ignore them.
    """
    pool = [row for row in fresh if int(row["topic_id"]) in weak]
    chosen: list[dict] = []
    local: Counter = Counter()

    while len(chosen) < limit and pool:
        # The weight belongs to the *topic*, so a topic with ten questions must not out-draw a topic
        # with three by ten to three. Each row carries an equal share of its topic's weight, and the
        # share is recomputed after every pick because the pool shrank.
        rows_per_topic = Counter(int(row["topic_id"]) for row in pool)
        weighted: list[tuple[dict, float]] = []
        total = 0.0
        for row in pool:
            topic_id = int(row["topic_id"])
            topic = weak[topic_id]
            if local[topic_id] >= PER_TOPIC:
                continue
            if used_today[topic_id] + local[topic_id] >= mastery.daily_cap(int(topic["level"])):
                continue
            weight = mix.weight_of(topic, int(supply.get(topic_id, 0)))
            if weight <= 0:
                continue
            weight /= max(1, rows_per_topic[topic_id])
            weighted.append((row, weight))
            total += weight
        if not weighted:
            break

        target = rng.random() * total
        running = 0.0
        picked = weighted[-1][0]          # float drift guard: the last item wins a tie
        for row, weight in weighted:
            running += weight
            if target <= running:
                picked = row
                break
        chosen.append(picked)
        pool.remove(picked)
        local[int(picked["topic_id"])] += 1

    return chosen


def pick_group(db, device_id: str, day_key: str, now_ms: int, difficulty: int) -> Group:
    """Return the chosen question rows, in the order they will be served, plus the review bookkeeping."""
    progress = ensure_progress(db, device_id, now_ms)
    rows = _candidates(db, device_id, difficulty)
    if not rows:
        raise NotEnoughQuestions("the bank has no servable questions at this difficulty")

    fresh = [row for row in rows if not _seen_recently(row, now_ms)]
    supply = Counter(int(row["topic_id"]) for row in fresh)
    plan = mix.snapshot(db, device_id, day_key, now_ms, supply)
    window = int(plan["prev_window"])

    ordered = _ordered(rows, progress, now_ms, random.Random(now_ms), window)
    by_subject: dict[int, list[dict]] = defaultdict(list)
    for row in ordered:
        by_subject[int(row["subject_id"])].append(row)

    picked: list[dict] = []
    review: list[dict] = []
    per_topic: Counter = Counter()
    tier0_cap = int(plan["c"])
    tier0_taken = 0

    def take(row: dict, from_review: bool = False) -> None:
        nonlocal tier0_taken
        picked.append(row)
        per_topic[int(row["topic_id"])] += 1
        if _tier(row, progress, window) == 0:
            tier0_taken += 1
        by_subject[int(row["subject_id"])].remove(row)
        if from_review:
            review.append(row)

    def room(row: dict) -> bool:
        if per_topic[int(row["topic_id"])] >= PER_TOPIC:
            return False
        return not (_tier(row, progress, window) == 0 and tier0_taken >= tier0_cap)

    def take_from(subject_id: int) -> bool:
        for row in by_subject[subject_id]:
            if room(row):
                take(row)
                return True
        return False

    # Step 2: the review segment takes its slots first, or the course rotation would fill them.
    if plan["r"]:
        used_today = _review_used_today(db, device_id, day_key)
        for row in _review_pick(fresh, plan["weak"], supply, random.Random(now_ms), int(plan["r"]),
                                used_today):
            take(row, from_review=True)

    subject_order = sorted(by_subject)
    # Round one: one question from each subject that has anything, so a group is never single-subject.
    for subject_id in subject_order:
        if len(picked) >= limits.GROUP_SIZE:
            break
        take_from(subject_id)

    if len({int(row["subject_id"]) for row in picked}) < 2:
        raise NotEnoughQuestions("fewer than two subjects have servable questions")

    # Fill the rest round-robin, so the strongest subject does not swallow the group.
    def fill() -> None:
        while len(picked) < limits.GROUP_SIZE:
            took_any = False
            for subject_id in subject_order:
                if len(picked) >= limits.GROUP_SIZE:
                    break
                if take_from(subject_id):
                    took_any = True
            if not took_any:
                break

    fill()

    if len(picked) < limits.GROUP_SIZE and tier0_taken >= tier0_cap:
        # The current-unit quota, the previous units and the review segment all ran dry. Give the
        # remaining slots back to the current unit rather than serving four questions (§6.4).
        tier0_cap = limits.GROUP_SIZE
        fill()

    if len(picked) < limits.MIN_GROUP_SIZE:
        raise NotEnoughQuestions(f"only {len(picked)} questions available, need {limits.MIN_GROUP_SIZE}")

    # The served order should not follow the subject order either — otherwise the child learns that the
    # third question is always English.
    random.Random(now_ms).shuffle(picked)

    review_ids = {int(row["id"]) for row in review}
    return Group(
        questions=picked,
        review_ids=[int(row["id"]) for row in picked if int(row["id"]) in review_ids],
        plan=plan,
    )


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
