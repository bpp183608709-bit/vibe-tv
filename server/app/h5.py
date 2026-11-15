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

The pairing alphabet and TTL live here rather than in `api.py` so there is one copy: `pair/start`
(create a code) and `h5/pair` (consume one) must agree on both, and they sit in different modules.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import secrets
from collections import deque

from server.app import limits
from server.app.auth import SESSION_COOKIE, Accounts
from server.app.grade import GradeError

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

    def wrongbook(self, device_id: str, day_key: str, days: int = 7, limit: int = 60) -> dict:
        """What the parent reads to know what to help with.

        The full question, what the child picked, and what the right answer was — plus the explanation
        when the bank has one. C13's "no answers" rule is about the version the *child* sees; hiding
        them from the parent would leave them looking at "虚拟语气 3 题" with no way to teach it.
        """
        days = max(1, min(days, 30))
        limit = max(1, min(limit, 200))
        span = _recent_days(day_key, days)
        placeholders = ",".join("?" for _ in span)

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
            (device_id, *span, limit),
        )
        if not rows:
            return {"days": days, "items": []}

        ids = sorted({int(row["question_id"]) for row in rows})
        marks = ",".join("?" for _ in ids)
        options: dict[int, list[dict]] = {}
        for row in self.db.query(
            f"SELECT question_id, label, text, is_correct FROM question_option"
            f" WHERE question_id IN ({marks}) ORDER BY label",
            ids,
        ):
            options.setdefault(int(row["question_id"]), []).append(
                {"label": row["label"], "text": row["text"], "correct": bool(row["is_correct"])}
            )
        accepted: dict[int, list[str]] = {}
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
            item = {
                "question_id": qid,
                "day_key": row["answered_day"],
                "stem": row["stem"],
                "qtype": row["qtype"],
                "subject": row["subject"],
                "unit": row["unit"],
                "topic": row["topic"],
                "chosen": chosen,
                "explanation": row.get("explanation_zh"),
                "flagged_unknown": bool(row.get("flagged_unknown")),
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

        return {"days": days, "items": items}

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
