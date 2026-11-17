"""The HTTP surface: standard library, no framework.

    GET  /health
    POST /v1/pair/start          register the TV, hand out its secret and a six-character code
    POST /v1/pair/renew          a new code for the same TV, when the first one expired
    GET  /v1/pair/status         has the parent typed the code yet (the TV polls this)
    GET  /v1/quota               today's counters, for the lock screen
    GET  /v1/config              settings the parent changed, by version (the TV polls this)
    POST /v1/heartbeat           "still here", plus the viewing time the server cannot see itself
    POST /v1/quiz/next           serve one group
    POST /v1/quiz/{id}/grade     grade it, maybe hand back a signed ticket

    GET  /                       the parent page (server/web/)
    POST /v1/h5/pair             swap a code for a cookie
    GET  /v1/h5/summary          today, for the phone
    GET  /v1/h5/config           read the three numbers
    PUT  /v1/h5/config           write them, bump the version
    GET  /v1/h5/wrongbook        wrong answers in the last N days, with answers and explanations
    GET  /v1/h5/mastery          the weak topics, worst first, with today's quota snapshot
    GET  /v1/h5/mastery/{id}     one topic: score, the ledger behind it, the line to say out loud
    POST /v1/h5/topic/{id}/adjust  the parent's ±10, with a reason
    POST /v1/h5/paper            record "he got 3 wrong on this topic" from a paper
    POST /v1/h5/question/{id}/flag  a bad question: stop serving it, give back the score it caused
    GET  /v1/h5/curriculum       the term's first day and where each book's unit stands
    PUT  /v1/h5/curriculum       set that first day; PUT …/unit overrides one book by hand
    GET  /v1/h5/report           seven days: attempts, passes, the three worst topics
    POST /v1/h5/unpair           drop this phone's cookie

Why `http.server` and not FastAPI: one process, a handful of JSON routes, no streaming, no websockets,
and a 2 GB box that should not be running an ASGI server plus a process supervisor. The part a framework
would buy — validation and routing — is forty lines here, and the deployment story becomes "python -m
server.app.api".

**Every television route but `POST /v1/pair/start` needs `Authorization: Bearer <device_secret>`**,
where the secret came back from `pair/start` exactly once and is stored hashed in `device.secret_hash`.
Before 2026-11-17 the `device_id` in the body was the whole story, which was survivable on a LAN and
is not survivable on the internet: an id is a name, not a password. What is still missing is rate
limiting outside pairing — one address can knock as often as it likes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from server.app import grade as grading
from server.app import limits, picker, ticket
from server.app.db import Database, default_db
from server.app.grade import GradeError
from server.app.h5 import H5, PAIR_ALPHABET, PAIR_CODE_LEN, PAIR_TTL_MS, SESSION_COOKIE
from server.app.ids import new_ulid
from server.app.llm import client as llm

#: Dev-only clock override, so the acceptance suite can test "the ten minutes ran out" and "it is
#: tomorrow now" without waiting. Off unless QUIZ_DEV=1; a production server refuses the field outright.
DEV_MODE = os.environ.get("QUIZ_DEV") == "1"

#: The parent page. Three files, no build step; see server/web/.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
WEB_FILES = {
    "": ("index.html", "text/html; charset=utf-8"),
    "index.html": ("index.html", "text/html; charset=utf-8"),
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "app.js": ("app.js", "text/javascript; charset=utf-8"),
    "manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "icon.svg": ("icon.svg", "image/svg+xml"),
}

#: 每个响应都带的安全头。`style-src` 必须留 `'unsafe-inline'`：页面把 25 处 `style="…"` 拼进 HTML，
#: 掐掉它会让错题本和填报弹层错位。脚本侧没有内联 `<script>`、也没有 `onclick=` 这类内联处理器，
#: 所以 `script-src` 只留 `'self'`——真正防 XSS 的是这一条。
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'"),
)

#: 只在 TLS 连接上发。HSTS 对 IP 地址本来不生效（RFC 6797 只管域名），先发着：等 443 + 域名上线，
#: 这台机器就自动受它保护，那时不用再改一次代码。
HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000")


def _hash_secret(secret: str) -> str:
    """The column value for a television's secret. SHA-256, no salt: this is 32 bytes of `secrets`,
    not a password a person chose, so there is nothing to make expensive and nothing to guess."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def build_signer():
    key_path = os.environ.get("QUIZ_TICKET_KEY")
    if key_path:
        pem = Path(key_path).expanduser().read_bytes()
        return ticket.Es256Signer(private_key_pem=pem)
    secret = os.environ.get("QUIZ_TICKET_SECRET")
    if secret:
        return ticket.HmacSigner(secret.encode("utf-8"))
    # Development default. Loud, because a TV build shipped against this secret can mint its own time.
    print(
        "warning: no QUIZ_TICKET_KEY / QUIZ_TICKET_SECRET set; signing with the development HMAC key",
        file=sys.stderr,
    )
    return ticket.HmacSigner(b"development-only-secret-32-bytes!!")


class Api:
    """The routes, with the request object kept out of them so they can be called from a test."""

    def __init__(self, db: Database, signer):
        self.db = db
        self.signer = signer
        # The parent page's routes live in their own module; they share the database and nothing else.
        self.h5 = H5(db)
        self.started_at = time.monotonic()

    # ---- helpers ----

    def _device(self, device_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        if row is None:
            raise grading.GradeError("unknown-device", 404, "pair the TV first")
        if row.get("revoked_at"):
            raise grading.GradeError("device-revoked", 403)
        return row

    def authenticate(self, secret: str | None, device_id: str) -> dict:
        """The television behind a request, or 401.

        `device_id` is a name the TV chose for itself and it travels in the body; the secret is the
        credential and it travels in `Authorization: Bearer`. Before this existed, anyone who learned
        an id could start groups and hand them in on that television's behalf — on a LAN that was
        library-hours vandalism, on the open internet it is the first thing that happens.

        A wrong secret and an unknown id answer the same thing on purpose: which ids exist is not
        information a stranger should be able to collect by trying.
        """
        if not secret or not device_id:
            raise grading.GradeError("bad-device", 401, "the television's secret is missing")
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        stored = (row or {}).get("secret_hash") or ""
        # Compare even when there is no row, so the timing of "unknown id" and "wrong secret" match.
        matched = secrets.compare_digest(stored, _hash_secret(secret)) if stored else False
        if row is None or not matched:
            raise grading.GradeError("bad-device", 401, "device_id and secret do not match")
        if row.get("revoked_at"):
            raise grading.GradeError("device-revoked", 403)
        return row

    def owns(self, device_id: str, secret: str | None) -> bool:
        """Whether this request may act as [device_id], without raising. Used by pairing, which has to
        fall back to "a new television" rather than fail a screen that is already on."""
        if not device_id or not secret:
            return False
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        stored = (row or {}).get("secret_hash") or ""
        return bool(stored) and not row.get("revoked_at") and secrets.compare_digest(
            stored, _hash_secret(secret))

    def _now(self, body: dict) -> int:
        if DEV_MODE and isinstance(body.get("now_ms"), int):
            return int(body["now_ms"])
        return int(time.time() * 1000)

    # ---- routes ----

    def health(self, body: dict, now_ms: int) -> dict:
        return {"ok": True, "dsn": self.db.dsn.split(":")[0], "questions": self.db.scalar(
            "SELECT COUNT(*) FROM question", default=0)}

    def healthz(self) -> dict:
        """Liveness for the deployment's probes: state, not diagnosis.

        It deliberately does **not** call DeepSeek. A probe that spends money and takes two seconds to
        answer is a probe that turns a traffic spike into an outage of its own. What it reports is what
        can be known without asking anyone: whether a key was injected and for how long we have been up.
        """
        return {
            "status": "ok",
            "model": llm.model(),
            "key_present": llm.key_present(),
            "uptime_s": int(time.monotonic() - self.started_at),
        }

    def readyz(self) -> dict:
        """Readiness: the database answers and the model side is not in a known-bad state."""
        try:
            self.db.scalar("SELECT 1", default=0)
            database = True
        except Exception:  # noqa: BLE001 - a probe reports, it does not raise
            database = False
        body = self.healthz()
        body["db"] = database
        body["llm"] = "degraded" if llm.degraded() else "ok"
        body["status"] = "ok" if database and not llm.degraded() else "degraded"
        return body

    def pair_start(self, body: dict, now_ms: int, secret: str | None = None) -> dict:
        """Make a code for the TV to show, and the secret it will use from now on.

        A television that presents its own id *and* its own secret keeps that row: minting a new one
        per visit would split its counters across rows, and the daily caps are enforced per row, so
        three visits would buy three sets of three groups. Without the secret, an id in the body is
        just a number someone typed — that path now creates a new television instead of adopting
        (and thereby locking out) somebody else's.
        """
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LEN))
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        expires = limits.to_iso(now_ms + PAIR_TTL_MS)
        now_iso = limits.to_iso(now_ms)
        # Rotated on every pairing, so the plaintext is handed over exactly once and a leaked
        # preference file is worth nothing after the parent pairs again.
        fresh_secret = new_device_secret()
        secret_hash = _hash_secret(fresh_secret)

        existing = body.get("device_id")
        if isinstance(existing, str) and existing and self.owns(existing, secret):
            row = self.db.query_one("SELECT * FROM device WHERE id = ?", (existing,))
            self.db.execute(
                "UPDATE device SET pair_code_hash = ?, pair_expires_at = ?, app_version = ?,"
                " last_seen_at = ?, secret_hash = ? WHERE id = ?",
                (code_hash, expires, body.get("app_version") or row.get("app_version"),
                 now_iso, secret_hash, existing),
            )
            return {
                "device_id": existing,
                "device_secret": fresh_secret,
                "pair_code": code,
                "expires_at": expires,
                "pair_ttl_seconds": PAIR_TTL_MS // 1000,
                "bound": bool(row.get("bound_at")),
            }

        device_id = new_ulid(now_ms)
        self.db.execute(
            "INSERT INTO device (id, name, pair_code_hash, pair_expires_at, secret_hash, app_version,"
            " last_seen_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                body.get("name"),
                code_hash,
                expires,
                secret_hash,
                body.get("app_version"),
                now_iso,
                now_iso,
            ),
        )
        return {
            "device_id": device_id,
            "device_secret": fresh_secret,
            "pair_code": code,
            "expires_at": expires,
            "pair_ttl_seconds": PAIR_TTL_MS // 1000,
            "bound": False,
        }

    def quota(self, query: dict, now_ms: int) -> dict:
        device_id = query.get("device_id") or ""
        self._device(device_id)
        day_key = limits.validate_day_key(query.get("day_key") or limits.to_iso(now_ms)[:10])
        row = limits.get_or_create_daily(self.db, device_id, day_key, now_ms)
        state = limits.quota_state(row)
        state["cooldown_seconds"] = limits.is_cooldown_active(row, now_ms)
        state["day_key"] = day_key
        return state

    def quiz_next(self, body: dict, now_ms: int) -> dict:
        device_id = body.get("device_id") or ""
        self._device(device_id)
        day_key = limits.validate_day_key(body.get("day_key") or limits.to_iso(now_ms)[:10])

        row = limits.get_or_create_daily(self.db, device_id, day_key, now_ms)
        state = limits.quota_state(row)

        cooldown = limits.is_cooldown_active(row, now_ms)
        if cooldown:
            raise grading.GradeError("cooldown", 429, f"{cooldown}s left")
        if state["attempts_left"] <= 0:
            raise grading.GradeError("attempts-exhausted", 429, "six groups a day, and they are spent")
        if not state["award_available"]:
            raise grading.GradeError("quota-exhausted", 409, "three groups or ninety minutes, already earned")

        difficulty = limits.target_difficulty(row)
        try:
            group = picker.pick_group(self.db, device_id, day_key, now_ms, difficulty)
        except picker.NotEnoughQuestions as exc:
            raise grading.GradeError("no-questions", 503, str(exc)) from exc

        session_id = new_ulid(now_ms)
        expires_at = now_ms + limits.SESSION_TTL_MS
        with self.db.tx() as cur:
            cur.insert(
                "INSERT INTO quiz_session (id, device_id, day_key, issued_at, expires_at, status,"
                " target_difficulty, question_ids, review_question_ids, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'issued', ?, ?, ?, ?)",
                (
                    session_id, device_id, day_key, limits.to_iso(now_ms), limits.to_iso(expires_at),
                    difficulty, json.dumps([int(q["id"]) for q in group.questions]),
                    # Server-side bookkeeping only: the day's per-topic cap and the parent's mix
                    # metric read this. It never appears in the response (§9, §10).
                    json.dumps(group.review_ids),
                    limits.to_iso(now_ms),
                ),
            )
            cur.execute(
                "UPDATE daily_state SET attempt_count = attempt_count + 1, updated_at = ?"
                " WHERE device_id = ? AND day_key = ?",
                (limits.to_iso(now_ms), device_id, day_key),
            )

        return {
            "session_id": session_id,
            "day_key": day_key,
            "expires_at": limits.to_iso(expires_at),
            "ttl_seconds": limits.SESSION_TTL_MS // 1000,
            "questions": picker.payload(self.db, group.questions),
            "quota": limits.quota_state(
                self.db.query_one(
                    "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?", (device_id, day_key)
                )
            ),
        }

    def quiz_grade(self, session_id: str, body: dict, now_ms: int) -> dict:
        device_id = body.get("device_id") or ""
        self._device(device_id)
        answers = body.get("answers")
        if not isinstance(answers, list):
            raise grading.GradeError("bad-answers", 400, "answers must be a list")
        return grading.grade_session(
            self.db,
            session_id=session_id,
            device_id=device_id,
            answers=answers,
            now_ms=now_ms,
            signer=self.signer,
        )


GRADE_PATH = re.compile(r"^/v1/quiz/([A-Z0-9]{26})/grade$")

#: The television's own routes. Every one of them needs the bearer secret `/v1/pair/start` handed out;
#: pairing itself is the one exception (that is where the secret comes from). The parent page's routes
#: are authenticated by its session cookie, and the probes by nothing at all.
TV_GET_ROUTES = frozenset({"/v1/quota", "/v1/pair/status", "/v1/config"})
TV_POST_ROUTES = frozenset({"/v1/pair/renew", "/v1/heartbeat", "/v1/quiz/next"})

#: Parent-page routes carrying an id. Compiled once, matched after the exact paths, so a typo in a
#: number is a 404 rather than a route that silently takes the wrong topic.
H5_TOPIC_PATH = re.compile(r"^/v1/h5/mastery/(\d+)$")
H5_TOPIC_ADJUST_PATH = re.compile(r"^/v1/h5/topic/(\d+)/adjust$")
H5_QUESTION_FLAG_PATH = re.compile(r"^/v1/h5/question/(\d+)/flag$")


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # quieter default; one line per request
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def _bearer(self) -> str | None:
            """The television's secret, from `Authorization: Bearer …`."""
            scheme, _, token = (self.headers.get("Authorization") or "").partition(" ")
            if scheme.lower() != "bearer":
                return None
            return token.strip() or None

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > 64 * 1024:
                raise grading.GradeError("body-too-large", 413)
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise grading.GradeError("bad-json", 400) from exc
            if not isinstance(data, dict):
                raise grading.GradeError("bad-json", 400, "expected an object")
            return data

        def _security_headers(self) -> None:
            """Security headers. Called before every end_headers, JSON and static alike."""
            for key, value in SECURITY_HEADERS:
                self.send_header(key, value)
            if self._secure():
                self.send_header(*HSTS_HEADER)

        def _send(self, status: int, payload: dict, headers: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _serve_static(self, path: str) -> bool:
            """Three files, looked up in a table rather than joined onto a directory: no path traversal
            to get wrong, and nothing else on disk becomes reachable by accident."""
            entry = WEB_FILES.get(path.lstrip("/"))
            if entry is None:
                return False
            filename, mime = entry
            try:
                data = (WEB_DIR / filename).read_bytes()
            except OSError:
                self._send(500, {"error": "web-missing", "detail": filename})
                return True
            # The names carry no hash, so the validator has to be the content: 改一行重新部署，
            # 手机下次打开就会拿到新的；没改就只回 304，省掉 9 KB 的往返。
            etag = '"%s"' % hashlib.sha256(data).hexdigest()[:32]
            if self.headers.get("If-None-Match", "").strip() in (etag, "W/" + etag):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache, must-revalidate")
                self._security_headers()
                self.end_headers()
                return True
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", etag)
            # The names carry no hash, so no long cache: a phone stuck on yesterday's JavaScript after
            # an update is the classic "works on my laptop".
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self._security_headers()
            self.end_headers()
            self.wfile.write(data)
            return True

        def _cookie(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE and value:
                    return value
            return None

        def _source(self) -> str:
            """Who to rate limit. Behind a proxy the socket address is the proxy; the header is used
            but only its first hop — the rest is whatever the caller chose to write."""
            forwarded = self.headers.get("X-Forwarded-For") or ""
            if forwarded:
                return forwarded.split(",")[0].strip()
            return self.client_address[0]

        def _secure(self) -> bool:
            return isinstance(self.connection, ssl.SSLSocket)

        def _session_cookie(self, token: str, max_age: int) -> str:
            parts = [f"{SESSION_COOKIE}={token}", "Path=/", f"Max-Age={max_age}",
                     "HttpOnly", "SameSite=Lax"]
            if self._secure():
                parts.append("Secure")
            return "; ".join(parts)

        def _day_key(self, query: dict, body: dict, now_ms: int) -> str:
            """The TV's local day, computed by whoever knows its timezone — the phone, in practice.
            Falls back to the server's date, which is wrong for a TV in another zone by at most a day."""
            return limits.validate_day_key(
                query.get("day_key") or body.get("day_key") or limits.to_iso(now_ms)[:10]
            )

        def _auth_route(self, path: str, method: str, query: dict, body: dict, now_ms: int) -> bool:
            """Sign up, sign in, sign out, change or recover the password, and "who am I".

            These are the only routes under `/v1/auth/`; everything else is 404 rather than a hint
            about what exists (docs/ACCOUNT.md §7).
            """
            accounts = api.h5.accounts
            if not path.startswith("/v1/auth/"):
                return False
            if path == "/v1/auth/challenge" and method == "GET":
                self._send(200, accounts.challenge(now_ms))
                return True

            if path == "/v1/auth/register" and method == "POST":
                result = accounts.register(body, now_ms, self._source())
                cookie = self._session_cookie(result.pop("token"), result.pop("max_age_seconds"))
                result["devices"] = accounts.devices_of(result["account"]["id"])
                self._send(201, result, {"Set-Cookie": cookie})
                return True

            if path == "/v1/auth/login" and method == "POST":
                result = accounts.login(body, now_ms, self._source())
                cookie = self._session_cookie(result.pop("token"), result.pop("max_age_seconds"))
                result["devices"] = accounts.devices_of(result["account"]["id"])
                self._send(200, result, {"Set-Cookie": cookie})
                return True

            if path == "/v1/auth/logout" and method == "POST":
                self._send(200, accounts.logout(self._cookie()),
                           {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0"})
                return True

            if path == "/v1/auth/me" and method == "GET":
                token = self._cookie()
                try:
                    account = accounts.resolve(token, now_ms)
                except GradeError as exc:
                    # A phone that paired before accounts existed (A5) is not signed out — it is one
                    # form away from being an account, so `/me` says so instead of 401.
                    if exc.code != "not-signed-in":
                        raise
                    legacy = api.h5.legacy_device(token)
                    if legacy is None:
                        raise
                    self._send(200, {"legacy": True,
                                     "device": {"device_id": legacy["id"], "name": legacy.get("name")}})
                    return True
                # `account_json` rather than the raw resolve() row: the page needs `has_question` to
                # know whether replacing the question has to prove the old answer first.
                self._send(200, {"account": accounts.account_json(account["id"]),
                                 "devices": accounts.devices_of(account["id"])})
                return True

            if path == "/v1/auth/claim" and method == "POST":
                result = api.h5.claim(body, now_ms, self._source(), self._cookie())
                cookie = self._session_cookie(result.pop("token"), result.pop("max_age_seconds"))
                self._send(200, result, {"Set-Cookie": cookie})
                return True

            if path == "/v1/auth/password" and method == "POST":
                # Old password in, new password out, every session revoked — including this one, so
                # the cookie goes with the answer.
                account = accounts.resolve(self._cookie(), now_ms)
                result = accounts.change_password(account["id"], body, now_ms)
                self._send(200, result,
                           {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0"})
                return True

            if path == "/v1/auth/question" and method == "POST":
                self._send(200, accounts.recover_question(body, now_ms, self._source()))
                return True

            if path == "/v1/auth/question/set" and method == "POST":
                account = accounts.resolve(self._cookie(), now_ms)
                self._send(200, accounts.set_question(account["id"], body, now_ms))
                return True

            if path == "/v1/auth/recover" and method == "POST":
                self._send(200, accounts.recover(body, now_ms, self._source()))
                return True

            self._send(404, {"error": "not-found", "path": path})
            return True

        def _h5_route(self, path: str, method: str, query: dict, body: dict, now_ms: int) -> bool:
            """The parent page's API. Every route here needs a session, and every route but `pair`
            needs a television that session's account holds (docs/ACCOUNT.md §7)."""
            accounts = api.h5.accounts

            if path == "/v1/h5/pair":
                if method != "POST":
                    return False
                account = accounts.resolve(self._cookie(), now_ms)
                self._send(200, api.h5.pair(account["id"], body, now_ms, self._source()))
                return True

            if not path.startswith("/v1/h5/"):
                return False

            account = accounts.resolve(self._cookie(), now_ms)
            device_id = str(query.get("device_id") or body.get("device_id") or "")

            if path == "/v1/h5/summary" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.summary(device_id, self._day_key(query, body, now_ms), now_ms))
            elif path == "/v1/h5/config" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.get_config(device_id))
            elif path == "/v1/h5/config" and method == "PUT":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.put_config(device_id, body, now_ms, source="h5",
                                                  account_id=account["id"]))
            elif path == "/v1/h5/wrongbook" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                try:
                    days = int(query.get("days") or 7)
                    limit = int(query.get("limit") or 60)
                    offset = int(query.get("offset") or 0)
                except ValueError:
                    days, limit, offset = 7, 60, 0
                self._send(200, api.h5.wrongbook(
                    device_id, self._day_key(query, body, now_ms), days, limit, offset,
                    str(query.get("source") or "all"),
                ))
            elif path == "/v1/h5/mastery" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.weak_list(
                    device_id, self._day_key(query, body, now_ms), now_ms))
            elif H5_TOPIC_PATH.match(path) and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.weak_topic(
                    device_id, int(H5_TOPIC_PATH.match(path).group(1)),
                    self._day_key(query, body, now_ms), now_ms))
            elif H5_TOPIC_ADJUST_PATH.match(path) and method == "POST":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.adjust_topic(
                    device_id, int(H5_TOPIC_ADJUST_PATH.match(path).group(1)), body, now_ms,
                    account["id"]))
            elif path == "/v1/h5/topics" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.catalog(device_id))
            elif path == "/v1/h5/paper" and method == "POST":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.add_paper(device_id, account["id"], body, now_ms))
            elif H5_QUESTION_FLAG_PATH.match(path) and method == "POST":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.flag_question(
                    device_id, int(H5_QUESTION_FLAG_PATH.match(path).group(1)), body, now_ms,
                    account["id"]))
            elif path == "/v1/h5/curriculum" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.curriculum_overview(device_id, now_ms))
            elif path == "/v1/h5/curriculum" and method == "PUT":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.put_anchor(device_id, body, now_ms))
            elif path == "/v1/h5/curriculum/unit" and method == "PUT":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.put_unit(device_id, body, now_ms))
            elif path == "/v1/h5/report" and method == "GET":
                api.h5.device_of(account["id"], device_id)
                self._send(200, api.h5.report(device_id, self._day_key(query, body, now_ms), now_ms))
            elif path == "/v1/h5/unpair" and method == "POST":
                self._send(200, api.h5.unpair(account["id"], device_id))
            else:
                self._send(404, {"error": "not-found", "path": path})
            return True

        def _dispatch(self, method: str) -> None:
            path, _, query_string = self.path.partition("?")
            query = {}
            for pair in query_string.split("&"):
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    query[unquote(key)] = unquote(value)

            try:
                # PUT carries a body too; not reading it would leave the bytes in the socket and the
                # next request on this keep-alive connection would be parsed as garbage.
                body = self._read_json() if method in ("POST", "PUT") else {}
                now_ms = api._now(body)

                # One gate in front of every route the television owns, instead of a check inside each
                # of them that a later route could forget to copy. `pair/start` is outside it: that is
                # the call that hands the secret out.
                if (path in TV_GET_ROUTES or path in TV_POST_ROUTES or GRADE_PATH.match(path)):
                    api.authenticate(self._bearer(),
                                     str(query.get("device_id") or body.get("device_id") or ""))

                if method == "GET" and path == "/health":
                    self._send(200, api.health(body, now_ms))
                elif method == "GET" and path == "/healthz":
                    self._send(200, api.healthz())
                elif method == "GET" and path == "/readyz":
                    self._send(200, api.readyz())
                elif method == "GET" and path == "/v1/quota":
                    self._send(200, api.quota(query, now_ms))
                elif method == "GET" and path == "/v1/pair/status":
                    self._send(200, api.h5.pair_status(query.get("device_id") or ""))
                elif method == "GET" and path == "/v1/config":
                    self._send(200, api.h5.tv_config(query))
                elif method == "POST" and path == "/v1/heartbeat":
                    self._send(200, api.h5.heartbeat(body, now_ms))
                elif method == "POST" and path == "/v1/pair/start":
                    self._send(200, api.pair_start(body, now_ms, self._bearer()))
                elif method == "POST" and path == "/v1/pair/renew":
                    self._send(200, api.h5.pair_renew(body, now_ms))
                elif method == "POST" and path == "/v1/quiz/next":
                    self._send(200, api.quiz_next(body, now_ms))
                elif method == "POST" and GRADE_PATH.match(path):
                    session_id = GRADE_PATH.match(path).group(1)
                    self._send(200, api.quiz_grade(session_id, body, now_ms))
                elif self._auth_route(path, method, query, body, now_ms):
                    pass
                elif self._h5_route(path, method, query, body, now_ms):
                    pass
                elif method == "GET" and self._serve_static(path):
                    pass
                else:
                    self._send(404, {"error": "not-found", "path": path})
            except grading.GradeError as exc:
                self._send(exc.http_status, {"error": exc.code, "detail": exc.detail})
            except ValueError as exc:
                self._send(400, {"error": "bad-request", "detail": str(exc)})
            except Exception as exc:  # noqa: BLE001 - a crashed handler must still answer
                import traceback

                traceback.print_exc()
                self._send(500, {"error": "internal", "detail": type(exc).__name__})

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

    return Handler


#: 一次 TLS 握手最多给多久。握手是在 accept 的那个线程里同步做的，不设上限的话**一条握不完的连接**
#: 就能把这个监听钉死——端口扫描器、握手中途被掐断的请求都算。2026-11-17 家长页就是这么整个不可用的：
#: 一条只连不发数据的 TCP 连接，让 8443 之后的所有请求都在 backlog 里排到超时。
#: 取 5 秒：正常手机/电视握手都在 1 秒内完成，而卡住的时候下一条请求最多只等这么久，不是无限等。
HANDSHAKE_TIMEOUT = 5.0


class _TLSServer(ThreadingHTTPServer):
    """自己带超时做 TLS 握手的 ThreadingHTTPServer。

    不写成 `context.wrap_socket(server.socket, server_side=True)` 是因为那种写法会把握手放进
    CPython 的 `accept()` 里，而且没有任何超时；这里改成先 accept 明文连接、再用带超时的 socket 换
    TLS，握不完就关掉它、跳过这条，监听继续服务下一条。
    """

    def __init__(self, address: tuple[str, int], handler, context: ssl.SSLContext) -> None:
        self._context = context
        super().__init__(address, handler)

    def get_request(self):  # noqa: ANN201 - 返回 (socket, address)，基类就是这么约定的
        connection, address = self.socket.accept()
        connection.settimeout(HANDSHAKE_TIMEOUT)
        try:
            tls = self._context.wrap_socket(connection, server_side=True)
        except OSError:
            # SSLError 与 socket.timeout 都是 OSError：握手失败/超时，关掉这条，让 serve_forever 继续。
            connection.close()
            raise
        tls.settimeout(None)
        return tls, address


def _bind(host: str, port: int, api: "Api", tls_cert: str | None, tls_key: str | None) -> ThreadingHTTPServer:
    handler = make_handler(api)
    if tls_cert and tls_key:
        # TLS here rather than in a reverse proxy: the television pins a CA it ships with, the parent
        # page needs a publicly trusted certificate, and those are two different certificates — but
        # they can still be two sockets of one process, which is one thing for systemd to restart.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        server: ThreadingHTTPServer = _TLSServer((host, port), handler, context)
    else:
        server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(listeners: list[tuple[str, int, str | None, str | None]], db: Database, signer) -> None:
    """Bind every listener, serve them from this one process, stop on SIGTERM.

    8443 carries the publicly trusted certificate for the parent page; 9443 carries the private CA the
    television pins. Two processes would also work and would double the memory for no benefit on a
    1.6 GB box, and a reverse proxy in front would mean a unit this deployment does not have.

    SIGTERM is handled rather than defaulted: systemd stops the unit with it, and a handler that shuts
    the sockets down lets in-flight requests finish instead of severing them mid-response.
    """
    api = Api(db, signer)
    servers = []
    for host, port, cert, key in listeners:
        try:
            servers.append(_bind(host, port, api, cert, key))
        except OSError as exc:
            print(f"FATAL: 无法绑定 {host}:{port}（{exc.strerror}），端口被占用或权限不足", file=sys.stderr)
            for bound in servers:
                bound.server_close()
            raise SystemExit(78) from exc
        llm.event("INFO", "listen", host=host, port=port, tls=bool(cert))

    stop = threading.Event()

    def _on_signal(signum, _frame):  # noqa: ANN001 - signal handlers get (signum, frame)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    threads = [
        threading.Thread(target=server.serve_forever, name=f"listen-{server.server_port}", daemon=True)
        for server in servers
    ]
    for thread in threads:
        thread.start()

    try:
        while not stop.wait(1.0):
            pass
    finally:
        for server in servers:
            server.shutdown()
        for server in servers:
            server.server_close()
        llm.event("INFO", "stopped", listeners=len(servers))


def _parse_listener(spec: str, default_host: str) -> tuple[str, int, str | None, str | None]:
    """`PORT`, `PORT:CERT:KEY`, `HOST:PORT:CERT:KEY` — one flag instead of four.

    A listener without a certificate is plain http, which is only ever correct on loopback: the
    television pins a CA and the parent page carries a session cookie, so an unencrypted socket on a
    public address would be both broken and dangerous.
    """
    parts = spec.split(":")
    if parts[0].isdigit():
        host, port, rest = default_host, int(parts[0]), parts[1:]
    else:
        if len(parts) < 2 or not parts[1].isdigit():
            raise SystemExit(f"--listen 看起来不像 HOST:PORT[:CERT:KEY]：{spec!r}")
        host, port, rest = parts[0], int(parts[1]), parts[2:]
    if not rest:
        return host, port, None, None
    if len(rest) == 2:
        return host, port, rest[0] or None, rest[1] or None
    raise SystemExit(f"--listen 看起来不像 HOST:PORT[:CERT:KEY]：{spec!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the quiz-for-time API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--tls-cert", default=None, help="PEM chain; the television pins its CA (tools/make_ca.sh)")
    parser.add_argument("--tls-key", default=None, help="PEM private key for --tls-cert")
    parser.add_argument(
        "--listen",
        action="append",
        default=[],
        metavar="PORT[:CERT:KEY]",
        help="bind an extra socket in this same process; PORT alone means plain http",
    )
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="refuse to start without DEEPSEEK_API_KEY (the deployed unit passes this; local runs do not)",
    )
    args = parser.parse_args(argv)

    # Checked first: a missing key is a configuration error, and finding out about it when the first
    # question is requested would mean a service that looks healthy and does nothing. Exit 78 is
    # EX_CONFIG — systemd's Restart=on-failure would otherwise restart it forever, five seconds apart.
    if args.require_llm:
        llm.require_key()

    if bool(args.tls_cert) != bool(args.tls_key):
        print("--tls-cert and --tls-key go together", file=sys.stderr)
        return 2

    try:
        db = Database(args.dsn) if args.dsn else default_db()
        if args.dsn:
            db.connect(init_schema=True)
    except Exception as exc:  # noqa: BLE001 - the reason has to be readable in `systemctl status`
        print(f"FATAL: 数据库连接失败（{type(exc).__name__}），检查 QUIZ_DSN 与 MySQL", file=sys.stderr)
        return 78

    if args.listen:
        listeners = [_parse_listener(spec, args.host) for spec in args.listen]
    else:
        listeners = [(args.host, args.port, args.tls_cert, args.tls_key)]

    if not DEV_MODE:
        print("QUIZ_DEV is off: the now_ms override is disabled", file=sys.stderr)
    llm.event(
        "INFO",
        "startup",
        model=llm.model(),
        key_present=llm.key_present(),
        ports=",".join(str(port) for _, port, _, _ in listeners),
        db=db.dsn.split(":", 1)[0],
    )
    serve(listeners, db, build_signer())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
