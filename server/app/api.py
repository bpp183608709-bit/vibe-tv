"""The HTTP surface: standard library, no framework.

    GET  /health
    POST /v1/pair/start          register the TV, get a six-character code for the parent page
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
    GET  /v1/h5/report           seven days: attempts, passes, the three worst topics
    POST /v1/h5/unpair           drop this phone's cookie

Why `http.server` and not FastAPI: one process, a handful of JSON routes, no streaming, no websockets,
and a 2 GB box that should not be running an ASGI server plus a process supervisor. The part a framework
would buy — validation and routing — is forty lines here, and the deployment story becomes "python -m
server.app.api".

**Not production-hardened, and it says so.** There is no rate limiting outside pairing, and no device
authentication beyond the `device_id` in the body. That last one is the largest hole left: knowing
another TV's `device_id` lets you submit answers for it. It does not let you mint minutes — tickets are
signed for the device they were earned on, and the TV re-checks `did` — but it does let you burn
someone's attempts. The fix is a device keypair, and it belongs with the rest of the pairing work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from server.app import grade as grading
from server.app import limits, picker, ticket
from server.app.db import Database, default_db
from server.app.grade import GradeError
from server.app.h5 import H5, PAIR_ALPHABET, PAIR_CODE_LEN, PAIR_TTL_MS, SESSION_COOKIE
from server.app.ids import new_ulid

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

    # ---- helpers ----

    def _device(self, device_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM device WHERE id = ?", (device_id,))
        if row is None:
            raise grading.GradeError("unknown-device", 404, "pair the TV first")
        if row.get("revoked_at"):
            raise grading.GradeError("device-revoked", 403)
        return row

    def _now(self, body: dict) -> int:
        import time

        if DEV_MODE and isinstance(body.get("now_ms"), int):
            return int(body["now_ms"])
        return int(time.time() * 1000)

    # ---- routes ----

    def health(self, body: dict, now_ms: int) -> dict:
        return {"ok": True, "dsn": self.db.dsn.split(":")[0], "questions": self.db.scalar(
            "SELECT COUNT(*) FROM question", default=0)}

    def pair_start(self, body: dict, now_ms: int) -> dict:
        """Make a code for the TV to show. A TV that re-opens the pairing page keeps its device id:
        minting a new one per visit would split its counters across rows, and the daily caps are
        enforced per row — three visits would buy three sets of three groups."""
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LEN))
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        expires = limits.to_iso(now_ms + PAIR_TTL_MS)
        now_iso = limits.to_iso(now_ms)

        existing = body.get("device_id")
        if isinstance(existing, str) and existing:
            row = self.db.query_one("SELECT * FROM device WHERE id = ?", (existing,))
            if row is not None and not row.get("revoked_at"):
                self.db.execute(
                    "UPDATE device SET pair_code_hash = ?, pair_expires_at = ?, app_version = ?,"
                    " last_seen_at = ? WHERE id = ?",
                    (code_hash, expires, body.get("app_version") or row.get("app_version"),
                     now_iso, existing),
                )
                return {
                    "device_id": existing,
                    "pair_code": code,
                    "expires_at": expires,
                    "pair_ttl_seconds": PAIR_TTL_MS // 1000,
                    "bound": bool(row.get("bound_at")),
                }

        device_id = new_ulid(now_ms)
        self.db.execute(
            "INSERT INTO device (id, name, pair_code_hash, pair_expires_at, app_version, last_seen_at,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                body.get("name"),
                code_hash,
                expires,
                body.get("app_version"),
                now_iso,
                now_iso,
            ),
        )
        return {
            "device_id": device_id,
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
            questions = picker.pick_group(self.db, device_id, now_ms, difficulty)
        except picker.NotEnoughQuestions as exc:
            raise grading.GradeError("no-questions", 503, str(exc)) from exc

        session_id = new_ulid(now_ms)
        expires_at = now_ms + limits.SESSION_TTL_MS
        with self.db.tx() as cur:
            cur.insert(
                "INSERT INTO quiz_session (id, device_id, day_key, issued_at, expires_at, status,"
                " target_difficulty, question_ids, created_at) VALUES (?, ?, ?, ?, ?, 'issued', ?, ?, ?)",
                (
                    session_id, device_id, day_key, limits.to_iso(now_ms), limits.to_iso(expires_at),
                    difficulty, json.dumps([int(q["id"]) for q in questions]), limits.to_iso(now_ms),
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
            "questions": picker.payload(self.db, questions),
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


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # quieter default; one line per request
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

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

        def _send(self, status: int, payload: dict, headers: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
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
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            # The names carry no hash, so no long cache: a phone stuck on yesterday's JavaScript after
            # an update is the classic "works on my laptop".
            self.send_header("Cache-Control", "no-cache, must-revalidate")
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
                self._send(200, {"account": account, "devices": accounts.devices_of(account["id"])})
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
                self._send(200, accounts.set_question(account["id"], body))
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
                except ValueError:
                    days = 7
                self._send(200, api.h5.wrongbook(device_id, self._day_key(query, body, now_ms), days))
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

                if method == "GET" and path == "/health":
                    self._send(200, api.health(body, now_ms))
                elif method == "GET" and path == "/v1/quota":
                    self._send(200, api.quota(query, now_ms))
                elif method == "GET" and path == "/v1/pair/status":
                    self._send(200, api.h5.pair_status(query.get("device_id") or ""))
                elif method == "GET" and path == "/v1/config":
                    self._send(200, api.h5.tv_config(query))
                elif method == "POST" and path == "/v1/heartbeat":
                    self._send(200, api.h5.heartbeat(body, now_ms))
                elif method == "POST" and path == "/v1/pair/start":
                    self._send(200, api.pair_start(body, now_ms))
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


def serve(host: str, port: int, db: Database, signer, tls_cert: str | None = None, tls_key: str | None = None) -> None:
    api = Api(db, signer)
    server = ThreadingHTTPServer((host, port), make_handler(api))
    scheme = "http"
    if tls_cert and tls_key:
        # TLS here rather than in a reverse proxy: the box has an address and no domain, the
        # television pins a CA it ships with, and one process is one thing to restart (docs/QUIZ.md
        # §8). A proxy in front is still fine — this just means the wiki is not the only way to get
        # an https endpoint.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"quiz api on {scheme}://{host}:{port} (dsn={db.dsn})", file=sys.stderr)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the quiz-for-time API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--tls-cert", default=None, help="PEM chain; the television pins its CA (tools/make_ca.sh)")
    parser.add_argument("--tls-key", default=None, help="PEM private key for --tls-cert")
    args = parser.parse_args(argv)

    db = Database(args.dsn) if args.dsn else default_db()
    if args.dsn:
        db.connect(init_schema=True)
    if not DEV_MODE:
        print("QUIZ_DEV is off: the now_ms override is disabled", file=sys.stderr)
    if bool(args.tls_cert) != bool(args.tls_key):
        print("--tls-cert and --tls-key go together", file=sys.stderr)
        return 2
    serve(args.host, args.port, db, build_signer(), args.tls_cert, args.tls_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
