"""The parent page: pairing, settings, the week, the wrong answers.

Everything here is reached from `server/web/`, which the same process serves. The page itself is
plain HTML/CSS/JS with no build step (see that directory's README), because the household has one
parent, one phone, and no npm.

Four decisions worth keeping in mind when changing this file:

**An account is the credential; a cookie carries it** (docs/ACCOUNT.md, 2026-11-14). This file used to
say the opposite — one device token per phone, no accounts — and that is why pairing here used to
mint the token. It no longer does: `auth.Accounts` owns sessions, and `account_device` owns "who may
look at which television", many-to-many so two parents can hold one TV and one parent can hold two.

**The TV is a peer, not a client.** `/v1/config` carries the phone's settings to the TV and
`/v1/heartbeat` carries the TV's counter back, so the phone can show what actually happened. Both
exist for the same reason: the server holds the one copy both ends can see. Neither is
authoritative — the TV still decides what plays (docs/PARENTAL.md, 计时).

**Every write is audited** (`config_audit`, `account_id` included). The spec asks for it, and it is
also the only way to answer "who set 30 minutes?" when the two ends disagree.

**Nothing here mints time.** A ticket is signed in `grade.py` and spent on the TV; the parent page
changes limits, never balances.

**The wrong book is where a score is allowed to be read** (docs/WRONGBOOK.md §11). The TV says nothing
about weak topics, so every number a parent acts on arrives through the routes in the second half of this
file: the weak list, one topic's ledger, the two hands (±10 and "record three mistakes"), and the page
that says which unit the class is on. `h5.mastery` reads the day's frozen quota out of `daily_state`
rather than recomputing it — calling `mix.snapshot` from a page view would decide the child's day at the
moment the parent happened to open the tab (docs/RULES.md §2.6).

The pairing alphabet and TTL live here rather than in `api.py` so there is one copy: `pair/start`
(create a code) and `h5/pair` (consume one) must agree on both, and they sit in different modules.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import secrets
from collections import deque

from server.app import coach, curriculum, limits, mastery, mix, picker, report
from server.app.auth import SESSION_COOKIE, Accounts
from server.app.grade import GradeError
from server.app.ids import new_ulid

#: Pairing alphabet (G6): no I, O, l, o, 0, 1 — the characters a parent misreads off a TV screen.
#: Case is kept because the code is half the defence against guessing, the rate limit is the other.
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ" "abcdefghijkmnpqrstuvwxyz" "23456789"
PAIR_CODE_LEN = 6
PAIR_TTL_MS = 10 * 60_000

#: Name of the cookie holding the session token, re-exported so `api.py` has one import for it.
#: (It lives in `auth.py` now, next to the sessions it stands for.)
SESSION_MAX_AGE_S = 365 * 24 * 3600

#: The three numbers the phone can change, in minutes. Names match `ParentalStore`'s preference keys
#: and the H5's JSON, so nothing has to be translated at either end.
CONFIG_KEYS = ("daily_minutes", "session_minutes", "interval_minutes")

#: Ranges copied from `ParentalStore.Limits.clamped()`. If they ever change there, change them here:
#: the phone accepts what the TV accepts, or the parent sets a value the TV silently clamps away.
CONFIG_RANGE = {
    "daily_minutes": (0, 12 * 60),      # 0 = no limit
    "session_minutes": (0, 60),         # 0 = no limit
    "interval_minutes": (10, 60),       # never 0: with no session limit nothing could follow it
}

#: What a device that has never been configured looks like. 60 minutes a day is G1.
DEFAULT_CONFIG = {"daily_minutes": 60, "session_minutes": 30, "interval_minutes": 20}

#: What "weak" means on this page. Same predicate the picker uses (`mix.assess`): enough samples to be
#: judged, and level ≤ 1. Imported rather than written down again, because a page that lists topics the
#: picker will not practise is worse than one that lists none.
WEAK_MAX_LEVEL = mix.WEAK_MAX_LEVEL
WEAK_MIN_SAMPLE = mastery.MIN_SAMPLE

#: level -> the word this page prints. The numeric form ("0-未掌握") is for the report; a chip on a
#: phone wants the short word.
LEVEL_WORDS = ("未掌握", "基础", "熟练", "拓展")

#: `mastery_event.kind` -> what the parent reads when the row has no note of its own.
KIND_LABELS = {
    "tv_wrong": "电视答错",
    "tv_right": "电视答对",
    "paper_wrong": "试卷错题",
    "streak_bonus": "连对加成",
    "parent_adjust": "家长调整",
    "bad_question_void": "题目下架退回",
}

#: How many mistakes one recording may add at once. Ten is a weekend worksheet; more would let one tap
#: max out every topic the child has.
PAPER_MAX_ITEMS = 10
#: Photos of a paper would live 30 days (D4). Nothing is photographed yet, but the column is NOT NULL,
#: so the manual path has to put something defensible in it.
PAPER_PURGE_DAYS = 30
#: The page may choose the batch id, which is what makes a double tap harmless (§9 幂等).
PAPER_ID = re.compile(r"^[A-Za-z0-9-]{6,40}$")

#: Rate limits on the one endpoint worth brute-forcing (docs/PARENTAL.md, 限速与留痕).
RATE_PER_MINUTE = 5
RATE_PER_HOUR = 30
RATE_WINDOW_S = 3600
FAIL_LIMIT = 3
FAIL_LOCK_S = 600


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _day(day_key: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(day_key)
    except (TypeError, ValueError) as exc:
        raise GradeError("bad-day-key", 400, "day_key must be YYYY-MM-DD") from exc


def _recent_days(day_key: str, count: int) -> list[str]:
    """The `count` days ending on `day_key`, oldest first. Day keys are the TV's local days, so the
    arithmetic is on dates, not on instants."""
    end = _day(day_key)
    return [(end - datetime.timedelta(days=i)).isoformat() for i in range(count - 1, -1, -1)]


class Throttle:
    """Per-source limits on pairing: five a minute, thirty an hour, and a ten-minute lock after three
    failures in a row.

    In memory, not in the database. It is a gate, not a ledger — a restart clears it, which costs an
    attacker a few dozen more guesses against 2.8×10¹⁰ possible codes. The audit table is the ledger,
    and that one is persisted.

    "Three failures voids the code" became "three failures locks the source" once it was written:
    a wrong code does not identify a device, so at that moment the server has nothing to void. Locking
    the source achieves the same thing — whoever typed three wrong codes is out for ten minutes — and
    the code itself expires on its own in ten.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._fails: dict[str, deque[float]] = {}

    def block_seconds(self, source: str, now_s: float) -> int:
        """How long this source must wait before trying again; 0 means go ahead."""
        fails = self._fails.get(source)
        if fails:
            while fails and now_s - fails[0] > FAIL_LOCK_S:
                fails.popleft()
            if len(fails) >= FAIL_LIMIT:
                return int(FAIL_LOCK_S - (now_s - fails[0])) + 1

        hits = self._hits.setdefault(source, deque())
        while hits and now_s - hits[0] > RATE_WINDOW_S:
            hits.popleft()
        in_minute = sum(1 for t in hits if now_s - t < 60)
        if in_minute >= RATE_PER_MINUTE or len(hits) >= RATE_PER_HOUR:
            return 60
        return 0

    def record(self, source: str, now_s: float, ok: bool) -> None:
        self._hits.setdefault(source, deque()).append(now_s)
        if ok:
            self._fails.pop(source, None)

    def record_failure(self, source: str, now_s: float) -> None:
        self._fails.setdefault(source, deque()).append(now_s)


class H5:
    """The parent page's routes, kept out of the request object so they can be called from a test."""

    def __init__(self, db) -> None:
        self.db = db
        self.throttle = Throttle()
        #: Accounts and their sessions. Owned here because every route below is reached through one.
        self.accounts = Accounts(db)

    # ---- pairing ----

    def pair(self, account_id: str, body: dict, now_ms: int, source: str) -> dict:
        """Trade a six-character code for a place on the account.

        The code is the one endpoint a stranger can reach, so it is the one that is rate limited and
        the one that never says *why* a code was refused beyond a category. It is also the only proof
        that the caller is standing in front of this television: the account says **who** they are,
        the code says they are **here**.

        Two accounts may hold the same television (docs/ACCOUNT.md, A2). Adding the second one is not
        a takeover — the first one's row stays, and only that phone can remove it.
        """
        now_s = now_ms / 1000
        wait = self.throttle.block_seconds(source, now_s)
        if wait:
            raise GradeError("too-many-tries", 429, f"{wait}s")

        code = (body.get("pair_code") or "").strip()
        if len(code) != PAIR_CODE_LEN or any(ch not in PAIR_ALPHABET for ch in code):
            self.throttle.record_failure(source, now_s)
            self.throttle.record(source, now_s, ok=False)
            raise GradeError("wrong-code", 403)

        row = self.db.query_one("SELECT * FROM device WHERE pair_code_hash = ?", (_hash(code),))
        if row is None:
            # Same answer as an expired code, on purpose: telling a stranger which of the two it was
            # turns "guess the code" into "confirm the code".
            self.throttle.record_failure(source, now_s)
            self.throttle.record(source, now_s, ok=False)
            raise GradeError("wrong-code", 403)
        if row.get("revoked_at"):
            raise GradeError("device-revoked", 403, "this TV was unpaired")

        expires = limits.parse_iso(row.get("pair_expires_at"))
        if expires is None or expires < datetime.datetime.fromtimestamp(now_ms / 1000):
            self.throttle.record_failure(source, now_s)
            self.throttle.record(source, now_s, ok=False)
            # The TV is told to show a new one; the code is cleared so the same stale string cannot be
            # tried again after a restart.
            self.db.execute("UPDATE device SET pair_code_hash = NULL WHERE id = ?", (row["id"],))
            raise GradeError("code-expired", 403, "the code on the TV has expired")

        self.accounts.bind(account_id, row["id"], now_ms)
        self.throttle.record(source, now_s, ok=True)
        return {
            "device": {"device_id": row["id"], "name": row.get("name")},
            "devices": self.accounts.devices_of(account_id),
        }

    def device_of(self, account_id: str, device_id: str) -> dict:
        """The television this account is asking about, or 400/403.

        Before accounts the page never had to say which TV it meant: the cookie was the TV. Now the
        id travels in the request, so "missing" and "not yours" are separate answers — the first is a
        page bug, the second is the only thing standing between two households.
        """
        if not device_id:
            raise GradeError("no-device", 400, "say which television")
        return self.accounts.require_device(account_id, device_id)

    def legacy_device(self, token: str | None) -> dict | None:
        """The television behind a cookie minted before accounts existed, if any (A5).

        `device.token_hash` is no longer written; it is read here and nowhere else, so a phone that
        paired last month can be upgraded instead of being sent back to the TV for a new code.
        """
        if not token:
            return None
        row = self.db.query_one("SELECT * FROM device WHERE token_hash = ?", (_hash(token),))
        if row is None or row.get("revoked_at"):
            return None
        return row

    def claim(self, body: dict, now_ms: int, source: str, token: str | None) -> dict:
        """Turn a pre-account phone into an account, keeping the television it already holds.

        Registration and binding in one step: the parent types an email and a password where the page
        would otherwise ask for a code they no longer have. The old device token is cleared, so the
        cookie has exactly one meaning from then on.
        """
        device = self.legacy_device(token)
        if device is None:
            raise GradeError("not-paired", 401)
        result = self.accounts.register(body, now_ms, source)
        self.accounts.bind(result["account"]["id"], device["id"], now_ms)
        self.db.execute("UPDATE device SET token_hash = NULL WHERE id = ?", (device["id"],))
        result["device"] = {"device_id": device["id"], "name": device.get("name")}
        result["devices"] = self.accounts.devices_of(result["account"]["id"])
        return result

    def unpair(self, account_id: str, device_id: str) -> dict:
        """This account stops looking at this television. The television is not retired.

        It clears the mapping, and `device.bound_at` only when nobody else holds the TV — a second
        parent unbinding must not switch off the first one's page. It does *not* touch `revoked_at`:
        unbinding is "this account is done with this TV", not "this TV is retired". Writing
        `revoked_at` here (as this function used to, found on the television 2026-11-12) turns every
        television request into 403 and leaves the settings page showing 已连接 with nobody behind it.
        """
        return self.accounts.unbind(account_id, device_id)

    def pair_status(self, device_id: str) -> dict:
        """What the TV polls while its six characters are on screen."""
        row = self.db.query_one(
            "SELECT bound_at, name, app_version FROM device WHERE id = ?", (device_id,)
        )
        if row is None:
            raise GradeError("unknown-device", 404, "pair the TV first")
        return {
            "bound": bool(row.get("bound_at")),
            "bound_at": row.get("bound_at"),
            "name": row.get("name"),
        }

    def pair_renew(self, body: dict, now_ms: int) -> dict:
        """A fresh code for a television that already has an id.

        `pair/start` registers a *new* television, so using it to replace an expired code would throw
        away the device id: today's counters, the tickets already spent, and any history the parent
        page has been reading. This route keeps the id and only reissues the six characters.

        Refused once a phone is bound. A code is a way in, and an unbound television has nothing to
        protect, but a bound one has a week of answers behind it — there is also no reason to show a
        code at that point, so the page never asks.
        """
        device_id = body.get("device_id") or ""
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        if row is None:
            raise GradeError("unknown-device", 404, "register the TV first")
        if row.get("revoked_at"):
            raise GradeError("device-revoked", 403)
        # A bound television gets a code too (2026-11-14): 再加一台手机 — the second parent joins the
        # same device id instead of rebuilding it, which is what the old "换一台手机" had to do when a
        # device could only have one phone. The real gate is the parental password that reveals the
        # button; the code only proves the person asking is in the same room.

        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LEN))
        expires = now_ms + PAIR_TTL_MS
        self.db.execute(
            "UPDATE device SET pair_code_hash = ?, pair_expires_at = ? WHERE id = ?",
            (_hash(code), limits.to_iso(expires), device_id),
        )
        return {
            "device_id": device_id,
            "pair_code": code,
            "expires_at": limits.to_iso(expires),
            "pair_ttl_seconds": PAIR_TTL_MS // 1000,
        }

    # ---- settings ----

    def config_of(self, device_id: str) -> dict:
        row = self.db.query_one(
            "SELECT config_json, version, updated_at FROM device_config WHERE device_id = ?",
            (device_id,),
        )
        if row is None:
            return {"version": 0, "config": dict(DEFAULT_CONFIG), "updated_at": None}
        try:
            stored = json.loads(row["config_json"])
        except (TypeError, ValueError):
            stored = {}
        return {
            "version": int(row["version"]),
            "config": {**DEFAULT_CONFIG, **{k: stored[k] for k in CONFIG_KEYS if k in stored}},
            "updated_at": row["updated_at"],
        }

    def get_config(self, device_id: str) -> dict:
        current = self.config_of(device_id)
        return {"version": current["version"], "config": current["config"],
                "updated_at": current["updated_at"]}

    def put_config(
        self, device_id: str, body: dict, now_ms: int, source: str = "h5", account_id: str | None = None
    ) -> dict:
        """Write the three numbers and bump the version. Last write wins — see docs/PARENTAL.md
        「设置下发：最后写的赢」. No merge, no conflict dialogue: two changes within a minute are not a
        scenario, and tracking which field is dirty would cost more than it saves."""
        incoming = body.get("config")
        if not isinstance(incoming, dict):
            raise GradeError("bad-config", 400, "config must be an object")

        clean: dict[str, int] = {}
        for key in CONFIG_KEYS:
            if key not in incoming:
                continue
            try:
                value = int(incoming[key])
            except (TypeError, ValueError) as exc:
                raise GradeError("bad-config", 400, f"{key} must be a whole number") from exc
            low, high = CONFIG_RANGE[key]
            if not low <= value <= high:
                raise GradeError("bad-config", 400, f"{key} must be {low}..{high}")
            clean[key] = value
        if not clean:
            raise GradeError("bad-config", 400, "nothing to change")

        before = self.config_of(device_id)
        merged = {**before["config"], **clean}
        version = before["version"]
        now_iso = limits.to_iso(now_ms)

        with self.db.tx() as cur:
            version += 1
            if before["version"] == 0:
                cur.execute(
                    "INSERT INTO device_config (device_id, config_json, version, updated_at)"
                    " VALUES (?, ?, ?, ?)",
                    (device_id, json.dumps(merged), version, now_iso),
                )
            else:
                cur.execute(
                    "UPDATE device_config SET config_json = ?, version = ?, updated_at = ?"
                    " WHERE device_id = ?",
                    (json.dumps(merged), version, now_iso, device_id),
                )
            # Audit inside the same transaction as the change: a settings row that changed without a
            # line saying who changed it is exactly what this table exists to prevent.
            cur.execute(
                "INSERT INTO config_audit (device_id, source, account_id, fields, old_json, new_json,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (device_id, source, account_id, json.dumps(sorted(clean)),
                 json.dumps(before["config"]), json.dumps(merged), now_iso),
            )

        return {"version": version, "config": merged}

    # ---- today and the week ----

    def _daily_rows(self, device_id: str, days: list[str]) -> dict[str, dict]:
        placeholders = ",".join("?" for _ in days)
        rows = self.db.query(
            f"SELECT * FROM daily_state WHERE device_id = ? AND day_key IN ({placeholders})",
            (device_id, *days),
        )
        return {row["day_key"]: row for row in rows}

    def _today(self, device_id: str, day_key: str, now_ms: int, config: dict) -> dict:
        row = self.db.query_one(
            "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?", (device_id, day_key)
        )
        budget_ms = int(config["daily_minutes"]) * 60_000
        # The TV's own counters are what the parent sees; the server's are what stop a fourth group.
        # Both are reported, separately, so a small disagreement is visible rather than mysterious.
        used_ms = None
        if row and row.get("used_at"):
            used_ms = int(row.get("used_ms") or 0)
        return {
            "day_key": day_key,
            "budget_ms": budget_ms,
            "unlimited": budget_ms == 0,
            "used_ms": used_ms,
            "used_at": row.get("used_at") if row else None,
            "passed": int(row["success_count"] or 0) if row else 0,
            "awarded_ms": int(row["awarded_ms"] or 0) if row else 0,
            "attempts": int(row["attempt_count"] or 0) if row else 0,
            "attempts_left": max(0, limits.MAX_ATTEMPTS_PER_DAY - int(row["attempt_count"] or 0))
            if row else limits.MAX_ATTEMPTS_PER_DAY,
            "success_left": max(0, limits.MAX_SUCCESS_PER_DAY - int(row["success_count"] or 0))
            if row else limits.MAX_SUCCESS_PER_DAY,
            "award_left_ms": max(0, limits.MAX_AWARD_MS_PER_DAY - int(row["awarded_ms"] or 0))
            if row else limits.MAX_AWARD_MS_PER_DAY,
            "cooldown_seconds": limits.is_cooldown_active(row, now_ms) if row else 0,
        }

    def summary(self, device_id: str, day_key: str, now_ms: int) -> dict:
        current = self.config_of(device_id)
        today = self._today(device_id, day_key, now_ms, current["config"])
        # The name rides along so the page has a title on its first paint. Fetching it separately would
        # mean the header is empty for as long as a second round trip.
        row = self.db.query_one("SELECT name, app_version FROM device WHERE id = ?", (device_id,))
        return {
            "today": today,
            "version": current["version"],
            "device": {"name": (row or {}).get("name"), "app_version": (row or {}).get("app_version")},
        }

    def report(self, device_id: str, day_key: str, now_ms: int) -> dict:
        """Seven rolling days rather than a calendar week. A parent opening this on Monday morning
        wants to see the weekend, not an empty column; docs/PARENTAL.md says 最近 7 天 for that reason.
        """
        days = _recent_days(day_key, 7)
        rows = self._daily_rows(device_id, days)

        series = []
        attempts = passed = awarded = 0
        for day in days:
            row = rows.get(day)
            attempts += int(row["attempt_count"] or 0) if row else 0
            passed += int(row["success_count"] or 0) if row else 0
            awarded += int(row["awarded_ms"] or 0) if row else 0
            series.append({
                "day_key": day,
                "attempts": int(row["attempt_count"] or 0) if row else 0,
                "passed": int(row["success_count"] or 0) if row else 0,
                "awarded_ms": int(row["awarded_ms"] or 0) if row else 0,
                "used_ms": int(row["used_ms"]) if row and row.get("used_at") else None,
            })

        placeholders = ",".join("?" for _ in days)
        worst = self.db.query(
            "SELECT t.id AS topic_id, t.name_zh AS topic, u.name_zh AS unit, s.name_zh AS subject,"
            "       COUNT(*) AS wrong"
            "  FROM quiz_answer a"
            "  JOIN question q ON q.id = a.question_id"
            "  JOIN topic t ON t.id = q.topic_id"
            "  JOIN unit u ON u.id = t.unit_id"
            "  JOIN textbook b ON b.id = u.textbook_id"
            "  JOIN subject s ON s.id = b.subject_id"
            f" WHERE a.device_id = ? AND a.is_correct = 0 AND a.flagged_unknown = 0"
            f"   AND a.answered_day IN ({placeholders})"
            "  GROUP BY t.id, t.name_zh, u.name_zh, s.name_zh"
            "  ORDER BY wrong DESC, t.name_zh"
            "  LIMIT 3",
            (device_id, *days),
        )

        return {
            "days": series,
            "attempts": attempts,
            "passed": passed,
            "pass_rate": round(passed / attempts, 3) if attempts else None,
            "awarded_ms": awarded,
            "worst_topics": [dict(row) for row in worst],
            "wrong_total": self.db.scalar(
                "SELECT COUNT(*) FROM quiz_answer"
                f" WHERE device_id = ? AND is_correct = 0 AND answered_day IN ({placeholders})",
                (device_id, *days),
                default=0,
            ),
        }

    # ---- the wrong answers ----

    def wrongbook(self, device_id: str, day_key: str, days: int = 7, limit: int = 60,
                  offset: int = 0, source: str = "all") -> dict:
        """What the parent reads to know what to help with.

        Two sources, one list. A mistake on the television arrives through `quiz_answer` — it has the
        question, what the child picked and the right answer; a mistake the parent recorded from a paper
        arrives through `wrong_item` — no question, often no stem, because there was no OCR. They are
        merged rather than shown as two lists: the parent's question is "what did he get wrong", not
        "which of our tables is it in". C13's "no answers" rule is about the version the *child* sees;
        hiding them from the parent would leave them looking at "虚拟语气 3 题" with no way to teach it.

        `offset` is the parent's scroll position, i.e. it indexes the merged list, so both queries fetch
        up to `offset + limit` and the slicing happens after the sort. Correct rather than clever, and
        unremarkable at one child's volume.
        """
        days = max(1, min(days, 30))
        limit = max(1, min(limit, 200))
        offset = max(0, min(offset, 5000))
        source = source if source in ("all", "tv", "paper") else "all"
        span = _recent_days(day_key, days)
        placeholders = ",".join("?" for _ in span)
        fetch = offset + limit

        tv_count = int(self.db.scalar(
            "SELECT COUNT(*) FROM quiz_answer"
            f" WHERE device_id = ? AND is_correct = 0 AND answered_day IN ({placeholders})",
            (device_id, *span), default=0,
        ))
        paper_count = int(self.db.scalar(
            "SELECT COUNT(*) FROM wrong_item"
            " WHERE device_id = ? AND source = 'paper' AND status = 'confirmed'"
            f"   AND occurred_on IN ({placeholders})",
            (device_id, *span), default=0,
        ))
        counts = {"all": tv_count + paper_count, "tv": tv_count, "paper": paper_count}
        total = counts[source]

        mastery_score = {
            int(row["topic_id"]): int(row["score"])
            for row in self.db.query(
                "SELECT topic_id, score FROM topic_mastery WHERE device_id = ?", (device_id,)
            )
        }

        rows: list[dict] = []
        if source in ("all", "tv"):
            rows = self.db.query(
                "SELECT a.question_id, a.chosen, a.answered_day, a.flagged_unknown,"
                "       q.stem, q.qtype, q.explanation_zh, q.difficulty,"
                "       t.id AS topic_id, t.name_zh AS topic, u.name_zh AS unit,"
                "       s.name_zh AS subject, s.code AS subject_code"
                "  FROM quiz_answer a"
                "  JOIN question q ON q.id = a.question_id"
                "  JOIN topic t ON t.id = q.topic_id"
                "  JOIN unit u ON u.id = t.unit_id"
                "  JOIN textbook b ON b.id = u.textbook_id"
                "  JOIN subject s ON s.id = b.subject_id"
                f" WHERE a.device_id = ? AND a.is_correct = 0 AND a.answered_day IN ({placeholders})"
                "  ORDER BY a.answered_day DESC, a.answered_at DESC"
                "  LIMIT ?",
                (device_id, *span, fetch),
            )

        papers: list[dict] = []
        if source in ("all", "paper"):
            papers = self.db.query(
                "SELECT w.id, w.occurred_on, w.created_at, w.stem_text,"
                "       t.id AS topic_id, t.name_zh AS topic, u.name_zh AS unit,"
                "       s.name_zh AS subject, s.code AS subject_code"
                "  FROM wrong_item w"
                "  JOIN topic t ON t.id = w.topic_id"
                "  JOIN unit u ON u.id = t.unit_id"
                "  JOIN textbook b ON b.id = u.textbook_id"
                "  JOIN subject s ON s.id = b.subject_id"
                " WHERE w.device_id = ? AND w.source = 'paper' AND w.status = 'confirmed'"
                f"   AND w.occurred_on IN ({placeholders})"
                "  ORDER BY w.occurred_on DESC, w.created_at DESC"
                "  LIMIT ?",
                (device_id, *span, fetch),
            )

        ids = sorted({int(row["question_id"]) for row in rows})
        options: dict[int, list[dict]] = {}
        accepted: dict[int, list[str]] = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            for row in self.db.query(
                f"SELECT question_id, label, text, is_correct FROM question_option"
                f" WHERE question_id IN ({marks}) ORDER BY label",
                ids,
            ):
                options.setdefault(int(row["question_id"]), []).append(
                    {"label": row["label"], "text": row["text"], "correct": bool(row["is_correct"])}
                )
            for row in self.db.query(
                f"SELECT question_id, text_raw FROM question_answer"
                f" WHERE question_id IN ({marks}) ORDER BY ordinal",
                ids,
            ):
                accepted.setdefault(int(row["question_id"]), []).append(row["text_raw"])

        items = []
        for row in rows:
            qid = int(row["question_id"])
            chosen = row.get("chosen") or ""
            topic_id = int(row["topic_id"])
            item = {
                "source": "tv",
                "question_id": qid,
                "day_key": row["answered_day"],
                "stem": row["stem"],
                "qtype": row["qtype"],
                "subject": row["subject"],
                "unit": row["unit"],
                "topic": row["topic"],
                "topic_id": topic_id,
                "chosen": chosen,
                "explanation": row.get("explanation_zh"),
                "flagged_unknown": bool(row.get("flagged_unknown")),
                "mastery_score": mastery_score.get(topic_id, 0),
            }
            if row["qtype"] == "choice":
                opts = options.get(qid, [])
                item["options"] = opts
                item["chosen_text"] = next(
                    (o["text"] for o in opts if o["label"] == chosen), None
                )
                item["answer_text"] = next((o["text"] for o in opts if o["correct"]), None)
                item["answer_label"] = next((o["label"] for o in opts if o["correct"]), None)
            else:
                item["chosen_text"] = chosen or None
                item["answer_text"] = " / ".join(accepted.get(qid, []))
            items.append(item)

        for row in papers:
            items.append({
                "source": "paper",
                "item_id": row["id"],
                "question_id": None,
                "day_key": row["occurred_on"],
                "stem": row.get("stem_text") or "",
                "qtype": "manual",
                "subject": row["subject"],
                "unit": row["unit"],
                "topic": row["topic"],
                "topic_id": int(row["topic_id"]),
                "chosen": None,
                "chosen_text": None,
                # Not "—": there is no right answer to show, and an em dash would read as one.
                "answer_text": None,
                "explanation": None,
                "flagged_unknown": False,
                "mastery_score": mastery_score.get(int(row["topic_id"]), 0),
            })

        # Newest day first. Within a day the television's answers stay ahead of the recorded ones,
        # because a stable sort keeps the order the two queries were appended in and only one of them
        # knows a time of day.
        items.sort(key=lambda item: item["day_key"], reverse=True)

        return {"days": days, "items": items[offset:offset + limit], "total": total,
                "offset": offset, "counts": counts, "source": source}

    # ---- the weak list, which is the only place a score is allowed to be read ----

    def _topics(self, device_id: str, topic_id: int | None = None,
                floor: bool = True) -> list[dict]:
        """Topic metadata + mastery + how much the bank holds: one topic, the weak list, or everything.

        One topic ignores the floor — the wrong list links to topics that have since been practised
        back to 0, and a 404 on a row the parent just tapped would be a bug they can see. The catalog
        (`floor=False`) is what the add sheet picks from, where the topic is often not weak yet.

        `in_bank` is counted here rather than read from `topic_mastery.ready_qty`: that column is the
        generator's bookkeeping (P1, not built yet) and is always 0, so the parent page would show
        "题库已有 0 题" next to "目标 10 题" and read it as a bug rather than a thin bank.
        """
        if topic_id is not None:
            where, params, tail = "WHERE t.id = ?", (device_id, device_id, topic_id), "ORDER BY t.id"
        elif floor:
            # A node is on the list only once it has been measured (sample_count ≥ 3) and is weak
            # (level ≤ 1). Membership comes from the `weakness` table rather than being re-derived here:
            # the page and the number then cannot disagree about what "weak" means, because one write
            # (mastery._sync_weakness) decides both. Worst first: lowest level, then lowest accuracy.
            where = ("WHERE EXISTS (SELECT 1 FROM weakness w"
                     " WHERE w.device_id = ? AND w.topic_id = t.id AND w.resolved = 0)")
            params = (device_id, device_id, device_id)
            tail = "ORDER BY COALESCE(m.level, 3), COALESCE(m.acc_x1000, 1000), t.name_zh"
        else:
            where, params = "WHERE 1 = 1", (device_id, device_id)
            tail = "ORDER BY s.id, u.ordinal, t.ordinal"
        rows = self.db.query(
            "SELECT t.id AS topic_id, t.name_zh AS topic, t.code AS node_code,"
            "       t.knowledge_type, t.summary_zh,"
            "       u.ordinal AS unit_ordinal, u.code AS unit_code, u.name_zh AS unit,"
            "       s.name_zh AS subject, s.code AS subject_code, b.id AS textbook_id,"
            "       COALESCE(m.score, 0) AS score, COALESCE(m.total_wrong, 0) AS total_wrong,"
            "       COALESCE(m.total_right, 0) AS total_right, m.paused_until,"
            "       COALESCE(m.level, 0) AS level, COALESCE(m.sample_count, 0) AS sample_count,"
            "       COALESCE(m.acc_x1000, 0) AS acc_x1000, COALESCE(m.hard_right, 0) AS hard_right,"
            "       COALESCE(m.bias, 0) AS bias, m.ability_breakdown_json,"
            "       (SELECT MAX(w.occurred_on) FROM wrong_item w"
            "         WHERE w.device_id = ? AND w.topic_id = t.id AND w.status = 'confirmed')"
            "        AS last_wrong_on,"
            "       (SELECT COUNT(*) FROM question q"
            "         WHERE q.topic_id = t.id AND q.retired_at IS NULL"
            "           AND q.verify_status IN ('sympy_ok', 'manual_ok')) AS in_bank"
            "  FROM topic t"
            "  JOIN unit u ON u.id = t.unit_id"
            "  JOIN textbook b ON b.id = u.textbook_id"
            "  JOIN subject s ON s.id = b.subject_id"
            "  LEFT JOIN topic_mastery m ON m.topic_id = t.id AND m.device_id = ?"
            f" {where} {tail}",
            params,
        )
        windows = self._windows(device_id)
        return [self._shape_topic(row, windows.get(int(row["topic_id"]))) for row in rows]

    def _windows(self, device_id: str) -> dict[int, list[dict]]:
        """The rolling window per topic, in one read.

        The list page needs it for every row's 「还要答对几道」, and a query per topic would be fifty-odd
        round trips for numbers that come from one small table.
        """
        rows = self.db.query(
            "SELECT topic_id, is_correct, weight_x10, seq FROM mastery_event"
            " WHERE device_id = ? AND counted = 1 ORDER BY topic_id, seq DESC",
            (device_id,),
        )
        out: dict[int, list[dict]] = {}
        for row in rows:
            bucket = out.setdefault(int(row["topic_id"]), [])
            if len(bucket) < mastery.MASTERY_WINDOW:
                bucket.append(row)
        return out

    def catalog(self, device_id: str) -> dict:
        """Every topic, weak or not, for the add sheet's picker.

        The weak list would be the wrong menu: a parent recording a mistake from a paper is often
        recording one on a topic that is not weak yet, and a picker that only offered weak topics would
        hide the case it exists for.
        """
        return {"topics": self._topics(device_id, floor=False)}

    def _shape_topic(self, row: dict, window: list[dict] | None = None) -> dict:
        level = int(row["level"] or 0)
        acc = int(row["acc_x1000"] or 0)
        sample = int(row["sample_count"] or 0)
        hard_right = int(row["hard_right"] or 0)
        bias = int(row["bias"] or 0)
        return {
            "topic_id": int(row["topic_id"]),
            "topic": row["topic"],
            "node_code": row.get("node_code"),
            "knowledge_type": row.get("knowledge_type"),
            "summary": row.get("summary_zh"),
            "subject": row["subject"],
            "subject_code": row["subject_code"],
            "textbook_id": int(row["textbook_id"]),
            "unit": row["unit"],
            "unit_code": row.get("unit_code"),
            "unit_ordinal": int(row["unit_ordinal"]),
            "score": int(row["score"] or 0),
            "level": level,
            "level_name": mastery.LEVEL_NAMES[level],
            "level_word": LEVEL_WORDS[level],
            # What the parent reads: a node under three answers is 待测, not 未掌握. Printing the band
            # for a node nobody has measured would accuse the child of not knowing something untested.
            "level_label": mastery.LEVEL_WORDS[level] if sample >= WEAK_MIN_SAMPLE else "待测",
            "weighted_accuracy": round(acc / 1000, 3),
            "sample_count": sample,
            "hard_right": hard_right,
            "bias": bias,
            "ability_breakdown": self._abilities(row),
            "need_sample": mastery.need_sample(sample),
            "need_right": mastery.need_right(acc, sample, hard_right, bias, window),
            "weight": mastery.weight_x10(level) / 10,
            "target_qty": mastery.target_qty(level),
            "daily_cap": mastery.daily_cap(level),
            "in_bank": int(row["in_bank"] or 0),
            "total_wrong": int(row["total_wrong"] or 0),
            "total_right": int(row["total_right"] or 0),
            "last_wrong_on": row["last_wrong_on"],
            "paused_until": row["paused_until"],
        }

    def _mix(self, device_id: str, day_key: str) -> dict | None:
        """Today's split as it was frozen, or None when no group has been served yet.

        Read, never recomputed: `mix.snapshot` writes the day's quota on the first group, and calling it
        from a page view would decide the child's day at the moment the parent happened to open the tab.
        """
        row = self.db.query_one(
            "SELECT mix_state, mix_c, mix_r, mix_n, mix_deepest, mix_note FROM daily_state"
            " WHERE device_id = ? AND day_key = ?",
            (device_id, day_key),
        )
        if not row or not row.get("mix_state"):
            return None
        c, r = int(row["mix_c"]), int(row["mix_r"])
        return {"state": row["mix_state"], "c": c, "r": r, "p": limits.GROUP_SIZE - c - r,
                "n": int(row["mix_n"] or 0), "deepest": int(row["mix_deepest"] or 0),
                "note": row.get("mix_note") or ""}

    def _week_stats(self, device_id: str, day_key: str, weak_ids: list[int]) -> dict:
        """The four numbers on the list page: how many arrived, how many left, and the average then/now.

        Computed from the ledger in one read rather than four aggregate queries. The ledger is one
        child's: hundreds of rows after a term, so replaying it is cheaper than four round trips and
        much easier to read than the SQL that would replace it.
        """
        cutoff = (_day(day_key) - datetime.timedelta(days=7)).isoformat() + " 00:00:00"
        events = self.db.query(
            "SELECT topic_id, score_after, created_at FROM mastery_event"
            " WHERE device_id = ? ORDER BY created_at, id",
            (device_id,),
        )
        first_at: dict[int, str] = {}
        before: dict[int, int] = {}
        touched: set[int] = set()
        for event in events:
            topic_id = int(event["topic_id"])
            first_at.setdefault(topic_id, event["created_at"])
            if event["created_at"] < cutoff:
                before[topic_id] = int(event["score_after"])
            else:
                touched.add(topic_id)

        weak = set(weak_ids)
        arrived = [topic_id for topic_id in weak if first_at.get(topic_id, "") >= cutoff]
        cleared = [topic_id for topic_id in touched if topic_id not in weak]
        # 平均掌握度, not 平均错误分: the number now means "how well", so higher is better everywhere.
        scores = [
            int(row["score"]) for row in self.db.query(
                "SELECT score FROM topic_mastery"
                " WHERE device_id = ? AND level <= ? AND sample_count >= ?",
                (device_id, WEAK_MAX_LEVEL, WEAK_MIN_SAMPLE),
            )
        ]
        before_scores = [before.get(topic_id, 0) for topic_id in weak]
        return {
            "days": 7,
            "arrived": len(arrived),
            "cleared": len(cleared),
            "avg_now": round(sum(scores) / len(scores)) if scores else 0,
            "avg_before": round(sum(before_scores) / len(before_scores)) if before_scores else 0,
        }

    def weak_list(self, device_id: str, day_key: str, now_ms: int) -> dict:
        """The weak list: the only page in the product where a score is allowed to be read.

        Ordering is worst first — level, then score, then the most recent mistake — because the page
        exists to answer "which one do I teach this week", and the answer is whichever row is at the top.

        `mix` is the day's quota when the child has already been served, so the parent can see what the
        page decided (3 课文 + 2 错题训练, say) instead of guessing from the child's screen, where
        nothing is said at all.
        """
        items = self._topics(device_id)
        return {
            "items": items,
            "count": len(items),
            "weak_max_level": WEAK_MAX_LEVEL,
            "mix": self._mix(device_id, day_key),
            "week": self._week_stats(device_id, day_key, [item["topic_id"] for item in items]),
            "today": day_key,
            "week_line": coach.week_line(len(items)),
        }

    def _abilities(self, row: dict) -> list[dict]:
        """The persisted per-ability drill-down. [] when it was never measured.

        Each line carries its own `level`, read from `ability_mastery` through the breakdown JSON. The
        node's level and a line's level are different numbers on purpose: a node can be 基础 while one
        of its four lines is 未掌握, and that is the sentence a parent came to the detail page for.
        """
        raw = row.get("ability_breakdown_json")
        if not raw:
            return []
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        return [{"tag": item.get("tag"), "sample": int(item.get("sample") or 0),
                 "acc": int(item.get("acc") or 0), "level": int(item.get("level") or 0)}
                for item in value if isinstance(item, dict) and item.get("tag")]

    def report_by_node(self, device_id: str, period: str = "最近 30 天", scope: str = "weak") -> dict:
        """The two-level report: chapters as rows, weak diagnostic nodes as lines (docs/RULES.md §2.6).

        The weak lines are read from `weakness`, not reassembled from `topic_mastery`: that table is
        written in the same transaction as the level, so a report cannot print a level the weak list
        disagrees with. `scope='all'` also fills `full_view` from the mastery rows — the chapter view
        a parent gets when they ask "and the rest?" — but the weak list does not change.

        The chapter is never judged. It has no questions of its own, so "第三章薄弱" would be a claim
        about nothing; it carries only the two counts (`diagnostic_count`, `weak_diagnostic_count`).
        """
        rows = self._topics(device_id, floor=False)
        open_weak = {int(row["topic_id"]): row for row in self.db.query(
            "SELECT * FROM weakness WHERE device_id = ? AND resolved = 0", (device_id,))}

        groups: dict[str, dict] = {}
        for row in rows:
            topic_id = int(row["topic_id"])
            # Read, never assembled: `unit.code` is the aggregate node id, written by the importer. A
            # missing code means the nodes were not imported from knowledge-eval/data, which is a state
            # to report, not a string to invent from the subject code and an ordinal.
            aggregate_id = row.get("unit_code")
            if not aggregate_id:
                raise GradeError("node-not-imported", 500,
                                 f"unit {row['unit_ordinal']} of {row['subject_code']} has no code")
            group = groups.get(aggregate_id)
            if group is None:
                group = groups[aggregate_id] = {
                    "student_id": device_id,
                    "aggregate_id": aggregate_id,
                    "aggregate_name": row["unit"],
                    "diagnostic_count": 0,
                    "diagnostics": [],
                }
            group["diagnostic_count"] += 1

            weakness_row = open_weak.get(topic_id)
            if weakness_row is not None:
                try:
                    evidence = json.loads(weakness_row.get("evidence_question_ids") or "[]")
                except (TypeError, ValueError):
                    evidence = []
                group["diagnostics"].append({
                    "node_id": weakness_row["diagnostic_node_id"],
                    "node_name": row["topic"],
                    "level": int(weakness_row["mastery_level"]),
                    "acc": int(weakness_row["weighted_accuracy"]),
                    "sample": int(weakness_row["sample_count"]),
                    "evidence": [str(item) for item in evidence],
                    "abilities": self._abilities(row),
                    "measured": True,
                })
                continue

            sample = int(row["sample_count"])
            group["diagnostics"].append({
                "node_id": row["node_code"] or str(topic_id),
                "node_name": row["topic"],
                "level": int(row["level"]),
                "acc": round(float(row["weighted_accuracy"]) * 1000),
                "sample": sample,
                "measured": sample >= WEAK_MIN_SAMPLE,
                "evidence": [],
                "abilities": self._abilities(row),
            })
        return report.build(list(groups.values()), period, scope=scope, source="db")

    def weak_topic(self, device_id: str, topic_id: int, day_key: str, now_ms: int) -> dict:
        """One topic: the score, the arithmetic behind it, and the sentence to say out loud.

        The flow is the answer to "凭什么 72 分" — a parent who can read `+12 电视答错` four times does
        not need the model explained. It is capped at 20 rows because the newest explain the score; the
        rest is archaeology.
        """
        rows = self._topics(device_id, topic_id)
        if not rows:
            raise GradeError("unknown-topic", 404, "no such topic")
        item = rows[0]

        events = []
        for row in self.db.query(
            "SELECT kind, delta, score_after, acc_after, sample_after, note, created_at"
            " FROM mastery_event WHERE device_id = ? AND topic_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 20",
            (device_id, topic_id),
        ):
            events.append({
                "kind": row["kind"],
                "label": row.get("note") or KIND_LABELS.get(row["kind"], row["kind"]),
                "delta": int(row["delta"]),
                "score_after": int(row["score_after"]),
                # 为什么是这个数：每次写后的窗口正确率与样本数，比 delta 更能解释一次答对/答错
                "acc_after": None if row["acc_after"] is None else int(row["acc_after"]) / 1000,
                "sample_after": row["sample_after"],
                "at": row["created_at"],
            })

        wrong = []
        for row in self.db.query(
            "SELECT w.id, w.occurred_on, w.source, w.stem_text, q.stem"
            "  FROM wrong_item w LEFT JOIN question q ON q.id = w.question_id"
            " WHERE w.device_id = ? AND w.topic_id = ? AND w.status = 'confirmed'"
            " ORDER BY w.occurred_on DESC, w.created_at DESC LIMIT 5",
            (device_id, topic_id),
        ):
            wrong.append({
                "item_id": row["id"],
                "day_key": row["occurred_on"],
                "source": row["source"],
                "stem": row.get("stem") or row.get("stem_text") or "",
            })

        used = picker.review_used_today(self.db, device_id, day_key)
        item["today_used"] = int(used.get(topic_id, 0))
        item["events"] = events
        item["wrong_items"] = wrong
        item["coach"] = coach.line(item["level"], item["need_right"], item["need_sample"])
        return item

    def adjust_topic(self, device_id: str, topic_id: int, body: dict, now_ms: int,
                     account_id: str | None) -> dict:
        """The parent's ±10, with a reason. The reason is required because this is the one number in
        the product a human can move: an unexplained jump behind a score the child is served from would
        make the ledger worthless exactly when someone finally asks about it (§9)."""
        try:
            delta = int(body.get("delta"))
        except (TypeError, ValueError) as exc:
            raise GradeError("bad-delta", 400, "delta must be 10 or -10") from exc
        if delta not in (mastery.PARENT_ADJUST, -mastery.PARENT_ADJUST):
            raise GradeError("bad-delta", 400, "delta must be 10 or -10")
        note = str(body.get("note") or "").strip()
        if len(note) < 2:
            raise GradeError("note-required", 400, "写一句为什么")
        if not self._topics(device_id, topic_id):
            raise GradeError("unknown-topic", 404, "no such topic")

        with self.db.tx() as cur:
            result = mastery.adjust(cur, device_id=device_id, topic_id=topic_id, delta=delta,
                                    note=note[:120], now_iso=mastery.to_iso(now_ms),
                                    ref_id=account_id)
        row = self._topics(device_id, topic_id)[0]
        return {"topic": row, "delta": result["delta"], "score": result["score"]}

    def add_paper(self, device_id: str, account_id: str, body: dict, now_ms: int) -> dict:
        """The parent records "he got 3 wrong on this topic" from a paper (§11 录入弹层).

        No OCR, no photograph: one topic and a count, which is the ≤20-second path. Each item is a
        `wrong_item` row of its own even though they are identical, because the score is per item
        (+20 for the day's first, +8 after) and a count would make that arithmetic invisible.

        `paper_id` may be supplied by the page. If it already exists the call answers with what that
        batch did, so a double tap on a slow phone cannot add six mistakes instead of three.
        """
        try:
            topic_id = int(body.get("topic_id"))
            count = int(body.get("count") or 1)
        except (TypeError, ValueError) as exc:
            raise GradeError("bad-entry", 400, "topic_id and count are required") from exc
        if not 1 <= count <= PAPER_MAX_ITEMS:
            raise GradeError("bad-entry", 400, f"count must be 1..{PAPER_MAX_ITEMS}")
        day_key = limits.validate_day_key(
            str(body.get("day_key") or limits.to_iso(now_ms)[:10])
        )
        paper_id = str(body.get("paper_id") or "").strip() or new_ulid()
        if not PAPER_ID.match(paper_id):
            raise GradeError("bad-entry", 400, "paper_id must be 6-40 letters or digits")

        existing = self.db.query_one("SELECT * FROM paper WHERE id = ?", (paper_id,))
        if existing is not None:
            if existing["device_id"] != device_id:
                raise GradeError("paper-taken", 409, "that id belongs to another television")
            first = self.db.query_one(
                "SELECT topic_id FROM wrong_item WHERE paper_id = ? ORDER BY id LIMIT 1", (paper_id,)
            )
            row = self._topics(device_id, int(first["topic_id"]))[0] if first else None
            return {"paper_id": paper_id, "count": int(existing["item_total"]),
                    "repeated": True, "topic": row}

        rows = self._topics(device_id, topic_id)
        if not rows:
            raise GradeError("unknown-topic", 404, "no such topic")
        topic = rows[0]
        now_iso = mastery.to_iso(now_ms)
        purge = limits.to_iso(now_ms + PAPER_PURGE_DAYS * 24 * 3600 * 1000)
        subject_id = self.db.scalar(
            "SELECT b.subject_id FROM topic t JOIN unit u ON u.id = t.unit_id"
            " JOIN textbook b ON b.id = u.textbook_id WHERE t.id = ?",
            (topic_id,),
        )

        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO paper (id, device_id, account_id, subject_id, shot_count, ocr_status,"
                " item_total, item_confirmed, captured_at, confirmed_at, purge_at)"
                " VALUES (?, ?, ?, ?, 0, 'none', ?, ?, ?, ?, ?)",
                (paper_id, device_id, account_id, subject_id, count, count, now_iso, now_iso, purge),
            )
            for _ in range(count):
                item_id = new_ulid()
                cur.execute(
                    "INSERT INTO wrong_item (id, device_id, source, question_id, paper_id, topic_id,"
                    " stem_text, occurred_on, status, confidence, created_at)"
                    " VALUES (?, ?, 'paper', NULL, ?, ?, NULL, ?, 'confirmed', NULL, ?)",
                    (item_id, device_id, paper_id, topic_id, day_key, now_iso),
                )
                # Inside the loop, after its own insert: the +20/+8 decision counts today's paper
                # mistakes for this topic, and this one has to be among them.
                mastery.record_paper_wrong(cur, device_id=device_id, topic_id=topic_id,
                                           wrong_item_id=item_id, day_key=day_key, now_iso=now_iso)

        after = self._topics(device_id, topic_id)[0]
        return {"paper_id": paper_id, "count": count, "day_key": day_key, "topic": after,
                "added": after["score"] - topic["score"]}

    def flag_question(self, device_id: str, question_id: int, body: dict, now_ms: int,
                      account_id: str | None) -> dict:
        """「这题有问题」, moved off the television by D5 (§11).

        Two effects, both needed: the question stops being served (`retired_at`), and the score it
        caused is given back. Without the second, a bad question would leave a topic marked as weak
        because of a typo in the bank, and the child would keep meeting its replacement for a week.
        """
        row = self.db.query_one("SELECT id, retired_at FROM question WHERE id = ?", (question_id,))
        if row is None:
            raise GradeError("unknown-question", 404, "no such question")
        note = str(body.get("note") or "").strip()[:120] or "家长标记题目有问题"
        now_iso = mastery.to_iso(now_ms)

        with self.db.tx() as cur:
            voided = mastery.void_question(cur, device_id=device_id, question_id=question_id,
                                           note=note, now_iso=now_iso, ref_id=account_id)
            cur.execute(
                "UPDATE question SET retired_at = ? WHERE id = ? AND retired_at IS NULL",
                (now_iso, question_id),
            )
        return {"question_id": question_id, "retired": True, "voided": voided}

    # ---- where the child is in the book ----

    def curriculum_overview(self, device_id: str, now_ms: int) -> dict:
        """The progress card: the term's first day, which week that makes today, and each book's unit.

        Before this page existed the parent had no way to say "he is in unit 5": `set_anchor` and
        `set_unit` were written for the picker and reachable from nowhere, so every child was served
        unit 1 forever (docs/RULES.md §3.0). This is that page's API.
        """
        stages = curriculum.resolve(self.db, device_id, now_ms)
        anchor = self.db.query_one("SELECT start_date FROM term_anchor WHERE device_id = ?", (device_id,))
        start = curriculum.parse_date(anchor["start_date"] if anchor else None)
        today = datetime.date.fromtimestamp(now_ms / 1000)
        week = curriculum.weeks_in(start, today) if start else None

        books: dict[int, dict] = {}
        for row in self.db.query(
            "SELECT b.id AS textbook_id, b.title_zh AS book, b.grade, b.term, b.subject_id,"
            "       s.name_zh AS subject, s.code AS subject_code,"
            "       u.ordinal, u.name_zh AS unit, u.week_start"
            "  FROM unit u"
            "  JOIN textbook b ON b.id = u.textbook_id"
            "  JOIN subject s ON s.id = b.subject_id"
            " WHERE b.is_active = 1 ORDER BY b.id, u.ordinal"
        ):
            book = books.setdefault(int(row["textbook_id"]), {
                "textbook_id": int(row["textbook_id"]),
                "book": row["book"],
                "grade": int(row["grade"]),
                "term": row["term"],
                "subject": row["subject"],
                "subject_code": row["subject_code"],
                "units": [],
            })
            book["units"].append({"ordinal": int(row["ordinal"]), "name": row["unit"],
                                  "week_start": row["week_start"]})

        for textbook_id, book in books.items():
            stage = stages.get(textbook_id)
            book.update({
                "current": stage.current if stage else 1,
                "last": stage.last if stage else max(u["ordinal"] for u in book["units"]),
                "reviewing": bool(stage and stage.reviewing),
                "source": stage.source if stage else "calendar",
            })
        return {
            "start_date": start.isoformat() if start else None,
            "week": week,
            "today": today.isoformat(),
            "books": sorted(books.values(), key=lambda book: (book["subject_code"], book["textbook_id"])),
        }

    def put_anchor(self, device_id: str, body: dict, now_ms: int) -> dict:
        """The one date a parent types per term. Everything else about the calendar is arithmetic."""
        raw = str(body.get("start_date") or "").strip()
        try:
            start = datetime.date.fromisoformat(raw)
        except ValueError as exc:
            raise GradeError("bad-date", 400, "start_date must be YYYY-MM-DD") from exc
        if not 2000 <= start.year <= 2100:
            raise GradeError("bad-date", 400, "start_date is not a school year")
        curriculum.set_anchor(self.db, device_id, start, now_ms)
        return self.curriculum_overview(device_id, now_ms)

    def put_unit(self, device_id: str, body: dict, now_ms: int) -> dict:
        """One book's unit, or `mode: 'calendar'` to hand that book back to the date."""
        try:
            textbook_id = int(body.get("textbook_id"))
        except (TypeError, ValueError) as exc:
            raise GradeError("bad-unit", 400, "textbook_id is required") from exc

        if str(body.get("mode") or "") == "calendar":
            curriculum.release_unit(self.db, device_id, textbook_id, now_ms)
            return self.curriculum_overview(device_id, now_ms)

        try:
            ordinal = int(body.get("ordinal"))
        except (TypeError, ValueError) as exc:
            raise GradeError("bad-unit", 400, "ordinal is required") from exc
        exists = self.db.query_one(
            "SELECT 1 FROM unit WHERE textbook_id = ? AND ordinal = ?", (textbook_id, ordinal)
        )
        if exists is None:
            raise GradeError("bad-unit", 404, "that book has no such unit")
        curriculum.set_unit(self.db, device_id, textbook_id, ordinal, now_ms)
        return self.curriculum_overview(device_id, now_ms)

    # ---- the TV's side of the same conversation ----

    def heartbeat(self, body: dict, now_ms: int) -> dict:
        """The TV saying "still here, and this is what today looks like".

        Watching time is counted on the TV (docs/PARENTAL.md, 计时) and the server has no way to see it;
        this is the only path by which it exists anywhere else. `used_ms` is reported, never summed:
        the TV sends its total for the day, so a missed heartbeat costs nothing and a duplicate one
        cannot inflate the day.

        The beat also carries the TV's three settings and the version it has applied, which is how
        "last write wins" survives a television that was changed with the remote. Both ends can change
        these numbers; the version decides who moved last:

        - **versions equal, numbers differ** → the TV changed them (its settings page), so the server
          takes the TV's copy, bumps the version and audits it as `tv`;
        - **the TV is behind** → the phone changed them, and the answer already carries the new config
          so the next pull applies it. Writing the TV's stale copy here would undo the parent's change
          within half an hour, which is the bug this branch exists to prevent.
        """
        device_id = body.get("device_id") or ""
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        if row is None:
            raise GradeError("unknown-device", 404, "pair the TV first")
        if row.get("revoked_at"):
            raise GradeError("device-revoked", 403)

        day_key = limits.validate_day_key(body.get("day_key") or limits.to_iso(now_ms)[:10])
        now_iso = limits.to_iso(now_ms)
        state = limits.get_or_create_daily(self.db, device_id, day_key, now_ms)

        used_ms = body.get("used_ms")
        updates = {"last_seen_at": now_iso}
        if isinstance(used_ms, int) and used_ms >= 0:
            self.db.execute(
                "UPDATE daily_state SET used_ms = ?, used_at = ?, updated_at = ?"
                " WHERE device_id = ? AND day_key = ?",
                (used_ms, now_iso, now_iso, device_id, day_key),
            )
        app_version = body.get("app_version")
        if isinstance(app_version, str) and app_version:
            updates["app_version"] = app_version[:32]
        self.db.execute(
            "UPDATE device SET last_seen_at = ?, app_version = COALESCE(?, app_version) WHERE id = ?",
            (now_iso, updates.get("app_version"), device_id),
        )

        self._take_tv_settings(device_id, body, now_ms)

        config = self.config_of(device_id)
        return {
            "server_ms": now_ms,
            "config_version": config["version"],
            "config": config["config"],
            "cooldown_seconds": limits.is_cooldown_active(state, now_ms),
            "bound": bool(row.get("bound_at")),
        }

    def _take_tv_settings(self, device_id: str, body: dict, now_ms: int) -> None:
        """Adopt the TV's numbers when the TV is the one that moved them (see `heartbeat`)."""
        posted = body.get("config")
        posted_version = body.get("config_version")
        if not isinstance(posted, dict) or not isinstance(posted_version, int):
            return

        current = self.config_of(device_id)
        if posted_version != current["version"]:
            return

        clean: dict[str, int] = {}
        for key in CONFIG_KEYS:
            value = posted.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                low, high = CONFIG_RANGE[key]
                clean[key] = max(low, min(high, value))
        changed = {k: v for k, v in clean.items() if current["config"].get(k) != v}
        if not changed:
            return
        # Straight to `put_config`: it validates, bumps the version and writes the audit line in one
        # transaction, and the TV is a legitimate source of these three numbers (the remote is the
        # normal way they are changed at home).
        self.put_config(device_id, {"config": changed}, now_ms, source="tv")

    def tv_config(self, query: dict) -> dict:
        """`GET /v1/config?device_id=…&version=n`. Answers `changed: false` when the TV's copy is
        current, so the common case — nothing has changed in five minutes — costs one small body."""
        device_id = query.get("device_id") or ""
        row = self.db.query_one("SELECT id, revoked_at FROM device WHERE id = ?", (device_id,))
        if row is None:
            raise GradeError("unknown-device", 404, "pair the TV first")
        if row.get("revoked_at"):
            raise GradeError("device-revoked", 403)

        current = self.config_of(device_id)
        try:
            known = int(query.get("version") or 0)
        except ValueError:
            known = -1
        if known == current["version"]:
            return {"changed": False, "version": current["version"]}
        return {"changed": True, "version": current["version"], "config": current["config"]}
