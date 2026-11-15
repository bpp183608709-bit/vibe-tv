"""End-to-end acceptance for P1: serve, grade, sign, verify — over real HTTP.

Run:  .venv/bin/python server/tools/acceptance.py

What it does, in order:

  1. builds a throwaway database (unique filename under server/local/, because this sandbox refuses to
     delete files, so "fresh" means "new name" rather than "cleaned up");
  2. imports the curriculum tree and generates the seed bank, in-process;
  3. mints an ES256 key pair and starts the API as a subprocess, signing real tickets;
  4. walks the ten cases below through HTTP and verifies the tickets locally, the way the TV would;
  5. stops the server and reports one line per case.

The cases are the ones that would actually bite: the pass line, the cooldown, the daily ceiling, a
replayed ticket, an edited ticket, a ticket from another device, a double submit, equivalent spellings
of a fraction, and yesterday's ticket.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app import es256, grade as grading, limits, ticket  # noqa: E402
from server.app.db import Database  # noqa: E402
from server.tools import gen_seed_questions, import_curriculum  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "local"
DAY = "2026-11-11"
NEXT_DAY = "2026-11-12"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, bool(condition), detail if not condition else ""))


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def pair() -> str:
    status, body = http("POST", "/v1/pair/start", {"app_version": "acceptance"})
    assert status == 200, (status, body)
    return body["device_id"]


def start(now_ms: int | None = None) -> dict:
    """`pair/start`, keeping the code as well as the id: the parent-page cases need both."""
    body: dict = {"app_version": "acceptance", "name": "acceptance"}
    if now_ms is not None:
        body["now_ms"] = now_ms
    status, payload = http("POST", "/v1/pair/start", body)
    assert status == 200, (status, payload)
    return payload


class Parent:
    """A phone: an account, and the session cookie a browser would keep for it.

    Separate from `http()` because the whole authorisation model is that cookie, so a test that
    forgets to send it is testing a different endpoint than the one it thinks.
    """

    #: A counter, so each phone gets its own "address". In a real house every phone is behind one
    #: router and shares one, which is why the per-source limit is five an hour — a suite that made
    #: twenty accounts from one address would be testing the limit, not the accounts.
    _n = 0

    def __init__(self, source: str | None = None) -> None:
        self.cookie: str | None = None
        if source is None:
            Parent._n += 1
            source = f"10.0.0.{Parent._n}"
        self.source = source

    def register(self, email: str, password: str = "correct horse battery",
                 question: str = "妈妈的生日是哪天？", answer: str = "三月九日") -> tuple[int, dict]:
        """Sign up the way the page does — challenge first, then the form — without the 1.5 s wait.

        The challenge carries its own issue time; `now_ms` is the dev-only clock the server honours
        with QUIZ_DEV=1, so the form can arrive a hair later than a person could type it without the
        suite sitting still for two seconds per account.
        """
        status, body = self.call("GET", "/v1/auth/challenge")
        assert status == 200, (status, body)
        return self.call("POST", "/v1/auth/register", {
            "email": email, "password": password, "question": question, "answer": answer,
            "challenge": body["challenge"], "now_ms": body["issued_at"] + 2_000,
        })

    def login(self, email: str, password: str) -> tuple[int, dict]:
        return self.call("POST", "/v1/auth/login", {"email": email, "password": password})

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        if self.cookie:
            request.add_header("Cookie", f"aq={self.cookie}")
        # What the server rate limits on; `_source()` reads the first hop of this header.
        request.add_header("X-Forwarded-For", self.source)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
                issued = response.headers.get("Set-Cookie") or ""
                if issued.startswith("aq="):
                    self.cookie = issued.split(";")[0][3:]
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"raw": raw}


def serve(device_id: str, day: str = DAY) -> dict:
    status, body = http("POST", "/v1/quiz/next", {"device_id": device_id, "day_key": day})
    assert status == 200, (status, body)
    return body


def right(q: dict) -> str:
    if q["qtype"] == "choice":
        return next(o["label"] for o in q["options"] if o["correct"])
    return q["accepted"][0]


def wrong(q: dict) -> str:
    if q["qtype"] == "choice":
        return next(o["label"] for o in q["options"] if not o["correct"])
    return "X"


def submit(device_id: str, session: dict, correct_count: int, day: str = DAY) -> dict:
    answers = []
    for index, question in enumerate(session["questions"]):
        chosen = right(question) if index < correct_count else wrong(question)
        answers.append({"question_id": question["id"], "chosen": chosen, "flagged_unknown": False})
    status, body = http("POST", f"/v1/quiz/{session['session_id']}/grade",
                        {"device_id": device_id, "answers": answers, "now_ms": None})
    assert status == 200, (status, body)
    return body


def main() -> int:
    LOCAL.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    db_path = LOCAL / f"acceptance-{stamp}.sqlite3"
    dsn = f"sqlite:{db_path}"

    # ---- 1/2. a fresh database, seeded ----
    db = Database(dsn)
    db.connect(init_schema=True)
    tree = json.loads((ROOT / "content/curriculum/grade6-up.json").read_text(encoding="utf-8"))
    import_curriculum.import_tree(db, tree)
    if db.scalar("SELECT COUNT(*) FROM question", default=0) == 0:
        gen_seed_questions.main(["--per-topic", "5", "--dsn", dsn])
    db.close()

    # ---- 3. key pair and server ----
    private_key, public_key = es256.generate()
    key_path = LOCAL / f"acceptance-key-{stamp}.pem"
    key_path.write_bytes(es256.private_to_pem(private_key, public_key))
    verifier = ticket.Es256Signer(public_key_pem=es256.public_to_pem(public_key))

    env = {
        **os.environ,
        "QUIZ_DSN": dsn,
        "QUIZ_DEV": "1",
        "QUIZ_TICKET_KEY": str(key_path),
    }
    server = subprocess.Popen(
        [sys.executable, "-m", "server.app.api", "--port", str(PORT)],
        cwd=str(ROOT.parent), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                if http("GET", "/health")[0] == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.2)
        else:
            print("server did not start", file=sys.stderr)
            return 2

        # ---- case 1: a clean pass yields a spendable ticket ----
        device = pair()
        session = serve(device)
        check("group has 5 questions", len(session["questions"]) == 5, str(len(session["questions"])))
        probe = Database(dsn)
        probe.connect()
        subject_rows = probe.query(
            "SELECT DISTINCT b.subject_id FROM question q JOIN textbook b ON b.id = q.textbook_id"
            f" WHERE q.id IN ({','.join('?' for _ in session['questions'])})",
            [q["id"] for q in session["questions"]],
        )
        probe.close()
        check("group spans at least 2 subjects (C5)", len(subject_rows) >= 2, f"{len(subject_rows)}")
        graded = submit(device, session, correct_count=5)
        check("five right passes", graded["passed"] is True, json.dumps(graded))
        check("pass is worth 30 minutes", graded["award_ms"] == limits.AWARD_MS, str(graded["award_ms"]))
        parsed = ticket.verify(
            graded["ticket"], verifier, device_id=device, day_key=DAY, spent_jti=set()
        )
        check("ticket verifies on the TV", parsed.ms == limits.AWARD_MS and parsed.did == device)

        # ---- case 5: the same string again is spent ----
        try:
            ticket.verify(graded["ticket"], verifier, device_id=device, day_key=DAY,
                          spent_jti={parsed.jti})
            check("replayed ticket is refused", False, "accepted a spent jti")
        except ticket.TicketError as exc:
            check("replayed ticket is refused", exc.reason == "spent", exc.reason)

        # ---- case 6: an edited award cannot even be signed ----
        forged = ticket.Ticket(jti=parsed.jti, did=device, day=DAY, ms=60 * 60_000,
                               iat=parsed.iat, exp=parsed.exp)
        try:
            ticket.verify(ticket.issue(forged, ticket.Es256Signer(
                private_key_pem=es256.private_to_pem(private_key, public_key))),
                verifier, device_id=device, day_key=DAY)
            check("a 60-minute award is refused", False, "accepted a forged amount")
        except ticket.TicketError as exc:
            check("a 60-minute award is refused", exc.reason == "bad-award", exc.reason)

        # ---- case 7: another device cannot spend it ----
        other = pair()
        try:
            ticket.verify(graded["ticket"], verifier, device_id=other, day_key=DAY)
            check("ticket is bound to its device", False, "accepted on another device")
        except ticket.TicketError as exc:
            check("ticket is bound to its device", exc.reason == "wrong-device", exc.reason)

        # ---- case 10: yesterday's ticket is void ----
        try:
            ticket.verify(graded["ticket"], verifier, device_id=device, day_key=NEXT_DAY)
            check("ticket dies at the day boundary", False, "accepted on the next day")
        except ticket.TicketError as exc:
            check("ticket dies at the day boundary", exc.reason == "wrong-day", exc.reason)

        # ---- case 8: a retried submit counts once and keeps its jti ----
        again = submit(device, session, correct_count=5)
        check("retry is flagged as a replay", again.get("replayed") is True, json.dumps(again))
        check("retry returns the same ticket",
              ticket.verify(again["ticket"], verifier, device_id=device, day_key=DAY).jti == parsed.jti)
        probe = Database(dsn)
        probe.connect()
        successes = probe.scalar(
            "SELECT success_count FROM daily_state WHERE device_id = ? AND day_key = ?", (device, DAY))
        tickets = probe.scalar("SELECT COUNT(*) FROM ticket WHERE device_id = ?", (device,))
        probe.close()
        check("retry did not add a success", successes == 1, f"success_count={successes}")
        check("retry did not mint a second ticket", tickets == 1, f"tickets={tickets}")

        # ---- case 2: four of five still passes ----
        device2 = pair()
        graded2 = submit(device2, serve(device2), correct_count=4)
        check("four of five passes", graded2["passed"] is True, json.dumps(graded2))
        check("four of five is still 30 minutes", graded2["award_ms"] == limits.AWARD_MS)
        check("a pass that got one wrong keeps it for review",
              len(graded2.get("review") or []) == 1 and graded2.get("cooldown_seconds") == 0,
              json.dumps({"review": graded2.get("review"), "cooldown": graded2.get("cooldown_seconds")}))

        # ---- case 3: three of five fails and starts the cooldown ----
        device3 = pair()
        session3 = serve(device3)
        graded3 = submit(device3, session3, correct_count=3)
        check("three of five fails", graded3["passed"] is False, json.dumps(graded3))
        check("a failure awards nothing", graded3["award_ms"] == 0)
        # ---- the result page's 错题本, and the cooldown the lock screen counts down (2026-11-12) ----
        review3 = graded3.get("review") or []
        check("a failed group lists its wrong answers",
              len(review3) == 2 and all(item.get("stem") and item.get("chosen") and item.get("correct")
                                        for item in review3),
              json.dumps(review3, ensure_ascii=False)[:300])
        check("every reviewed item is a wrong one",
              all(item["chosen"] != item["correct"] for item in review3),
              json.dumps(review3, ensure_ascii=False)[:300])
        check("a failed group reports the cooldown it started",
              graded3.get("cooldown_seconds") == limits.COOLDOWN_MS // 1000,
              graded3.get("cooldown_seconds"))
        check("a clean pass has nothing to review and no cooldown",
              graded.get("review") == [] and graded.get("cooldown_seconds") == 0,
              json.dumps({"review": graded.get("review"), "cooldown": graded.get("cooldown_seconds")}))
        # A submission whose response was lost is repeated as the same session; the page it draws must
        # be the same one, review and cooldown included.
        replay3 = submit(device3, session3, correct_count=3)
        check("a replay carries the same wrong answers",
              replay3.get("review") == review3, json.dumps(replay3.get("review"), ensure_ascii=False)[:300])
        check("a replay still reports the cooldown",
              (replay3.get("cooldown_seconds") or 0) > 0, replay3.get("cooldown_seconds"))
        status, body = http("POST", "/v1/quiz/next", {"device_id": device3, "day_key": DAY})
        check("cooldown blocks the next group",
              status == 429 and body.get("error") == "cooldown", f"{status} {body}")

        # ---- case 4: the third pass is the last one ----
        device4 = pair()
        for index in range(3):
            outcome = submit(device4, serve(device4), correct_count=5)
            check(f"pass {index + 1} of 3 is awarded", outcome["award_ms"] == limits.AWARD_MS,
                  json.dumps(outcome))
        status, body = http("POST", "/v1/quiz/next", {"device_id": device4, "day_key": DAY})
        check("a fourth group is refused",
              status == 409 and body.get("error") == "quota-exhausted", f"{status} {body}")

        # ---- case 9: 0.75 and 3/4 are the same answer ----
        probe = Database(dsn)
        probe.connect()
        # Any question with more than one accepted spelling is, by construction, a fraction that also
        # has a terminating decimal — exactly the pair this case is about.
        row = probe.query_one(
            "SELECT question_id FROM question_answer GROUP BY question_id"
            " HAVING COUNT(*) >= 2 LIMIT 1")
        check("the bank stores equivalent spellings", row is not None, "no question has two answers")
        accepted = probe.query("SELECT text_raw, cmp_kind FROM question_answer WHERE question_id = ?",
                               (row["question_id"],)) if row else []
        probe.close()
        question = {"id": row["question_id"], "qtype": "fill"} if row else {"id": 0, "qtype": "fill"}
        fraction_form = next((a["text_raw"] for a in accepted if "/" in a["text_raw"]), None)
        decimal_form = next((a["text_raw"] for a in accepted if "." in a["text_raw"]), None)
        check("a fraction equals its decimal",
              bool(fraction_form and decimal_form)
              and grading.answers_match(question, accepted, decimal_form)
              and grading.answers_match(question, accepted, fraction_form),
              f"accepted={accepted}")
        check("a wrong number is still wrong",
              not grading.answers_match(question, accepted, "9999"))

        # ---- case 10 onwards: the parent page (P3) ----
        #
        # One fixed moment for the whole block, because three of these cases are about time: a code is
        # good for ten minutes, and the TV's half-hourly beat is what makes a settings change stick.
        # `now_ms` is honoured only with QUIZ_DEV=1 (the server refuses it otherwise), which is why the
        # harness sets that variable.
        T = 1_770_000_000_000

        # The bound television gets a group first, so the wrongbook has something to show. Three wrong
        # answers out of five is also a failure, which the week report should count as an attempt.
        pairing = start(T)
        bound = pairing["device_id"]
        submit(bound, serve(bound, DAY), 2, DAY)

        phone = Parent()
        status, body = phone.register("dad@example.com")
        check("an account is created and signed in",
              status == 201 and phone.cookie is not None and body["account"]["email"] == "dad@example.com",
              f"{status} {body}")
        status, body = phone.call(
            "POST", "/v1/h5/pair", {"pair_code": pairing["pair_code"], "now_ms": T + 60_000}
        )
        check("the code on the TV binds the phone",
              status == 200 and body["device"]["device_id"] == bound, f"{status} {body}")

        status, _ = Parent().call("GET", "/v1/h5/summary")
        check("a phone with no cookie gets nowhere", status == 401, str(status))

        # A second account, for the cases that need somebody signed in without a television.
        stranger = Parent()
        status, body = stranger.register("stranger@example.com")
        check("a second account can be made", status == 201, f"{status} {body}")
        status, body = stranger.call("POST", "/v1/h5/pair", {"pair_code": "ZZZZZZ"})
        check("a wrong code is refused",
              status == 403 and body.get("error") == "wrong-code", f"{status} {body}")
        status, body = stranger.call("GET", f"/v1/h5/summary?day_key={DAY}&device_id={bound}")
        check("somebody else's TV is out of reach",
              status == 403 and body.get("error") == "not-yours", f"{status} {body}")

        # ---- the account itself: duplicate email, weak password, sign-in ----
        status, body = Parent().register("DAD@example.com", "another one entirely")
        check("one email is one account, whatever the capitals",
              status == 409 and body.get("error") == "email-taken", f"{status} {body}")
        status, body = Parent().register("short@example.com", "abc")
        check("a short password is refused", status == 400 and body.get("error") == "password-short",
              f"{status} {body}")
        status, body = Parent().register("not-an-email", "correct horse battery")
        check("a malformed email is refused", status == 400 and body.get("error") == "email-bad",
              f"{status} {body}")

        # The 1.5 s floor: a challenge answered at once was not typed by a person.
        status, body = http("GET", "/v1/auth/challenge")
        status, body = Parent().call("POST", "/v1/auth/register", {
            "email": "fast@example.com", "password": "correct horse battery",
            "challenge": body["challenge"], "now_ms": body["issued_at"] + 100,
        })
        check("a registration submitted at once is refused",
              status == 400 and body.get("error") == "challenge-bad", f"{status} {body}")

        # The per-address ceiling, on a source of its own so the rest of the suite is unaffected.
        busy = Parent(source="10.9.9.9")
        codes = []
        for index in range(6):
            status, body = busy.register(f"busy{index}@example.com")
            codes.append(status)
        check("five accounts an hour from one address, then a refusal",
              codes[:5] == [201] * 5 and codes[5] == 429, str(codes))

        status, body = Parent().login("dad@example.com", "wrong password")
        check("a wrong password is refused", status == 403, f"{status} {body}")
        status, body = Parent().login("nobody@example.com", "correct horse battery")
        check("an unknown email looks the same as a wrong password", status == 403, f"{status} {body}")
        again = Parent()
        status, body = again.login("dad@example.com", "correct horse battery")
        check("a right password signs in", status == 200 and again.cookie is not None, f"{status} {body}")
        status, body = again.call("GET", "/v1/auth/me")
        check("and the session knows which account it is",
              status == 200 and body["account"]["email"] == "dad@example.com"
              and len(body["devices"]) == 1, f"{status} {body}")
        status, body = again.call("POST", "/v1/auth/logout", {})
        check("signing out ends this session only", status == 200, f"{status} {body}")
        status, _ = again.call("GET", "/v1/auth/me")
        check("and the cookie is dead", status == 401, str(status))
        status, body = phone.call("GET", "/v1/auth/me")
        check("the other phone is still signed in", status == 200, f"{status} {body}")

        # ---- the password: what it must be, changing it, and getting it back without mail
        rules = Parent(source="10.8.8.8")
        status, body = rules.register("rules@example.com", "8181818181")
        check("an all-digits password is refused",
              status == 400 and body.get("error") == "password-weak", f"{status} {body}")

        status, body = rules.call("GET", "/v1/auth/challenge")
        status, body = rules.call("POST", "/v1/auth/register", {
            "email": "noq@example.com", "password": "correct horse battery",
            "challenge": body["challenge"], "now_ms": body["issued_at"] + 2_000})
        check("a registration with no question is allowed", status == 201, f"{status} {body}")
        status, body = rules.call("POST", "/v1/auth/question", {"email": "noq@example.com"})
        check("but that account has no way back, and is told so",
              status == 409 and body.get("error") == "no-question", f"{status} {body}")

        # Half a credential is refused either way round: the page checks the pair before posting,
        # and the server refuses it too rather than storing something unusable.
        status, body = rules.call("GET", "/v1/auth/challenge")
        status, body = rules.call("POST", "/v1/auth/register", {
            "email": "half@example.com", "password": "correct horse battery", "answer": "302",
            "challenge": body["challenge"], "now_ms": body["issued_at"] + 2_000})
        check("an answer without a question is refused",
              status == 400 and body.get("error") == "answer-orphan", f"{status} {body}")
        status, body = rules.call("GET", "/v1/auth/challenge")
        status, body = rules.call("POST", "/v1/auth/register", {
            "email": "half2@example.com", "password": "correct horse battery",
            "question": "门牌号？", "answer": "   ",
            "challenge": body["challenge"], "now_ms": body["issued_at"] + 2_000})
        check("a question with a blank answer is refused",
              status == 400 and body.get("error") == "answer-short", f"{status} {body}")

        status, body = rules.register("rules@example.com")
        check("an account arrives with its question", status == 201, f"{status} {body}")

        status, body = rules.call("POST", "/v1/auth/question", {"email": "RULES@example.com"})
        check("the question comes back, the address is not case-sensitive",
              status == 200 and body.get("question") == "妈妈的生日是哪天？", f"{status} {body}")
        status, body = rules.call("POST", "/v1/auth/question", {"email": "ghost@example.com"})
        check("an address with no account says so",
              status == 404 and body.get("error") == "no-account", f"{status} {body}")

        status, body = rules.call("POST", "/v1/auth/recover", {
            "email": "rules@example.com", "answer": "不知道", "password": "Apricot-wall-7781"})
        check("a wrong answer resets nothing",
              status == 403 and body.get("error") == "bad-answer", f"{status} {body}")

        # The answer is matched after trimming and case-folding, but a weak password is still a
        # weak password — and being refused must not spend the one good answer.
        status, body = rules.call("POST", "/v1/auth/recover", {
            "email": "rules@example.com", "answer": "  三月九日 ", "password": "8282828282"})
        check("a right answer with a bad password is refused, and the answer is not spent",
              status == 400 and body.get("error") == "password-weak", f"{status} {body}")

        status, body = rules.call("POST", "/v1/auth/recover", {
            "email": "rules@example.com", "answer": "  三月九日 ", "password": "Apricot-wall-7781"})
        check("the same answer then goes through",
              status == 200 and body.get("ok"), f"{status} {body}")
        status, body = rules.call("GET", "/v1/auth/me")
        check("and every session it had is gone", status == 401, f"{status} {body}")
        status, body = rules.login("rules@example.com", "correct horse battery")
        check("the old password is dead", status == 403, f"{status} {body}")
        status, body = rules.login("rules@example.com", "Apricot-wall-7781")
        check("the reset password works", status == 200, f"{status} {body}")

        status, body = rules.call("POST", "/v1/auth/password",
                                  {"old_password": "nope", "password": "Tangerine-line-2048"})
        check("changing the password needs the current one",
              status == 403 and body.get("error") == "bad-credentials", f"{status} {body}")
        status, body = rules.call("POST", "/v1/auth/password",
                                  {"old_password": "Apricot-wall-7781",
                                   "password": "Apricot-wall-7781"})
        check("the same password is not a change",
              status == 400 and body.get("error") == "password-same", f"{status} {body}")
        status, body = rules.call("POST", "/v1/auth/password",
                                  {"old_password": "Apricot-wall-7781",
                                   "password": "Tangerine-line-2048"})
        check("a real change goes through and signs every phone out",
              status == 200 and body.get("signed_out"), f"{status} {body}")
        status, body = rules.call("GET", "/v1/auth/me")
        check("so the changed phone signs in again", status == 401, f"{status} {body}")
        status, body = rules.login("rules@example.com", "Tangerine-line-2048")
        check("with the new password", status == 200, f"{status} {body}")

        # An account that predates questions has none, and would have no way back in. Setting one
        # while signed in is the only repair, and it must take effect immediately.
        status, body = rules.call("POST", "/v1/auth/question/set", {
            "question": "家里第一只狗叫什么？", "answer": "小黑"})
        check("the question can be set while signed in",
              status == 200 and body.get("question") == "家里第一只狗叫什么？", f"{status} {body}")
        status, body = rules.call("POST", "/v1/auth/question",
                                  {"email": "rules@example.com"})
        check("and that is the one read back", body.get("question") == "家里第一只狗叫什么？",
              f"{status} {body}")
        status, body = Parent(source="10.8.8.8").call("POST", "/v1/auth/recover", {
            "email": "rules@example.com", "answer": "小黑", "password": "Zucchini-bell-5510"})
        check("an anonymous phone can then reset with the new answer",
              status == 200 and body.get("ok"), f"{status} {body}")
        status, body = rules.call("GET", "/v1/auth/me")
        check("which signs the old session out", status == 401, f"{status} {body}")

        # Expiry, before the lock case below: the throttle is per source, and every case here comes
        # from 127.0.0.1, so a lock would hide the rest of the block.
        stale = start(T)["pair_code"]
        status, body = stranger.call(
            "POST", "/v1/h5/pair", {"pair_code": stale, "now_ms": T + 11 * 60_000}
        )
        check("a code older than ten minutes is refused",
              status == 403 and body.get("error") == "code-expired", f"{status} {body}")

        # Renewal keeps the television: `pair/start` would register a second one and lose today.
        spare = start(T)["device_id"]
        status, body = http("POST", "/v1/pair/renew", {"device_id": spare, "now_ms": T + 700_000})
        check("renewing a code keeps the same TV",
              status == 200 and body.get("device_id") == spare, f"{status} {body}")

        # A bound television does offer another code now: that is how the second parent joins without
        # rebuilding the device (docs/ACCOUNT.md, A2).
        status, body = http("POST", "/v1/pair/renew", {"device_id": bound})
        check("a paired TV can still hand out a code for a second phone",
              status == 200 and body.get("pair_code") and body.get("device_id") == bound,
              f"{status} {body}")

        # Three failures recorded, then the door shuts: the attempt after the third gets the 429.
        for wrong in ("ZZZZZY", "ZZZZZX", "ZZZZZW"):
            status, body = stranger.call("POST", "/v1/h5/pair", {"pair_code": wrong})
        check("three wrong codes lock the source", status == 429, f"{status} {body}")

        # ---- settings: version, the TV's poll, and who wrote last ----
        status, body = phone.call("GET", f"/v1/h5/config?device_id={bound}")
        check("a fresh television has the default limits",
              status == 200 and body["version"] == 0 and body["config"]["daily_minutes"] == 60,
              f"{status} {body}")

        status, body = phone.call("PUT", f"/v1/h5/config?device_id={bound}", {"config": {"daily_minutes": 45}})
        check("writing a setting bumps the version",
              status == 200 and body["version"] == 1 and body["config"]["daily_minutes"] == 45,
              f"{status} {body}")

        status, body = http("GET", f"/v1/config?device_id={bound}&version=0")
        check("the television is told the limits changed",
              body.get("changed") is True and body["config"]["daily_minutes"] == 45, str(body))

        status, body = http("GET", f"/v1/config?device_id={bound}&version=1")
        check("and is told nothing when its copy is current",
              body.get("changed") is False, str(body))

        # The television's own settings page: it has applied v1, and now holds 90 minutes instead.
        status, body = http("POST", "/v1/heartbeat", {
            "device_id": bound, "day_key": DAY, "used_ms": 1_200_000, "config_version": 1,
            "config": {"daily_minutes": 90, "session_minutes": 30, "interval_minutes": 20},
        })
        check("a TV that moved a number is taken at its word",
              body["config"]["daily_minutes"] == 90 and body["config_version"] == 2, str(body))

        # A television that is behind must not write back: otherwise the half-hourly beat would undo
        # whatever the parent changed on the phone, and nobody would know why.
        status, body = http("POST", "/v1/heartbeat", {
            "device_id": bound, "day_key": DAY, "used_ms": 1_320_000, "config_version": 1,
            "config": {"daily_minutes": 60, "session_minutes": 30, "interval_minutes": 20},
        })
        check("a television that is behind does not undo the phone",
              body["config"]["daily_minutes"] == 90 and body["config_version"] == 2, str(body))

        probe = Database(dsn)
        probe.connect()
        sources = [row["source"] for row in probe.query(
            "SELECT source FROM config_audit WHERE device_id = ? ORDER BY id", (bound,))]
        probe.close()
        check("both ends' writes are in the audit", sources == ["h5", "tv"], str(sources))

        # ---- what the parent reads ----
        status, body = phone.call("GET", f"/v1/h5/summary?day_key={DAY}&device_id={bound}")
        check("the phone sees the viewing time the television reported",
              body["today"]["used_ms"] == 1_320_000, str(body["today"]))

        status, body = phone.call("GET", f"/v1/h5/wrongbook?day_key={DAY}&days=7&device_id={bound}")
        items = body.get("items", [])
        check("the wrong answers come back with the right answer",
              len(items) == 3 and all(i.get("answer_text") for i in items), str(len(items)))
        check("and with what the child picked",
              all(i.get("chosen_text") is not None for i in items), str(items[:1]))

        status, body = phone.call("GET", f"/v1/h5/report?day_key={DAY}&device_id={bound}")
        check("the week has seven days", len(body.get("days", [])) == 7, str(len(body.get("days", []))))
        check("and counts the failed group",
              body["attempts"] == 1 and body["passed"] == 0, str(body))

        # ---- a second parent on the same television (docs/ACCOUNT.md A2) ----
        status, body = http("POST", "/v1/pair/renew", {"device_id": bound, "now_ms": T + 900_000})
        code_for_mom = body.get("pair_code")
        mom = Parent()
        status, body = mom.register("mom@example.com")
        check("the second parent has an account", status == 201, f"{status} {body}")
        status, body = mom.call("POST", "/v1/h5/pair",
                                {"pair_code": code_for_mom, "now_ms": T + 901_000})
        check("and joins the same television instead of rebuilding it",
              status == 200 and body["device"]["device_id"] == bound, f"{status} {body}")
        status, body = phone.call("GET", f"/v1/h5/summary?day_key={DAY}&device_id={bound}")
        check("the first parent still sees it", status == 200, f"{status} {body}")

        # ---- unbinding ----
        # 解绑 is about this account, not the television. This used to write `revoked_at` on the device
        # row, which turned every television request into 403 (questions, heartbeat, settings) and left
        # the settings page showing 已连接 with nobody behind it — the pairing page then never offered
        # a new code. Found on the television on 2026-11-12, after the flow had gone green in review.
        status, body = phone.call("POST", "/v1/h5/unpair", {"device_id": bound})
        check("unbinding answers ok", status == 200, f"{status} {body}")
        status, body = phone.call("GET", "/v1/auth/me")
        check("the account is still signed in, just without a television",
              status == 200 and body["devices"] == [], f"{status} {body}")
        status, body = phone.call("GET", f"/v1/h5/summary?day_key={DAY}&device_id={bound}")
        check("and that television is out of reach", status == 403, f"{status} {body}")
        status, body = http("GET", f"/v1/pair/status?device_id={bound}")
        check("the television stays paired while the second parent holds it",
              body.get("bound") is True, str(body))
        status, body = http("GET", f"/v1/config?device_id={bound}")
        check("and the television itself keeps working", status == 200, f"{status} {body}")

        status, body = mom.call("POST", "/v1/h5/unpair", {"device_id": bound})
        status, body = http("GET", f"/v1/pair/status?device_id={bound}")
        check("the last account leaving is what unplugs the TV", body.get("bound") is False, str(body))

        # ---- a phone from before accounts existed (A5) ----
        # 2026-11-14: the pairing flow used to hand the phone a device token. Those cookies still sit
        # in real browsers, and sending them back to the TV for a new code would be a regression, so
        # they get one form that turns them into an account.
        legacy = start(T)["device_id"]
        legacy_token = "legacy-token-from-the-suite"
        probe = Database(dsn)
        probe.connect()
        probe.execute("UPDATE device SET token_hash = ?, bound_at = CURRENT_TIMESTAMP WHERE id = ?",
                      (hashlib.sha256(legacy_token.encode("utf-8")).hexdigest(), legacy))
        probe.close()

        old_phone = Parent()
        old_phone.cookie = legacy_token
        status, body = old_phone.call("GET", "/v1/auth/me")
        check("a pre-account cookie is offered the upgrade",
              status == 200 and body.get("legacy") is True
              and body["device"]["device_id"] == legacy, f"{status} {body}")

        status, body = old_phone.call("GET", "/v1/auth/challenge")
        status, body = old_phone.call("POST", "/v1/auth/claim", {
            "email": "grandma@example.com", "password": "correct horse battery",
            # The upgrade form is the registration form, question and all: an account without one
            # could never be recovered, and this is the last chance to ask for it.
            "question": "家里第一只狗叫什么？", "answer": "小黑",
            "challenge": body["challenge"], "now_ms": body["issued_at"] + 2_000,
        })
        check("upgrading keeps the television", status == 200
              and body["device"]["device_id"] == legacy, f"{status} {body}")
        check("and hands back a session cookie, not the old token",
              old_phone.cookie not in (None, legacy_token), str(old_phone.cookie))
        status, body = old_phone.call("GET", f"/v1/h5/summary?day_key={DAY}&device_id={legacy}")
        check("the upgraded phone works as an account", status == 200, f"{status} {body}")
        probe = Database(dsn)
        probe.connect()
        leftover = probe.scalar("SELECT token_hash FROM device WHERE id = ?", (legacy,))
        probe.close()
        check("and the device token is cleared", leftover is None, str(leftover))

    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    print(f"\nacceptance: {len(results) - len(failed)}/{len(results)} passed  (db {db_path.name})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
