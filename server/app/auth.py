"""Accounts: the identity behind the parent page (docs/ACCOUNT.md).

Three things live here and nowhere else:

1. **Passwords** — scrypt from the standard library, parameters from OWASP's second tier
   (N=2^15, r=8, p=3: about 270 ms and 32 MiB per hash). The parameters travel inside the stored
   string, so raising them later re-hashes new passwords and still verifies old ones.
2. **Sessions** — a 32-byte token in an HttpOnly cookie, only its SHA-256 in the database. Thirty
   days, pushed out on use, never past ninety from creation.
3. **The four gates in front of signup** — per-source rate limits, a global ceiling, a signed-in-time
   challenge (a registration must be at least 1.5 s old), and a honeypot field. No captcha service,
   no third-party anything: this process has to keep running with `python -m server.app.api` and
   nothing else.

**Email is a login name, not proof of ownership.** There is no mail gateway, so nothing verifies the
address. Consequences are written down in docs/ACCOUNT.md §3 A1 — the important one is that
"forgot password" cannot be solved by mail, so this release does not solve it at all.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import os
import re
import secrets
from collections import deque

from server.app import limits
from server.app.grade import GradeError
from server.app.ids import new_ulid

#: Name of the cookie holding the session token. The same string the device token used to travel in:
#: one cookie, one meaning per release, and the H5 clears it when it upgrades (A5).
SESSION_COOKIE = "aq"

#: Sliding session: thirty days, extended on each use, never past ninety from the day it was made.
SESSION_DAYS = 30
SESSION_MAX_DAYS = 90

#: scrypt parameters (OWASP Password Storage Cheat Sheet, the N=2^15 / p=3 tier).
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 3
SCRYPT_MAXMEM = 96 * 1024 * 1024

#: Password rules. Eight is the floor the sheet and NIST both allow; 128 caps the work an attacker
#: can make this process do with one request.
PASSWORD_MIN = 8
PASSWORD_MAX = 128

#: Registration gates (docs/ACCOUNT.md §6). Per source: five an hour, twenty a day. Globally: sixty
#: an hour, which no household reaches and a script sweeping the internet does in seconds.
SIGNUP_PER_HOUR = 5
SIGNUP_PER_DAY = 20
SIGNUP_GLOBAL_PER_HOUR = 60

#: The security question is the weakest credential in the file: it is chosen by the parent, often
#: guessable by anyone who knows the family, and it resets the password outright. So it is throttled
#: harder than a login — five wrong answers shut the door for fifteen minutes, in the database, and
#: the answer is hashed with the same scrypt as a password rather than kept in clear.
QUESTION_MIN = 3
QUESTION_MAX = 120
ANSWER_MIN = 2
ANSWER_MAX = 100
RECOVER_FAIL_LIMIT = 5
RECOVER_LOCK_S = 900
RECOVER_PER_15MIN = 20

#: Sign-in gates. Per source fifteen minutes; per account, eight failures lock it for fifteen minutes
#: and that counter is in the database, because a restart must not hand out a fresh budget.
LOGIN_PER_15MIN = 20
LOGIN_FAIL_LIMIT = 8
LOGIN_LOCK_S = 900

#: A registration that arrives faster than this was not typed by a person reading a form.
CHALLENGE_MIN_AGE_MS = 1_500
CHALLENGE_TTL_MS = 10 * 60_000

#: The passwords a script tries first. Not a dictionary — a dictionary lives in a file and this
#: process ships without one. Eighteen entries cost nothing and stop the wave that fills a table.
COMMON_PASSWORDS = frozenset({
    "12345678", "123456789", "1234567890", "password", "password1", "password123",
    "qwertyui", "qwerty123", "11111111", "00000000", "88888888", "abc12345",
    "iloveyou", "letmein1", "welcome1", "admin123", "p@ssw0rd", "a1234567",
})

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    """`scrypt$N$r$p$<salt>$<hash>`. Self-describing so the parameters can move without a migration."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time against the derived hash; a malformed stored value is a failed login, not a 500."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), maxmem=SCRYPT_MAXMEM, dklen=len(expected),
        )
    except (ValueError, AttributeError, MemoryError):
        return False
    return hmac.compare_digest(digest, expected)


def password_problem(password: str, email: str) -> str | None:
    """Why this password is not allowed, or None. The wording goes to the phone, so it is a code."""
    if not isinstance(password, str):
        return "password-missing"
    if len(password) < PASSWORD_MIN:
        return "password-short"
    if len(password.encode("utf-8")) > PASSWORD_MAX:
        return "password-long"
    if password.strip().isdigit():
        # Eight digits is still eight digits: on a phone this is what "12345678" looks like, and it
        # is the single most common thing a hurried parent types.
        return "password-weak"
    if password.strip().lower() in COMMON_PASSWORDS:
        return "password-common"
    local = email.split("@", 1)[0].lower()
    if local and password.strip().lower() == local:
        return "password-is-email"
    return None


# ---------------------------------------------------------------- email

def normalise_email(raw: object) -> tuple[str, str]:
    """(as typed, lookup key). The key is lower-cased and trimmed, which is all the normalising a
    login name needs; the @-local part keeps its case because some servers do care."""
    email = (raw or "").strip() if isinstance(raw, str) else ""
    if len(email) > 160 or not _EMAIL_RE.match(email):
        raise GradeError("email-bad", 400, "that does not look like an email address")
    return email, email.lower()


def normalise_question(raw: object) -> str:
    """The question is shown back to the phone word for word, so it is kept as typed, only trimmed."""
    text = (raw or "").strip() if isinstance(raw, str) else ""
    if len(text) < QUESTION_MIN:
        raise GradeError("question-short", 400, "write a question only you can answer")
    if len(text) > QUESTION_MAX:
        raise GradeError("question-long", 400, "that question is too long")
    return text


def normalise_answer(raw: object) -> str:
    """Case and spacing are not what a parent remembers, so neither counts: ` 红 色 ` is `红色`."""
    text = " ".join(((raw or "") if isinstance(raw, str) else "").split()).casefold()
    if len(text) < ANSWER_MIN:
        raise GradeError("answer-short", 400, "the answer is too short")
    if len(text.encode("utf-8")) > ANSWER_MAX:
        raise GradeError("answer-long", 400, "that answer is too long")
    return text


# ---------------------------------------------------------------- rate windows

class Windows:
    """A generic sliding-window counter: `hit(key, now, limit, window)` says yes or no.

    In memory, like `h5.Throttle`, and for the same reason: it is a gate, not a ledger. A restart
    clears it; the account lock that matters across restarts is `account.fail_count`, in the database.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def used(self, key: str, now_s: float, window_s: int) -> int:
        hits = self._hits.setdefault(key, deque())
        while hits and now_s - hits[0] > window_s:
            hits.popleft()
        return len(hits)

    def hit(self, key: str, now_s: float, limit: int, window_s: int) -> bool:
        """Records one attempt; False means the limit was already reached (and nothing is recorded)."""
        if self.used(key, now_s, window_s) >= limit:
            return False
        self._hits.setdefault(key, deque()).append(now_s)
        return True


# ---------------------------------------------------------------- accounts

class Accounts:
    """Registration, sign-in, sessions, and who may look at which television."""

    def __init__(self, db) -> None:
        self.db = db
        self.signups = Windows()
        self.logins = Windows()
        self.recovers = Windows()
        #: Outstanding registration challenges: nonce -> issued_at (ms). Single use, ten minutes.
        self._challenges: dict[str, float] = {}

    # ---- registration ----

    def challenge(self, now_ms: int) -> dict:
        """A nonce the form has to bring back. It proves the page was actually loaded and left alone
        for a moment — cheap for a person, one more thing for a script to fake."""
        self._expire_challenges(now_ms)
        nonce = secrets.token_urlsafe(18)
        self._challenges[nonce] = float(now_ms)
        return {"challenge": nonce, "issued_at": now_ms, "min_age_ms": CHALLENGE_MIN_AGE_MS}

    def _expire_challenges(self, now_ms: int) -> None:
        stale = [k for k, at in self._challenges.items() if now_ms - at > CHALLENGE_TTL_MS]
        for key in stale:
            self._challenges.pop(key, None)

    def register(self, body: dict, now_ms: int, source: str) -> dict:
        """Create the account and sign it in. Called only from `/v1/auth/register`."""
        now_s = now_ms / 1000
        if (body.get("website") or "").strip():
            # The honeypot. A human never sees this field; every form-filler fills it.
            raise GradeError("bot", 400, "registration refused")
        # Read-only checks: asking is free, only an account that actually gets created is counted
        # (see the bottom of this method). A parent whose challenge went stale, or who picked a
        # password that was too short, must not be locked out by their own retries.
        # The hour and the day keep separate keys: one deque shared by two windows would be trimmed
        # by the shorter one, and the daily ceiling would never be reached.
        if self.signups.used(f"ip:{source}", now_s, 3600) >= SIGNUP_PER_HOUR:
            raise GradeError("signup-rate", 429, "too many accounts from this address, try later")
        if self.signups.used(f"day:{source}", now_s, 86_400) >= SIGNUP_PER_DAY:
            raise GradeError("signup-rate", 429, "today's limit for this address is used up")
        if self.signups.used("global", now_s, 3600) >= SIGNUP_GLOBAL_PER_HOUR:
            raise GradeError("signup-closed", 503, "the server is not taking new accounts right now")

        issued = self._challenges.pop(str(body.get("challenge") or ""), None)
        if issued is None:
            raise GradeError("challenge-bad", 400, "reload the page and try again")
        age = now_ms - issued
        if age < CHALLENGE_MIN_AGE_MS or age > CHALLENGE_TTL_MS:
            raise GradeError("challenge-bad", 400, "reload the page and try again")

        email, key = normalise_email(body.get("email"))
        problem = password_problem(body.get("password"), email)
        if problem:
            raise GradeError(problem, 400)

        if self.db.query_one("SELECT id FROM account WHERE email_key = ?", (key,)):
            # Same wording as "bad password" would be over-thinking: without mail there is no way to
            # prove ownership anyway, and the phone has to tell the parent what to change.
            raise GradeError("email-taken", 409, "that email already has an account")

        # Optional — but never half of one. Without a mail gateway the question is the only way back
        # into an account, so the page offers it; the page also lets it be skipped, because a parent
        # who cannot think of an answer at sign-up is better served by an account they can use now
        # and a question they can add later from settings (POST /v1/auth/question/set) than by a
        # form they abandon. What is refused is an answer with no question: that is a credential
        # that looks stored and can never be used.
        question = body.get("question") if isinstance(body.get("question"), str) else ""
        answer = body.get("answer")
        if question.strip():
            question = normalise_question(question)
            answer_hash = hash_password(normalise_answer(answer))
        else:
            if isinstance(answer, str) and answer.strip():
                raise GradeError("answer-orphan", 400, "an answer needs a question")
            question, answer_hash = None, None

        # Counted last, so a refused form spends nothing: neither the address's budget nor the
        # server-wide one. A bot is still capped at five accounts an hour per address.
        self.signups.hit(f"ip:{source}", now_s, SIGNUP_PER_HOUR, 3600)
        self.signups.hit(f"day:{source}", now_s, SIGNUP_PER_DAY, 86_400)
        self.signups.hit("global", now_s, SIGNUP_GLOBAL_PER_HOUR, 3600)
        account_id = new_ulid()
        now_iso = limits.to_iso(now_ms)
        with self.db.tx() as cur:
            cur.execute(
                "INSERT INTO account (id, email, email_key, password_hash, recovery_question,"
                " recovery_answer_hash, created_at, last_login_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, email, key, hash_password(body["password"]), question,
                 answer_hash, now_iso, now_iso),
            )
        return self._signed_in(account_id, now_ms)

    # ---- sign in and out ----

    def login(self, body: dict, now_ms: int, source: str) -> dict:
        now_s = now_ms / 1000
        if not self.logins.hit(f"ip:{source}", now_s, LOGIN_PER_15MIN, 900):
            raise GradeError("login-rate", 429, "too many attempts, wait a few minutes")

        email, key = normalise_email(body.get("email"))
        row = self.db.query_one("SELECT * FROM account WHERE email_key = ?", (key,))
        if row is None:
            # Spend the same time as a real check: an instant 403 here is a way to ask "does this
            # person have an account".
            verify_password(body.get("password") or "", _DUMMY_HASH)
            raise GradeError("bad-credentials", 403)

        if row.get("disabled_at"):
            raise GradeError("account-disabled", 403)

        locked = limits.parse_iso(row.get("locked_until"))
        if locked is not None and locked > _dt(now_ms):
            left = int((locked.timestamp() * 1000 - now_ms) / 1000) + 1
            raise GradeError("account-locked", 429, f"{left}s")

        if not verify_password(body.get("password") or "", row["password_hash"]):
            self._note_failure(row, now_ms)
            raise GradeError("bad-credentials", 403)

        now_iso = limits.to_iso(now_ms)
        self.db.execute(
            "UPDATE account SET fail_count = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
            (now_iso, row["id"]),
        )
        return self._signed_in(row["id"], now_ms)

    def _note_failure(self, row: dict, now_ms: int) -> None:
        count = int(row.get("fail_count") or 0) + 1
        locked_until = None
        if count >= LOGIN_FAIL_LIMIT:
            locked_until = limits.to_iso(now_ms + LOGIN_LOCK_S * 1000)
            count = 0
        self.db.execute(
            "UPDATE account SET fail_count = ?, locked_until = ? WHERE id = ?",
            (count, locked_until, row["id"]),
        )

    # ---- the password, and getting it back ----

    def change_password(self, account_id: str, body: dict, now_ms: int) -> dict:
        """Signed in, old password, new password. Every session dies, this one included.

        Changing the password is how a parent takes the account back, so leaving other sessions alive
        would defeat the point: whoever else was holding a cookie keeps holding it. The phone is told
        `signed_out` and goes back to the login screen to prove the new password works.
        """
        row = self.db.query_one(
            "SELECT email, password_hash FROM account WHERE id = ?", (account_id,))
        if row is None:
            raise GradeError("not-signed-in", 401)
        if not verify_password(body.get("old_password") or "", row["password_hash"]):
            raise GradeError("bad-credentials", 403, "the current password is not right")
        problem = password_problem(body.get("password"), row["email"])
        if problem:
            raise GradeError(problem, 400)
        if verify_password(body.get("password") or "", row["password_hash"]):
            raise GradeError("password-same", 400, "that is the password you already have")

        with self.db.tx() as cur:
            cur.execute(
                "UPDATE account SET password_hash = ?, fail_count = 0, locked_until = NULL"
                " WHERE id = ?",
                (hash_password(body["password"]), account_id),
            )
            cur.execute("DELETE FROM account_session WHERE account_id = ?", (account_id,))
        return {"ok": True, "signed_out": True}

    def set_question(self, account_id: str, body: dict) -> dict:
        """Set or replace the question, while signed in.

        Needed for two reasons: accounts created before questions existed have none and would have no
        way back in, and a parent who typed something they cannot answer later deserves to fix it.
        Replacing it does not touch the password; the old answer simply stops working.
        """
        question = normalise_question(body.get("question"))
        answer = normalise_answer(body.get("answer"))
        self.db.execute(
            "UPDATE account SET recovery_question = ?, recovery_answer_hash = ?,"
            " recover_fail_count = 0, recover_locked_until = NULL WHERE id = ?",
            (question, hash_password(answer), account_id),
        )
        return {"ok": True, "question": question}

    def recover_question(self, body: dict, now_ms: int, source: str) -> dict:
        """The question this account chose, for the phone that forgot the password.

        An address with no account is told so. Registration already answers that question for anyone
        who asks (409 `email-taken`), so hiding it here would buy no secrecy and cost a parent ten
        minutes of guessing which address they used.
        """
        if not self.recovers.hit(f"look:{source}", now_ms / 1000, RECOVER_PER_15MIN, 900):
            raise GradeError("recover-rate", 429, "too many attempts, try again later")
        _, key = normalise_email(body.get("email"))
        row = self.db.query_one(
            "SELECT recovery_question, recover_locked_until FROM account WHERE email_key = ?", (key,))
        if row is None:
            raise GradeError("no-account", 404, "no account with that email")
        self._raise_if_recover_locked(row, now_ms)
        if not row.get("recovery_question"):
            # An account that predates questions. Say so; there is no honest alternative to offer.
            raise GradeError("no-question", 409, "this account has no security question")
        return {"question": row["recovery_question"]}

    def recover(self, body: dict, now_ms: int, source: str) -> dict:
        """Answer the question, set a new password, and every old session goes away.

        No session is handed back: the parent signs in with the password they just typed, which is
        also the only proof the new password is one they can actually reproduce.
        """
        if not self.recovers.hit(f"try:{source}", now_ms / 1000, RECOVER_PER_15MIN, 900):
            raise GradeError("recover-rate", 429, "too many attempts, try again later")
        _, key = normalise_email(body.get("email"))
        row = self.db.query_one(
            "SELECT id, email, recovery_answer_hash, recover_fail_count, recover_locked_until"
            " FROM account WHERE email_key = ?", (key,))
        if row is None:
            # Same work as a real comparison, so the reply time does not announce "no such account".
            verify_password("nobody", _DUMMY_HASH)
            raise GradeError("no-account", 404, "no account with that email")
        self._raise_if_recover_locked(row, now_ms)

        answer = normalise_answer(body.get("answer"))
        stored = row.get("recovery_answer_hash")
        if not stored or not verify_password(answer, stored):
            self._note_recover_failure(row, now_ms)
            raise GradeError("bad-answer", 403, "that answer does not match")

        # The answer was right, but a weak password must not cost the parent their only good guess:
        # nothing is recorded until the password passes, so the same answer can be sent again.
        problem = password_problem(body.get("password"), row["email"])
        if problem:
            raise GradeError(problem, 400)

        now_iso = limits.to_iso(now_ms)
        with self.db.tx() as cur:
            cur.execute(
                "UPDATE account SET password_hash = ?, recover_fail_count = 0,"
                " recover_locked_until = NULL, fail_count = 0, locked_until = NULL WHERE id = ?",
                (hash_password(body["password"]), row["id"]),
            )
            cur.execute("DELETE FROM account_session WHERE account_id = ?", (row["id"],))
        return {"ok": True, "email": row["email"]}

    def _raise_if_recover_locked(self, row: dict, now_ms: int) -> None:
        locked = limits.parse_iso(row.get("recover_locked_until"))
        if locked is not None and locked > _dt(now_ms):
            left = int((locked.timestamp() * 1000 - now_ms) / 1000) + 1
            raise GradeError("recover-locked", 429, f"{left}s")

    def _note_recover_failure(self, row: dict, now_ms: int) -> None:
        count = int(row.get("recover_fail_count") or 0) + 1
        locked_until = None
        if count >= RECOVER_FAIL_LIMIT:
            locked_until = limits.to_iso(now_ms + RECOVER_LOCK_S * 1000)
            count = 0
        self.db.execute(
            "UPDATE account SET recover_fail_count = ?, recover_locked_until = ? WHERE id = ?",
            (count, locked_until, row["id"]),
        )

    def logout(self, token: str | None) -> dict:
        if token:
            self.db.execute("DELETE FROM account_session WHERE token_hash = ?", (_hash(token),))
        return {"ok": True}

    # ---- sessions ----

    def resolve(self, token: str | None, now_ms: int) -> dict:
        """The account behind a session cookie, refreshing its expiry on the way through."""
        if not token:
            raise GradeError("not-signed-in", 401)
        row = self.db.query_one(
            "SELECT s.token_hash, s.account_id, s.created_at, s.expires_at, a.email, a.created_at AS"
            " account_created_at, a.disabled_at FROM account_session s"
            " JOIN account a ON a.id = s.account_id WHERE s.token_hash = ?",
            (_hash(token),),
        )
        if row is None:
            raise GradeError("not-signed-in", 401)
        expires = limits.parse_iso(row.get("expires_at"))
        if expires is None or expires <= _dt(now_ms):
            self.db.execute("DELETE FROM account_session WHERE token_hash = ?", (row["token_hash"],))
            raise GradeError("not-signed-in", 401)
        if row.get("disabled_at"):
            raise GradeError("account-disabled", 403)

        # Sliding, within the absolute cap: a phone used every day never has to sign in again, and a
        # cookie that sat in a drawer for a year is not resurrected by one visit.
        created = limits.parse_iso(row.get("created_at"))
        ceiling_ms = int(created.timestamp() * 1000) if created else now_ms
        wanted = min(now_ms + SESSION_DAYS * 86_400_000, ceiling_ms + SESSION_MAX_DAYS * 86_400_000)
        if wanted > int(expires.timestamp() * 1000) + 3_600_000:
            self.db.execute(
                "UPDATE account_session SET expires_at = ?, last_seen_at = ? WHERE token_hash = ?",
                (limits.to_iso(wanted), limits.to_iso(now_ms), row["token_hash"]),
            )
        return {"id": row["account_id"], "email": row["email"]}

    def _signed_in(self, account_id: str, now_ms: int) -> dict:
        token = secrets.token_urlsafe(32)
        expires = now_ms + SESSION_DAYS * 86_400_000
        now_iso = limits.to_iso(now_ms)
        self.db.execute(
            "INSERT INTO account_session (token_hash, account_id, created_at, last_seen_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (_hash(token), account_id, now_iso, now_iso, limits.to_iso(expires)),
        )
        return {
            "account": self.account_json(account_id),
            "token": token,
            "max_age_seconds": SESSION_DAYS * 86_400,
        }

    def account_json(self, account_id: str) -> dict:
        row = self.db.query_one("SELECT id, email, created_at FROM account WHERE id = ?", (account_id,))
        if row is None:
            raise GradeError("not-signed-in", 401)
        return {"id": row["id"], "email": row["email"], "created_at": row.get("created_at")}

    # ---- devices ----

    def devices_of(self, account_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT d.id, d.name, d.app_version, d.last_seen_at, d.revoked_at, ad.bound_at"
            " FROM account_device ad JOIN device d ON d.id = ad.device_id"
            " WHERE ad.account_id = ? ORDER BY ad.bound_at",
            (account_id,),
        )
        return [
            {
                "device_id": row["id"],
                "name": row.get("name"),
                "app_version": row.get("app_version"),
                "last_seen_at": row.get("last_seen_at"),
                "bound_at": row.get("bound_at"),
                "revoked": bool(row.get("revoked_at")),
            }
            for row in rows
        ]

    def may_see(self, account_id: str, device_id: str) -> bool:
        return self.db.query_one(
            "SELECT 1 FROM account_device WHERE account_id = ? AND device_id = ?",
            (account_id, device_id),
        ) is not None

    def require_device(self, account_id: str, device_id: str) -> dict:
        """The device row, or 403. Not 404: the caller is signed in and asking about a television that
        is not theirs, which is a permission problem, and the page has a sentence for it."""
        if not self.may_see(account_id, device_id):
            raise GradeError("not-yours", 403, "this television is not on your account")
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        if row is None:
            raise GradeError("unknown-device", 404)
        if row.get("revoked_at"):
            raise GradeError("device-revoked", 403)
        return row

    def bind(self, account_id: str, device_id: str, now_ms: int) -> None:
        """Adds one row; keeps `device.bound_at` as the derived "somebody holds this TV".

        Binding twice is not an error: the same phone re-entering a fresh code for a TV it already
        has is what happens when someone reloads the page, and it should look like nothing happened.
        """
        now_iso = limits.to_iso(now_ms)
        already = self.may_see(account_id, device_id)
        with self.db.tx() as cur:
            if not already:
                cur.execute(
                    "INSERT INTO account_device (account_id, device_id, bound_at) VALUES (?, ?, ?)",
                    (account_id, device_id, now_iso),
                )
            cur.execute(
                "UPDATE device SET bound_at = COALESCE(bound_at, ?), pair_code_hash = NULL,"
                " last_seen_at = ? WHERE id = ?",
                (now_iso, now_iso, device_id),
            )

    def unbind(self, account_id: str, device_id: str) -> dict:
        """Removes my row. The television is only "unbound" once nobody holds it — the second parent
        unbinding must not turn off the first one's page."""
        self.db.execute(
            "DELETE FROM account_device WHERE account_id = ? AND device_id = ?",
            (account_id, device_id),
        )
        left = self.db.scalar(
            "SELECT COUNT(*) FROM account_device WHERE device_id = ?", (device_id,), default=0
        )
        if not left:
            self.db.execute(
                "UPDATE device SET bound_at = NULL, token_hash = NULL, pair_code_hash = NULL"
                " WHERE id = ?",
                (device_id,),
            )
        return {"ok": True, "still_bound": bool(left)}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dt(epoch_ms: int) -> datetime.datetime:
    """Milliseconds since the epoch as a naive local datetime — the shape `limits.parse_iso` gives."""
    return datetime.datetime.fromtimestamp(epoch_ms / 1000)


#: A real scrypt hash of a value nobody knows, so a login for an unknown email costs the same as a
#: login with a wrong password. Built once at import (about 270 ms, paid at start-up).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))
