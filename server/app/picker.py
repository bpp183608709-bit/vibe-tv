"""Choose the five questions of one group.

The rules, in the order they bite:

1. **Where the child is in the book.** `curriculum_progress.current_unit_ordinal` per textbook, moved by
   the school calendar or by the parent. Questions from that unit come first.
2. **Everything taught this term is fair game.** A group made only of the current unit runs dry in a
   week — the seed bank has tens of questions per unit, not hundreds. A fixed two-unit window had a
   worse problem: in week 18 it never reached what the child learned in September, so "review" meant
   "review the last fortnight". The pool is now the current unit, then every earlier unit in the book,
   and only as a last resort the units not taught yet.
3. **A finished book has no current unit.** When `current` reaches the last unit, `reviewing` is true
   and the `c` slots stop privileging one unit: they draw from the whole book. See `curriculum.py` for
   how `current` moves and why finishing is a state rather than an error.
4. **Do not re-ask what was answered in the last seven days**, unless there is nothing else left, in
   which case the oldest come back first. The window is per question, not per topic: a child who got
   one fraction question wrong should meet a different one, not the same one.
5. **Coverage before repetition outside the current unit.** In the previous-units band the order is
   "never asked, then longest unseen", because that is what makes a whole term get looked at; inside
   the current unit the order stays "worst first", which is what the week's lesson needs.
6. **At least two subjects, at most two questions per topic**, five in total (C5, C2). A group of five
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

from server.app import curriculum, limits, mastery, mix

# Re-exported: the frozen picker in acceptance calls `picker.ensure_progress`, and the name still means
# the same thing now that the calendar can move it.
from server.app.curriculum import ensure_progress  # noqa: F401


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


def hard_topics(db, device_id: str) -> set[int]:
    """Nodes that may be served a hard question: 熟练 by accuracy but never a hard one right.

    Without this set the group's difficulty cap has nothing to apply to — 3-拓展 needs a hard question
    answered right, and nothing else would ever serve one.
    """
    rows = db.query(
        "SELECT topic_id FROM topic_mastery"
        " WHERE device_id = ? AND level = 2 AND acc_x1000 > 800 AND hard_right = 0",
        (device_id,),
    )
    return {int(row["topic_id"]) for row in rows}


def _candidates(db, device_id: str, difficulty_cap: int,
                hard: set[int] | None = None) -> list[dict]:
    """Servable questions, difficulty-filtered.

    The cap applies to everything except hard questions on a node in `hard` — those are allowed through
    even at cap 2, because they are the only way that node can reach 拓展.
    """
    rows = db.query(
        "SELECT q.id, q.topic_id, q.qtype, q.stem, q.difficulty, q.angle,"
        " t.chapter_order AS unit_ordinal, b.id AS textbook_id,"
        " COALESCE(q.subject_id, b.subject_id) AS subject_id,"
        " COALESCE(ts.wrong_count, 0) AS wrong_count,"
        " (SELECT MAX(a.answered_at) FROM quiz_answer a"
        "   WHERE a.question_id = q.id AND a.device_id = ?) AS last_seen"
        " FROM question q"
        # No `unit` join: the chapter ordinal lives on the diagnostic node, so a question never has to
        # point at an aggregate. `chapter_order` is a denormalised unit.ordinal, written at import.
        " JOIN topic t ON t.id = q.topic_id"
        " JOIN textbook b ON b.id = q.textbook_id"
        " LEFT JOIN topic_signal ts ON ts.topic_id = q.topic_id AND ts.device_id = ?"
        " WHERE q.retired_at IS NULL AND q.verify_status IN ('sympy_ok', 'manual_ok')"
        "   AND q.difficulty <= ?",
        (device_id, device_id, max(int(difficulty_cap), 3)),
    )
    allowed_hard = hard or set()
    return [
        dict(row) for row in rows
        if int(row["difficulty"]) <= difficulty_cap
        or (int(row["difficulty"]) == 3 and int(row["topic_id"]) in allowed_hard)
    ]


#: a question the child has not been taught yet. Served only when the taught part of the book runs out.
UNLEARNED = 3


def _tier(row: dict, stages: dict[int, curriculum.BookStage]) -> int:
    """0 = the unit being taught (or any unit, once the book is finished), 1 = taught but earlier,
    3 = not taught yet. Missing stage means the book is not in progress: teach nothing from it."""
    stage = stages.get(int(row["textbook_id"]))
    if stage is None:
        return UNLEARNED
    ordinal = int(row["unit_ordinal"])
    if ordinal > stage.current:
        return UNLEARNED
    if stage.reviewing:
        return 0
    return 0 if ordinal == stage.current else 1


def _seen_recently(row: dict, now_ms: int) -> bool:
    last_seen = limits.parse_iso(row.get("last_seen"))
    if last_seen is None:
        return False
    cutoff = datetime.datetime.fromtimestamp(now_ms / 1000) - datetime.timedelta(
        days=limits.REPEAT_WINDOW_DAYS
    )
    return last_seen >= cutoff


def _ordered(rows: list[dict], stages: dict[int, curriculum.BookStage], now_ms: int,
             rng: random.Random) -> list[dict]:
    """Sort into the order questions will be taken, then append the un-taught ones as a last resort.

    Two orders live here on purpose. Inside the unit being taught, `wrong_count` leads: this week's
    lesson is what the child is being graded on and what the parent is watching. Everywhere else
    (`tier` 1, and every unit of a finished book) coverage leads — never asked first, then longest
    unseen — because a term is only actually reviewed if the September units come back too.

    Exactly one `rng.random()` per row, in both branches: the zero-regression group depends on the
    generator being consumed in the same order as before (docs/RULES.md §5).
    """
    tiers = {id(row): _tier(row, stages) for row in rows}
    fresh = [row for row in rows if not _seen_recently(row, now_ms)]
    stale = [row for row in rows if _seen_recently(row, now_ms)]

    def key(row: dict) -> tuple:
        tier = tiers[id(row)]
        stage = stages.get(int(row["textbook_id"]))
        last_seen = row.get("last_seen") or ""     # never asked sorts first
        wrong = -int(row["wrong_count"])
        if tier == 1 or (tier == 0 and stage is not None and stage.reviewing):
            return (tier, last_seen, wrong, rng.random())
        return (tier, wrong, last_seen, rng.random())

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


def review_used_today(db, device_id: str, day_key: str) -> Counter:
    """How many review questions each topic has already been given today (public, for the H5).

    The parent page shows "今天已出 2 题（上限 3）" next to a topic, and that number has to be the one
    the picker will act on tomorrow morning — the same count, from the same place, not a second
    approximation that drifts.
    """
    return _review_used_today(db, device_id, day_key)


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
    stages = curriculum.resolve(db, device_id, now_ms)
    rows = _candidates(db, device_id, difficulty, hard_topics(db, device_id) if difficulty < 3 else None)
    if not rows:
        raise NotEnoughQuestions("the bank has no servable questions at this difficulty")

    fresh = [row for row in rows if not _seen_recently(row, now_ms)]
    supply = Counter(int(row["topic_id"]) for row in fresh)
    plan = mix.snapshot(db, device_id, day_key, now_ms, supply)

    ordered = _ordered(rows, stages, now_ms, random.Random(now_ms))
    tiers = {id(row): _tier(row, stages) for row in rows}
    by_subject: dict[int, list[dict]] = defaultdict(list)
    for row in ordered:
        by_subject[int(row["subject_id"])].append(row)

    picked: list[dict] = []
    review: list[dict] = []
    per_topic: Counter = Counter()
    # A finished book has no unit to privilege, so the c slots draw from the whole book. Left at `c`,
    # the cap would spend itself on one unit and then hand the rest back through the "supply ran dry"
    # path below — an accident dressed as a rule (§3.3).
    reviewing = bool(stages) and all(stage.reviewing for stage in stages.values())
    plan["stage"] = "reviewing" if reviewing else "learning"
    tier0_cap = limits.GROUP_SIZE - int(plan["r"]) if reviewing else int(plan["c"])
    tier0_taken = 0

    def take(row: dict, from_review: bool = False) -> None:
        nonlocal tier0_taken
        picked.append(row)
        per_topic[int(row["topic_id"])] += 1
        if tiers[id(row)] == 0:
            tier0_taken += 1
        by_subject[int(row["subject_id"])].remove(row)
        if from_review:
            review.append(row)

    def room(row: dict) -> bool:
        if per_topic[int(row["topic_id"])] >= PER_TOPIC:
            return False
        return not (tiers[id(row)] == 0 and tier0_taken >= tier0_cap)

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
