"""The numbers that decide how much watching time a day contains, and the counters that enforce them.

Every value here is a decision the household made, not a tuning knob. They live in one file so the
spec (docs/PARENTAL.md §做题换时长) and the code can be read side by side:

| value                    | meaning                                              | source |
|--------------------------|------------------------------------------------------|--------|
| PASS_RATIO 0.8           | 4 of 5 right passes; the award is the same either way| B2     |
| AWARD_MS 30 min          | what one passed group is worth                       | B3     |
| MAX_SUCCESS_PER_DAY 3    | passed groups per day                                | B4     |
| MAX_AWARD_MS_PER_DAY 90  | earned minutes per day, the same 3 × 30              | B4     |
| MAX_ATTEMPTS_PER_DAY 6   | started groups per day, pass or fail                 | B5     |
| COOLDOWN_MS 10 min       | wait after a failed group                            | B5     |
| SESSION_TTL_MS 10 min    | a group must be finished within this (C4)            | C4     |
| GROUP_SIZE 5, MIN 3      | questions per group; 4 or 3 only if the bank is thin | C2     |
| REPEAT_WINDOW_DAYS 7     | do not re-ask the same question within a week        | §6     |

The two caps are separate on purpose: 3 successes will always fit inside 90 minutes, but a future
change to the award (say a 20-minute group) must not silently raise the daily ceiling.

Nothing here is enforced on the TV. The TV keeps its own counters as a second line of defence, but the
server is what decides — a TV whose storage was rolled back still cannot spend a fourth group.
"""

from __future__ import annotations

import datetime
from math import ceil

PASS_RATIO = 0.8
AWARD_MS = 30 * 60_000
MAX_SUCCESS_PER_DAY = 3
MAX_AWARD_MS_PER_DAY = 90 * 60_000
MAX_ATTEMPTS_PER_DAY = 6
COOLDOWN_MS = 10 * 60_000
SESSION_TTL_MS = 10 * 60_000
GROUP_SIZE = 5
MIN_GROUP_SIZE = 3
REPEAT_WINDOW_DAYS = 7
DEFAULT_DIFFICULTY = 2
HARD_MIN_DIFFICULTY = 1

ISO = "%Y-%m-%d %H:%M:%S"


def pass_line(question_count: int) -> int:
    """How many must be right. Ceiling, not rounding: a 3-question group needs all three, because
    "2 of 3 is 66%" must not become a pass by rounding up the ratio instead of the requirement."""
    return ceil(PASS_RATIO * question_count)


def to_iso(now_ms: int) -> str:
    return datetime.datetime.fromtimestamp(now_ms / 1000).strftime(ISO)


def parse_iso(text: str | None) -> datetime.datetime | None:
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text[:19], ISO)
    except ValueError:
        return None


def is_cooldown_active(row: dict, now_ms: int) -> int:
    """Seconds left on the cooldown, or 0. The TV shows this number, so it is returned rather than a
    bare boolean: "还差 4:32" is the whole point of the lock screen."""
    until = parse_iso(row.get("cooldown_until"))
    if until is None:
        return 0
    remaining = until - datetime.datetime.fromtimestamp(now_ms / 1000)
    return max(0, int(remaining.total_seconds()))


def quota_state(row: dict) -> dict:
    """What is left today, in the shape both the API and the lock screen want."""
    success = int(row.get("success_count") or 0)
    awarded = int(row.get("awarded_ms") or 0)
    attempts = int(row.get("attempt_count") or 0)
    return {
        "success_count": success,
        "max_success": MAX_SUCCESS_PER_DAY,
        "awarded_ms": awarded,
        "max_award_ms": MAX_AWARD_MS_PER_DAY,
        "attempt_count": attempts,
        "max_attempts": MAX_ATTEMPTS_PER_DAY,
        "award_available": success < MAX_SUCCESS_PER_DAY and awarded + AWARD_MS <= MAX_AWARD_MS_PER_DAY,
        "attempts_left": max(0, MAX_ATTEMPTS_PER_DAY - attempts),
    }


def get_or_create_daily(db, device_id: str, day_key: str, now_ms: int) -> dict:
    """Read today's row, creating it on first touch. Called inside the request's transaction so two
    concurrent first-groups cannot both insert."""
    row = db.query_one(
        "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?", (device_id, day_key)
    )
    if row is not None:
        return row
    db.execute(
        "INSERT INTO daily_state (device_id, day_key, success_count, awarded_ms, attempt_count,"
        " fail_count, cooldown_until, updated_at) VALUES (?, ?, 0, 0, 0, 0, NULL, ?)",
        (device_id, day_key, to_iso(now_ms)),
    )
    return db.query_one(
        "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?", (device_id, day_key)
    )


def target_difficulty(row: dict) -> int:
    """C7: after three failures in a row, serve one level easier — and say nothing in the UI.
    The child should feel that the questions got easier, not be told that the system gave up."""
    fail_count = int(row.get("fail_count") or 0)
    if fail_count >= 3:
        return max(HARD_MIN_DIFFICULTY, DEFAULT_DIFFICULTY - 1)
    return DEFAULT_DIFFICULTY


def validate_day_key(day_key: str) -> str:
    """The TV's local day, not the server's. Shape-checked so a bad value cannot become a key that
    silently starts a fresh set of counters."""
    try:
        parsed = datetime.date.fromisoformat(day_key)
    except (TypeError, ValueError) as exc:
        raise ValueError("day_key must be YYYY-MM-DD") from exc
    if parsed.year < 2020 or parsed.year > 2100:
        raise ValueError("day_key is out of range")
    return day_key


def _selfcheck() -> None:
    assert pass_line(5) == 4
    assert pass_line(4) == 4
    assert pass_line(3) == 3
    assert pass_line(1) == 1
    assert AWARD_MS * MAX_SUCCESS_PER_DAY == MAX_AWARD_MS_PER_DAY
    row = {"success_count": 3, "awarded_ms": 90 * 60_000, "attempt_count": 5}
    assert quota_state(row)["award_available"] is False
    row = {"success_count": 2, "awarded_ms": 60 * 60_000, "attempt_count": 2}
    assert quota_state(row)["award_available"] is True
    assert is_cooldown_active({"cooldown_until": None}, 0) == 0
    assert target_difficulty({"fail_count": 3}) == 1
    assert target_difficulty({"fail_count": 2}) == 2
    print("limits: ok")


if __name__ == "__main__":
    _selfcheck()
