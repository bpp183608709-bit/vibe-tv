"""Which unit the child is on, and what "current unit" means once the book runs out.

`curriculum_progress.current_unit_ordinal` existed from the start but nothing ever moved it: the only
writes were the row created at unit 1 on first use. So the picker has been serving unit 1 to a child in
unit 5 (docs/RULES.md §3.0). Two sources fix that, and they answer different failure modes:

- **calendar** — the parent sets the term's first Monday once (`term_anchor.start_date`), and the unit
  follows `unit.week_start`. Nothing to remember week to week.
- **parent** — a single override when the calendar is wrong: a missed week, a holiday, a child ahead of
  or behind the class. `curriculum_progress.updated_by = 'parent'` wins until the parent switches back,
  because a value somebody typed on purpose must not be silently overwritten by arithmetic.

**Finishing the book is a state, not an error.** When `current` reaches the last unit there is no
"current unit" to ask about any more: the terminological problem is real, so `reviewing` says so and the
picker stops treating one unit as special (see `BookStage`).

Nothing here is per-question; it is one small read per group.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from server.app import limits


@dataclass(frozen=True)
class BookStage:
    """Where one book stands for one device."""

    textbook_id: int
    current: int        # 1-based ordinal the child is on
    last: int           # highest ordinal in this book (not the count: 语文 goes 1,2,7,8)
    reviewing: bool     # the book is finished, so no unit is "current" in a meaningful sense
    source: str         # 'calendar' or 'parent'

    @property
    def learned(self) -> int:
        """Everything up to and including the current unit has been taught."""
        return self.current


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


def weeks_in(start_date: datetime.date, today: datetime.date) -> int:
    """Which week of the term `today` falls in. Days before the term starts are week 1, not week 0:
    a parent who sets next Monday as day one should not see the book reset to nothing."""
    return max(1, (today - start_date).days // 7 + 1)


def unit_for_week(units: list[tuple[int, int | None]], week: int) -> int:
    """The highest unit whose `week_start` has arrived. 1 when none has."""
    current = 1
    for ordinal, week_start in units:
        if week_start is not None and int(week_start) <= week:
            current = max(current, int(ordinal))
    return current


def _parse_date(value) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve(db, device_id: str, now_ms: int) -> dict[int, BookStage]:
    """textbook_id -> BookStage for every active book. Books without a stage are served nothing."""
    progress = ensure_progress(db, device_id, now_ms)
    anchor = db.query_one("SELECT start_date FROM term_anchor WHERE device_id = ?", (device_id,))
    start = _parse_date(anchor["start_date"] if anchor else None)
    today = datetime.date.fromtimestamp(now_ms / 1000)
    week = weeks_in(start, today) if start else None

    # One read for every override, instead of one per book: three books is three round trips for a
    # value that is almost always empty.
    override = {
        int(row["textbook_id"]): int(row["current_unit_ordinal"])
        for row in db.query(
            "SELECT textbook_id, current_unit_ordinal FROM curriculum_progress"
            " WHERE device_id = ? AND updated_by = 'parent'",
            (device_id,),
        )
    }

    units: dict[int, list[tuple[int, int | None]]] = {}
    for row in db.query(
        "SELECT u.textbook_id, u.ordinal, u.week_start FROM unit u"
        " JOIN textbook b ON b.id = u.textbook_id WHERE b.is_active = 1"
        " ORDER BY u.textbook_id, u.ordinal"
    ):
        units.setdefault(int(row["textbook_id"]), []).append(
            (int(row["ordinal"]), row["week_start"])
        )

    stages: dict[int, BookStage] = {}
    for textbook_id, rows in units.items():
        last = max(ordinal for ordinal, _ in rows)
        if textbook_id in override:
            current, source = override[textbook_id], "parent"
        elif week is not None:
            current, source = unit_for_week(rows, week), "calendar"
        else:
            current, source = progress.get(textbook_id, 1), "calendar"
        current = max(1, min(last, current))
        stages[textbook_id] = BookStage(
            textbook_id=textbook_id,
            current=current,
            last=last,
            reviewing=current >= last,
            source=source,
        )
    return stages


def set_anchor(db, device_id: str, start_date: datetime.date, now_ms: int) -> None:
    """The one number a parent enters per term. Switching back to the calendar needs nothing else:
    clearing `updated_by` is how every override is released at once."""
    now = limits.to_iso(now_ms)
    if db.query_one("SELECT 1 FROM term_anchor WHERE device_id = ?", (device_id,)):
        db.execute("UPDATE term_anchor SET start_date = ?, updated_at = ? WHERE device_id = ?",
                   (start_date.isoformat(), now, device_id))
    else:
        db.execute("INSERT INTO term_anchor (device_id, start_date, updated_at) VALUES (?, ?, ?)",
                   (device_id, start_date.isoformat(), now))
    db.execute(
        "UPDATE curriculum_progress SET updated_by = 'calendar', updated_at = ? WHERE device_id = ?",
        (now, device_id),
    )


def set_unit(db, device_id: str, textbook_id: int, ordinal: int, now_ms: int) -> None:
    """The parent's correction. Every later read prefers it over the calendar until `set_anchor`."""
    now = limits.to_iso(now_ms)
    exists = db.query_one(
        "SELECT 1 FROM curriculum_progress WHERE device_id = ? AND textbook_id = ?",
        (device_id, textbook_id),
    )
    if exists:
        db.execute(
            "UPDATE curriculum_progress SET current_unit_ordinal = ?, updated_by = 'parent',"
            " updated_at = ? WHERE device_id = ? AND textbook_id = ?",
            (ordinal, now, device_id, textbook_id),
        )
    else:
        db.execute(
            "INSERT INTO curriculum_progress (device_id, textbook_id, current_unit_ordinal,"
            " updated_by, updated_at) VALUES (?, ?, ?, 'parent', ?)",
            (device_id, textbook_id, ordinal, now),
        )


def _selfcheck() -> None:
    """Run: python -m server.app.curriculum"""
    d = datetime.date(2026, 9, 1)                      # a Tuesday; week 1 runs to Sep 7
    assert weeks_in(d, d) == 1
    assert weeks_in(d, d + datetime.timedelta(days=6)) == 1
    assert weeks_in(d, d + datetime.timedelta(days=7)) == 2
    # before the term starts is week 1, not week 0 or negative
    assert weeks_in(d, d - datetime.timedelta(days=10)) == 1

    math = [(1, 1), (2, 3), (3, 5), (4, 8), (5, 10), (6, 13), (7, 16), (8, 18)]
    assert unit_for_week(math, 1) == 1
    assert unit_for_week(math, 2) == 1
    assert unit_for_week(math, 3) == 2
    assert unit_for_week(math, 12) == 5
    assert unit_for_week(math, 17) == 7
    # a term that runs long sits on the last unit; `reviewing` is what says so, not a missing ordinal
    assert unit_for_week(math, 40) == 8

    # 语文 skips ordinals, so "last" is max(ordinal) and not len(units)
    chinese = [(1, 1), (2, 3), (7, 13), (8, 15)]
    assert unit_for_week(chinese, 14) == 7
    stage = BookStage(textbook_id=2, current=8, last=max(o for o, _ in chinese), reviewing=True,
                      source="calendar")
    assert stage.reviewing and stage.learned == 8
    # a unit without a week number never becomes current by the calendar alone
    assert unit_for_week([(1, 1), (2, None)], 30) == 1
    print("curriculum: ok")


if __name__ == "__main__":
    _selfcheck()
