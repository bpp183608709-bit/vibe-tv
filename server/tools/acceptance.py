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

import argparse
import datetime
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app import curriculum, es256, grade as grading, ids, limits, mastery, mix, picker, ticket  # noqa: E402
from server.app.db import Database  # noqa: E402
from server.tools import check_tv_silence, gen_seed_questions, import_curriculum  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "local"
DAY = "2026-11-11"
NEXT_DAY = "2026-11-12"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

results: list[tuple[str, bool, str]] = []

#: The wire, frozen (docs/WRONGBOOK.md §9). These are the exact shapes the television already parses;
#: a field that appears or disappears here is a change the child's screen would see, so it has to be an
#: explicit edit to this list rather than an accident of adding to a dict somewhere in the server.
NEXT_KEYS = {"session_id", "day_key", "expires_at", "ttl_seconds", "questions", "quota"}
QUESTION_KEYS = {"id", "topic_id", "qtype", "stem", "angle", "difficulty"}
QUOTA_KEYS = {"success_count", "max_success", "awarded_ms", "max_award_ms", "attempt_count",
              "max_attempts", "award_available", "attempts_left"}
VERDICT_KEYS = {"session_id", "score", "total", "pass_line", "passed", "award_ms", "ticket", "review",
                "cooldown_seconds", "flags"}
FLAG_KEYS = {"capped", "cooldown_applied", "unknown_flagged"}
REVIEW_KEYS = {"question_id", "subject", "topic", "stem", "chosen", "correct", "explanation"}

#: Names that would only be in a response to carry mastery state to the child's screen.
FORBIDDEN_FIELDS = ("need_right", "mastery", "review_question_ids", "mix_state", "mix_c", "mix_r",
                    "mix_n", "segment", "is_review", "weight", "level", "paused_until", "streak")

#: Words that would turn "this kind of question came back" into "you are being made to study".
BANNED_WORDS = ("薄弱", "复习", "巩固", "加强", "强化", "专项", "达标", "过关")


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


def legacy_pick(db, device_id: str, now_ms: int, difficulty: int) -> list[int]:
    """`pick_group` as it was before the quota (docs/WRONGBOOK.md §6.5), copied verbatim.

    This is a golden reference, not a second implementation to keep in step. It reads candidates
    through `picker._candidates` on purpose — the query is not what the quota changed — and then does
    the ordering, the subject rotation, the two-per-topic cap and the shuffle exactly as they were,
    including the *second* `random.Random(now_ms)` for the shuffle. Reusing the first generator there
    would shift the served order and this test would not notice, which is the whole failure it exists
    to catch.

    If this has to be edited, the served group has changed. That is allowed, but it is a decision to
    be made in daylight, not a side effect.
    """
    progress = picker.ensure_progress(db, device_id, now_ms)
    rows = picker._candidates(db, device_id, difficulty)
    rng = random.Random(now_ms)
    tiers = {}
    for row in rows:
        current = progress.get(int(row["textbook_id"]), 1)
        ordinal = int(row["unit_ordinal"])
        tiers[id(row)] = 0 if ordinal == current else (1 if current - 2 <= ordinal < current else 2)

    def key(row: dict) -> tuple:
        return (tiers[id(row)], -int(row["wrong_count"]), row.get("last_seen") or "", rng.random())

    fresh = [row for row in rows if not picker._seen_recently(row, now_ms)]
    stale = [row for row in rows if picker._seen_recently(row, now_ms)]
    ordered = sorted(fresh, key=key) + sorted(stale, key=key)

    by_subject: dict[int, list[dict]] = defaultdict(list)
    for row in ordered:
        by_subject[int(row["subject_id"])].append(row)

    picked: list[dict] = []
    per_topic: Counter[int] = Counter()

    def take(row: dict) -> None:
        picked.append(row)
        per_topic[int(row["topic_id"])] += 1
        by_subject[int(row["subject_id"])].remove(row)

    def take_from(subject_id: int) -> bool:
        for row in by_subject[subject_id]:
            if per_topic[int(row["topic_id"])] < 2:
                take(row)
                return True
        return False

    subject_order = sorted(by_subject)
    for subject_id in subject_order:
        if len(picked) >= limits.GROUP_SIZE:
            break
        take_from(subject_id)

    while len(picked) < limits.GROUP_SIZE:
        took_any = False
        for subject_id in subject_order:
            if len(picked) >= limits.GROUP_SIZE:
                break
            if take_from(subject_id):
                took_any = True
        if not took_any:
            break

    random.Random(now_ms).shuffle(picked)
    return [int(row["id"]) for row in picked]


def submit(device_id: str, session: dict, correct_count: int, day: str = DAY) -> dict:
    answers = []
    for index, question in enumerate(session["questions"]):
        chosen = right(question) if index < correct_count else wrong(question)
        answers.append({"question_id": question["id"], "chosen": chosen, "flagged_unknown": False})
    status, body = http("POST", f"/v1/quiz/{session['session_id']}/grade",
                        {"device_id": device_id, "answers": answers, "now_ms": None})
    assert status == 200, (status, body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="End-to-end acceptance for the quiz server.")
    parser.add_argument(
        "--dsn",
        default=None,
        help="run against this database instead of a fresh SQLite file. MySQL carries no schema "
             "helper: apply server/db/schema.sql first, and point this at an empty database.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even though the target database already has accounts, devices or answers. Only "
             "for a database you are willing to lose data from.",
    )
    args = parser.parse_args(argv)

    LOCAL.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    if args.dsn:
        dsn = args.dsn
        # The database name, not the tail of the string: a unix-socket DSN ends in "/mysqld.sock",
        # and "db mysqld.sock" in the summary line told nobody which database was measured.
        db_label = (urlparse(dsn).path or "/").lstrip("/") or dsn.rsplit("/", 1)[-1]
    else:
        db_path = LOCAL / f"acceptance-{stamp}.sqlite3"
        dsn = f"sqlite:{db_path}"
        db_label = db_path.name

    # ---- 1/2. a fresh database, seeded ----
    db = Database(dsn)
    db.connect(init_schema=True)
    if args.dsn and not dsn.startswith("sqlite"):
        try:
            db.scalar("SELECT COUNT(*) FROM question", default=0)
        except Exception:  # noqa: BLE001 - the message below is the point
            print(f"schema missing in {dsn.split('@')[-1]}; apply server/db/schema.sql first", file=sys.stderr)
            return 2
        # This suite registers accounts, pairs televisions, answers groups and seeds the question bank:
        # pointed at a live database it fills it with fiction, and its own assertions start failing on
        # leftovers from the previous run. Both happened (2026-11-16/17) because the DSN was copied
        # from deploy.env. An empty database is the whole contract of this flag, so it is checked
        # rather than hoped for — content tables included, since the suite imports its own tree.
        used = {table: db.scalar(f"SELECT COUNT(*) FROM {table}", default=0)
                for table in ("question", "topic", "textbook", "account", "device", "quiz_answer",
                              "wrong_item")}
        if any(used.values()) and not args.force:
            print(
                "refusing to run: " + db_label + " already holds "
                + ", ".join(f"{count} {table}" for table, count in used.items() if count)
                + ". Point --dsn at an empty database, or pass --force to overwrite it.",
                file=sys.stderr,
            )
            return 2
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
    # The server's own traceback is the only useful thing when a case fails with a 500, and it is the
    # one thing this suite used to throw away. Set QUIZ_ACCEPTANCE_LOG to keep it.
    log_path = os.environ.get("QUIZ_ACCEPTANCE_LOG")
    log = open(log_path, "wb") if log_path else subprocess.DEVNULL
    server = subprocess.Popen(
        [sys.executable, "-m", "server.app.api", "--port", str(PORT)],
        cwd=str(ROOT.parent), env=env,
        stdout=log, stderr=subprocess.STDOUT if log_path else subprocess.DEVNULL,
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

        # ---- P0-B: a wrong answer becomes a number (docs/WRONGBOOK.md §5) ----
        # A fresh device, so every topic starts at zero and the arithmetic can be checked by hand.
        device5 = pair()
        session5 = serve(device5)
        graded5 = submit(device5, session5, correct_count=2)
        wrong5 = [item["question_id"] for item in (graded5.get("review") or [])]
        check("a three-wrong group reviews three questions", len(wrong5) == 3, str(wrong5))

        probe = Database(dsn)
        probe.connect()
        filed = probe.query(
            "SELECT question_id, source, status FROM wrong_item WHERE device_id = ?", (device5,))
        ledger = probe.query(
            "SELECT topic_id, kind, delta, score_after FROM mastery_event WHERE device_id = ?"
            " ORDER BY created_at, id", (device5,))
        stored = {int(r["topic_id"]): int(r["score"]) for r in probe.query(
            "SELECT topic_id, score FROM topic_mastery WHERE device_id = ?", (device5,))}
        probe.close()

        check("each wrong answer lands in the wrong book",
              sorted(r["question_id"] for r in filed) == sorted(wrong5), f"{len(filed)} filed")
        check("filed as a confirmed television mistake",
              all(r["source"] == "tv" and r["status"] == "confirmed" for r in filed), str(filed[:1]))
        check("one ledger row per wrong answer",
              sum(1 for e in ledger if e["kind"] == "tv_wrong") == 3, str(ledger))
        check("one ledger row per right answer",
              sum(1 for e in ledger if e["kind"] == "tv_right") == 2, str(ledger))
        check("a first mistake is worth twelve",
              all(e["delta"] == 12 for e in ledger if e["kind"] == "tv_wrong"), str(ledger))
        # The ledger is only worth keeping if replaying it lands on the stored score. This is also the
        # check that would catch a weight changed in one place and not the other.
        check("the ledger replays to the stored score",
              bool(stored) and all(
                  sum(e["delta"] for e in ledger if int(e["topic_id"]) == topic) == score
                  for topic, score in stored.items()),
              f"stored={stored} ledger={ledger}")
        check("a right answer cannot take a topic below zero",
              all(int(e["score_after"]) >= 0 for e in ledger), str(ledger))

        replay5 = submit(device5, session5, correct_count=2)
        probe = Database(dsn)
        probe.connect()
        events_after = probe.scalar("SELECT COUNT(*) FROM mastery_event WHERE device_id = ?", (device5,))
        items_after = probe.scalar("SELECT COUNT(*) FROM wrong_item WHERE device_id = ?", (device5,))
        probe.close()
        check("a replayed submit adds no ledger rows", events_after == len(ledger),
              f"{events_after} vs {len(ledger)}")
        check("a replayed submit adds no wrong items", items_after == len(filed), str(items_after))
        check("a replay still answers like the first time", replay5.get("replayed") is True)

        # ---- P0-E: the television is told nothing new (docs/WRONGBOOK.md §9/§10) ----
        # Exact shapes, not "at least": a field that quietly appears here is something the child's
        # screen could start showing, and that is the one thing this feature must never do.
        check("the served group has exactly its old fields", set(session5) == NEXT_KEYS,
              str(sorted(set(session5) ^ NEXT_KEYS)))
        shapes = {frozenset(question) for question in session5["questions"]}
        check("a question carries the old fields and nothing else",
              shapes <= {frozenset(QUESTION_KEYS | {"options"}),
                         frozenset(QUESTION_KEYS | {"accepted"})},
              str([sorted(s) for s in shapes]))
        check("the quota is the same counters as before", set(session5["quota"]) == QUOTA_KEYS,
              str(sorted(set(session5["quota"]) ^ QUOTA_KEYS)))
        check("the verdict has exactly its old fields", set(graded5) == VERDICT_KEYS,
              str(sorted(set(graded5) ^ VERDICT_KEYS)))
        check("and the same flags", set(graded5["flags"]) == FLAG_KEYS, str(sorted(graded5["flags"])))
        check("a reviewed mistake has exactly its old fields",
              all(set(item) == REVIEW_KEYS for item in graded5["review"]),
              str([sorted(item) for item in graded5["review"]][:1]))

        served = json.dumps({"next": session5, "grade": graded5}, ensure_ascii=False)
        leaked = [name for name in FORBIDDEN_FIELDS if name in served]
        check("no mastery field reaches the television", not leaked, str(leaked))
        said = [word for word in BANNED_WORDS if word in served]
        check("no word about weak topics reaches the television", not said, str(said))
        silence = check_tv_silence.scan()
        check("and the android module has none either", not silence, "; ".join(silence[:3]))

        # ---- the rules a random group cannot be made to hit, driven straight at the model ----
        device6 = pair()
        probe = Database(dsn)
        probe.connect()
        topic6 = int(probe.scalar("SELECT id FROM topic ORDER BY id LIMIT 1"))
        iso6 = limits.to_iso(1_770_000_000_000)

        def answer(correct: bool, flagged: bool = False, question_id: int = 1) -> dict:
            with probe.tx() as cur:
                return mastery.record_tv_answer(
                    cur, device_id=device6, topic_id=topic6, correct=correct, flagged=flagged,
                    session_id="01ACCEPTANCE00000000000001", question_id=question_id,
                    now_ms=1_770_000_000_000, now_iso=iso6)

        answer(False)
        answer(False)                       # 24
        answer(True)                        # 14
        answer(True)                        # 4, then the pair bonus takes it to 0
        after_pair = mastery.state(probe, device6, topic6)
        check("a pair of right answers clears two mistakes", after_pair["score"] == 0,
              str(after_pair))
        bonus = probe.query(
            "SELECT delta FROM mastery_event WHERE device_id = ? AND kind = 'streak_bonus'", (device6,))
        check("the pair bonus is its own ledger row, clamped like everything else",
              len(bonus) == 1 and int(bonus[0]["delta"]) == -4, str(bonus))

        for index in range(5):
            paused = answer(False, question_id=10 + index)
        check("five wrong in a row pauses the topic", paused["paused_until"] is not None, str(paused))
        check("and leaves the score at sixty", paused["score"] == 60, str(paused))
        again6 = answer(False, question_id=20)
        check("a sixth wrong answer does not extend the pause",
              again6["paused_until"] == paused["paused_until"], f"{again6['paused_until']}")

        paper_a = ids.new_ulid(1_770_000_000_000)
        with probe.tx() as cur:
            cur.execute(
                "INSERT INTO wrong_item (id, device_id, source, topic_id, occurred_on, status)"
                " VALUES (?, ?, 'paper', ?, ?, 'confirmed')", (paper_a, device6, topic6, DAY))
            first_paper = mastery.record_paper_wrong(
                cur, device_id=device6, topic_id=topic6, wrong_item_id=paper_a, day_key=DAY,
                now_iso=iso6)
        paper_b = ids.new_ulid(1_770_000_000_000)
        with probe.tx() as cur:
            cur.execute(
                "INSERT INTO wrong_item (id, device_id, source, topic_id, occurred_on, status)"
                " VALUES (?, ?, 'paper', ?, ?, 'confirmed')", (paper_b, device6, topic6, DAY))
            second_paper = mastery.record_paper_wrong(
                cur, device_id=device6, topic_id=topic6, wrong_item_id=paper_b, day_key=DAY,
                now_iso=iso6)
        check("the first paper mistake of the day is worth twenty",
              first_paper["delta"] == 20, str(first_paper))
        check("the second on the same topic is worth eight",
              second_paper["delta"] == 8, str(second_paper))
        final6 = mastery.state(probe, device6, topic6)
        probe.close()
        check("a maxed-out topic reads as very weak", final6["level"] == 3 and final6["weight"] == 4.0,
              str(final6))
        check("the parent page can still promise a way out",
              final6["need_right"] == mastery.need_right(final6["score"]) > 0, str(final6))

        # ---- P0-C: the five slots are split by need (docs/WRONGBOOK.md §6) ----
        # One weak topic is enough to set the state: the count and the depth are judged separately and
        # the heavier one wins, so a single L3 topic is S3 on its own.
        probe = Database(dsn)
        probe.connect()

        # Yesterday, relative to DAY: these cases are about the quota, and a mistake dated *today*
        # would trip the newcomer rule instead (§6.6 is tested on its own further down).
        BEFORE_DAY = "2026-11-10 12:00:00"

        def weak_topics(count: int) -> list[int]:
            """Topics with questions to spare, outside the current unit and the two before it."""
            return [int(row["topic_id"]) for row in probe.query(
                "SELECT q.topic_id FROM question q JOIN unit u ON u.id = q.unit_id"
                " WHERE q.difficulty <= 2 AND u.ordinal >= 5 AND q.verify_status = 'sympy_ok'"
                f" GROUP BY q.topic_id HAVING COUNT(*) >= 3 ORDER BY q.topic_id LIMIT {int(count)}")]

        def weak_topic() -> int:
            return weak_topics(1)[0]

        def mark_weak(device_id: str, topic_id: int, score: int, when: str = BEFORE_DAY) -> None:
            level = mastery.level_of(score)
            with probe.tx() as cur:
                cur.execute(
                    "INSERT INTO topic_mastery (device_id, topic_id, score, level, weight_x10,"
                    " target_qty, last_event_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (device_id, topic_id, score, level, mastery.weight_x10(level),
                     mastery.target_qty(level), when, when))

        def split(device_id: str, session: dict) -> tuple[int, int, int]:
            """(review, current-unit, total), read back from the database rather than the response."""
            raw = probe.scalar("SELECT review_question_ids FROM quiz_session WHERE id = ?",
                               (session["session_id"],)) or "[]"
            review = set(json.loads(raw))
            qids = [question["id"] for question in session["questions"]]
            rows = probe.query(
                "SELECT q.id, u.ordinal, cp.current_unit_ordinal AS current FROM question q"
                " JOIN unit u ON u.id = q.unit_id"
                " JOIN curriculum_progress cp ON cp.textbook_id = q.textbook_id AND cp.device_id = ?"
                f" WHERE q.id IN ({','.join('?' for _ in qids)})", [device_id] + qids)
            current = sum(1 for row in rows
                          if int(row["ordinal"]) == int(row["current"]) and int(row["id"]) not in review)
            return len(review), current, len(qids)

        # S0 / S1 / S2 / S3 by one score each, because a single L3 topic is S3 without help.
        for score, state, expected_r, expected_c in ((None, mix.S0, 0, 5),
                                                     (12, mix.S1, 1, 4),
                                                     (36, mix.S2, 2, 3),
                                                     (71, mix.S3, 2, 2)):
            device7 = pair()
            topic7 = weak_topic()
            if score is not None:
                mark_weak(device7, topic7, score)
            session7 = serve(device7)
            review7, current7, total7 = split(device7, session7)
            stored_state = probe.scalar(
                "SELECT mix_state FROM daily_state WHERE device_id = ? AND day_key = ?",
                (device7, DAY))
            check(f"{state} puts {expected_r} of 5 in the review segment",
                  (review7, total7) == (expected_r, 5), f"{review7}/{total7}")
            check(f"{state} keeps the current unit within {expected_c}",
                  current7 <= expected_c, f"{current7} current-unit questions")
            check(f"{state} is the state the snapshot recorded", stored_state == state,
                  str(stored_state))

        # The split is decided once a day. Making the topic worse cannot move it until tomorrow.
        # Driven through `pick_group` rather than HTTP: the daily per-topic cap would otherwise be
        # indistinguishable from the split moving, and this case is about the split alone.
        device8 = pair()
        mark_weak(device8, weak_topic(), 36)                 # one L2 topic -> S2, two review slots
        first8 = picker.pick_group(probe, device8, DAY, 1_770_000_000_000, limits.DEFAULT_DIFFICULTY)
        with probe.tx() as cur:
            cur.execute("UPDATE topic_mastery SET score = 100, level = 3, weight_x10 = 40"
                        " WHERE device_id = ?", (device8,))
        second8 = picker.pick_group(probe, device8, DAY, 1_770_000_001_000, limits.DEFAULT_DIFFICULTY)
        check("the day's split is taken from the state at the first group",
              (first8.plan["state"], second8.plan["state"]) == (mix.S2, mix.S2),
              f"{first8.plan['state']} then {second8.plan['state']}")
        check("so the second group still has two review slots",
              len(first8.review_ids) == len(second8.review_ids) == 2,
              f"{len(first8.review_ids)} then {len(second8.review_ids)}")
        check("even though the topic is now very weak",
              mastery.state(probe, device8, weak_topic())["level"] == 3, "level did not change")

        # One topic cannot fill the day: three questions a day is the cap, and a slot that runs out of
        # candidates goes back to the course rather than shrinking the group.
        device9 = pair()
        topic9 = weak_topic()
        mark_weak(device9, topic9, 71)                       # L3, and the daily cap is 3
        per_group = [split(device9, serve(device9)) for _ in range(3)]
        check("the daily cap drains the review slots one group at a time",
              [item[0] for item in per_group] == [2, 1, 0], str(per_group))
        check("and a slot with no candidates goes back to the course, not missing",
              [item[2] for item in per_group] == [5, 5, 5], str(per_group))

        # Something that goes wrong today gets one slot even though the day's snapshot says none.
        device10 = pair()
        topic10 = weak_topic()
        before_ten = serve(device10)                         # snapshot taken while nothing was wrong
        mark_weak(device10, topic10, 71, when=f"{DAY} 10:00:00")
        after_ten = serve(device10)
        check("nothing was reviewed while nothing was wrong",
              split(device10, before_ten)[0] == 0, str(split(device10, before_ten)))
        check("a mistake made today is practised today",
              split(device10, after_ten)[0] == 1, str(split(device10, after_ten)))
        check("and the snapshot still says what it said this morning",
              probe.scalar("SELECT mix_state FROM daily_state WHERE device_id = ? AND day_key = ?",
                           (device10, DAY)) == mix.S0)

        # ---- P0-D: weighted draws, reproducible, and no drift when nothing is wrong ----
        def fake(topic_id: int, count: int) -> list[dict]:
            return [{"id": 1000 + topic_id * 100 + i, "topic_id": topic_id} for i in range(count)]

        # Supply equal to target, so the weights are the bare 4 and 2 of §6.7 and the ratio is 2:1.
        weak_pair = {1: {"level": 3, "score": 71, "topic_id": 1}, 2: {"level": 1, "score": 12, "topic_id": 2}}
        pool_pair = fake(1, 10) + fake(2, 5)
        supply_pair = Counter({1: 10, 2: 5})
        draws = Counter()
        for seed in range(400):
            first_pick = picker._review_pick(pool_pair, weak_pair, supply_pair, random.Random(seed), 1,
                                             Counter())[0]
            draws[int(first_pick["topic_id"])] += 1
        share = draws[1] / max(1, sum(draws.values()))
        check("the weaker topic is drawn about twice as often (4:2)",
              0.6 <= share <= 0.74, f"L3 share {share:.2f} over {sum(draws.values())} draws")
        same = [int(picker._review_pick(pool_pair, weak_pair, supply_pair, random.Random(7), 1,
                                        Counter())[0]["id"]) for _ in range(2)]
        check("the same seed draws the same question", same[0] == same[1], str(same))

        # The whole group twice at the same instant: same questions, or a report cannot be reproduced.
        device12 = pair()
        topic12 = weak_topic()
        mark_weak(device12, topic12, 71)
        once = picker.pick_group(probe, device12, NEXT_DAY, 1_770_000_000_000, limits.DEFAULT_DIFFICULTY)
        twice = picker.pick_group(probe, device12, NEXT_DAY, 1_770_000_000_000, limits.DEFAULT_DIFFICULTY)
        check("the same moment produces the same group",
              [int(q["id"]) for q in once.questions] == [int(q["id"]) for q in twice.questions],
              str([int(q["id"]) for q in once.questions]))
        check("and two review questions in it", len(once.review_ids) == 2, str(once.review_ids))

        # Zero regression (§6.5): with nothing weak, today's picker must return the old group exactly.
        device13 = pair()
        frozen = legacy_pick(probe, device13, 1_770_000_000_000, limits.DEFAULT_DIFFICULTY)
        actual = picker.pick_group(probe, device13, DAY, 1_770_000_000_000, limits.DEFAULT_DIFFICULTY)
        check("with no weak topic the picker serves exactly the group it used to",
              [int(q["id"]) for q in actual.questions] == frozen,
              f"{[int(q['id']) for q in actual.questions]} vs {frozen}")
        check("and takes nothing from the review segment", actual.review_ids == [],
              str(actual.review_ids))

        # ---- §3.0: who moves the unit, and what a finished book means ----
        # Until now `current_unit_ordinal` was written once at 1 and never again, so a child in unit 5
        # was being served unit 1. These cases pin the two sources that move it.
        device14 = pair()
        with probe.tx() as cur:
            cur.execute("INSERT INTO term_anchor (device_id, start_date) VALUES (?, ?)",
                        (device14, "2026-09-01"))

        def at(year: int, month: int, day: int) -> int:
            return int(datetime.datetime(year, month, day, 9, 0).timestamp() * 1000)

        autumn, winter = at(2026, 11, 11), at(2027, 1, 20)
        stage14 = curriculum.resolve(probe, device14, autumn)[1]     # textbook 1 is 数学
        check("by week 11 the calendar has the child in unit 5",
              (stage14.current, stage14.source, stage14.reviewing) == (5, "calendar", False),
              str(stage14))

        late14 = curriculum.resolve(probe, device14, winter)[1]
        check("by week 21 the book is finished, so no unit is 'current' any more",
              (late14.reviewing, late14.current) == (True, late14.last), str(late14))

        # A number a parent typed on purpose must not be overwritten by arithmetic.
        curriculum.set_unit(probe, device14, 1, 2, autumn)
        overridden = curriculum.resolve(probe, device14, autumn)[1]
        check("a parent's unit beats the calendar's",
              (overridden.current, overridden.source) == (2, "parent"), str(overridden))

        # ... and the calendar takes it back when the parent says so, without touching the date.
        curriculum.set_anchor(probe, device14, datetime.date(2026, 9, 1), autumn)
        restored = curriculum.resolve(probe, device14, autumn)[1]
        check("handing the unit back to the calendar restores week 11's answer",
              (restored.current, restored.source) == (5, "calendar"), str(restored))

        # Nothing from a unit the class has not reached — that was implicit before and wrong.
        curriculum.set_unit(probe, device14, 1, 2, autumn)
        stages14 = curriculum.resolve(probe, device14, autumn)
        group14 = picker.pick_group(probe, device14, NEXT_DAY, autumn, limits.DEFAULT_DIFFICULTY)
        highest: dict[int, int] = {}
        for question in group14.questions:
            book = int(question["textbook_id"])
            highest[book] = max(highest.get(book, 0), int(question["unit_ordinal"]))
        beyond = {book: ordinal for book, ordinal in highest.items()
                  if ordinal > stages14[book].current}
        check("no question comes from a unit the child has not reached", not beyond,
              f"{beyond} vs { {b: s.current for b, s in stages14.items()} }")

        # The previous-units band is the whole term, not the last two units: a September unit is a
        # previous unit (tier 1), while next month's is last-resort only (tier 3).
        previous = curriculum.BookStage(textbook_id=1, current=5, last=8, reviewing=False,
                                        source="parent")
        scope = {1: previous}

        def tier_of(ordinal: int) -> int:
            return picker._tier({"textbook_id": 1, "unit_ordinal": ordinal}, scope)

        check("a unit from September is a previous unit, not an out-of-scope one",
              (tier_of(1), tier_of(4), tier_of(5), tier_of(6))
              == (1, 1, 0, picker.UNLEARNED),
              f"{tier_of(1)}/{tier_of(4)}/{tier_of(5)}/{tier_of(6)}")

        # Finished book: the c slots stop privileging one unit, so all five come from the whole book.
        curriculum.set_anchor(probe, device14, datetime.date(2026, 9, 1), winter)
        within = curriculum.resolve(probe, device14, winter)
        check("all three books are finished by week 21",
              all(stage.reviewing for stage in within.values()),
              str({book: (stage.current, stage.last) for book, stage in within.items()}))
        group15 = picker.pick_group(probe, device14, NEXT_DAY, winter, limits.DEFAULT_DIFFICULTY)
        inside = sum(1 for question in group15.questions
                     if picker._tier(question, within) == 0)
        check("a finished book serves from all of it, not only from the last unit",
              (group15.plan.get("stage"), inside) == ("reviewing", 5),
              f"{group15.plan.get('stage')} / {inside} of 5")
        probe.close()

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

        # ---- P2: the weak list, the ledger, and the parent's two hands ----
        #
        # One promise holds this block together: a number the parent can act on, and an action that
        # changes what the child is asked next — while the television still says nothing (that is §10,
        # asserted above by the frozen protocol shapes). Nothing here may add a field to `/quiz/next`.
        p2 = start(T + 3_600_000)
        p2_device = p2["device_id"]
        dad = Parent()
        status, body = dad.register("p2@example.com")
        check("the third parent has an account", status == 201, f"{status} {body}")
        status, body = dad.call("POST", "/v1/h5/pair",
                                {"pair_code": p2["pair_code"], "now_ms": T + 3_660_000})
        check("and holds the P2 television",
              status == 200 and body["device"]["device_id"] == p2_device, f"{status} {body}")
        weak_url = f"/v1/h5/mastery?day_key={DAY}&device_id={p2_device}"

        status, body = dad.call("GET", weak_url)
        check("a television nobody has used has no weak topics",
              status == 200 and body["count"] == 0 and body["mix"] is None, f"{status} {body}")

        first_group = serve(p2_device, DAY)
        submit(p2_device, first_group, 1, DAY)          # four wrong answers, one right
        status, body = dad.call("GET", weak_url)
        listed = body["items"]
        check("wrong answers put topics on the weak list",
              body["count"] >= 1 and all(item["score"] >= 12 for item in listed), str(listed[:2]))
        check("worst first",
              [item["score"] for item in listed] == sorted(
                  (item["score"] for item in listed), reverse=True), str(listed[:2]))
        check("every row says how many right answers are left",
              all(item["need_right"] >= 1 for item in listed), str(listed[:2]))
        check("and carries the lesson it belongs to",
              all(item["unit"] and item["subject"] and item["topic"] for item in listed), str(listed[:1]))
        check("the day's split is reported as it was frozen",
              body["mix"] and body["mix"]["c"] + body["mix"]["p"] + body["mix"]["r"] == 5,
              str(body.get("mix")))
        check("the list page has its one sentence",
              isinstance(body["week_line"], str) and body["week_line"], str(body.get("week_line")))

        # The add sheet's menu is the whole catalog, not the weak list: a parent recording a mistake
        # from a paper is often recording one on a topic that has not gone weak yet.
        status, body = dad.call("GET", f"/v1/h5/topics?device_id={p2_device}")
        catalog = body.get("topics") or []
        check("the add sheet offers every topic, weak or not",
              status == 200 and len(catalog) >= 20
              and any(topic["score"] == 0 for topic in catalog)
              and any(topic["score"] >= 12 for topic in catalog),
              f"{status} {len(catalog)}")

        worst = listed[0]
        detail_url = (f"/v1/h5/mastery/{worst['topic_id']}?day_key={DAY}"
                      f"&device_id={p2_device}")
        status, detail = dad.call("GET", detail_url)
        # The ledger has to add up to the score it claims. If it ever does not, the page is quoting a
        # number no one — including the server — can reproduce.
        check("a topic page reproduces its own score from the ledger",
              status == 200 and sum(e["delta"] for e in detail["events"]) == detail["score"],
              f"{status} {detail.get('score')} {[e['delta'] for e in detail.get('events', [])]}")
        check("and shows the mistakes behind it",
              len(detail["wrong_items"]) >= 1 and all(w["stem"] for w in detail["wrong_items"]),
              str(detail.get("wrong_items")))
        check("and one sentence to say out loud",
              bool(detail["coach"]["say"]) and bool(detail["coach"]["why"])
              and bool(detail["coach"]["avoid"]), str(detail.get("coach")))
        check("the topic page knows how much of today's cap is spent",
              0 <= detail["today_used"] <= detail["daily_cap"], str(detail.get("today_used")))

        # ---- the parent's ±10 ----
        adjust_url = f"/v1/h5/topic/{worst['topic_id']}/adjust?device_id={p2_device}"
        before = worst["score"]
        status, body = dad.call("POST", adjust_url, {"delta": -10, "note": "周末给他讲过了"})
        check("ten points off with a reason", status == 200 and body["score"] == before - 10,
              f"{status} {body.get('score')} (was {before})")
        status, body = dad.call("GET", detail_url)
        check("the adjustment is in the ledger, with the reason",
              body["events"][0]["kind"] == "parent_adjust" and "周末" in body["events"][0]["label"],
              str(body["events"][:1]))
        check("and the score it records is the one after the clamp",
              body["events"][0]["score_after"] == body["score"], str(body["events"][:1]))
        status, body = dad.call("POST", adjust_url, {"delta": -10})
        check("but not without a reason",
              status == 400 and body.get("error") == "note-required", f"{status} {body}")
        status, body = dad.call("POST", adjust_url, {"delta": -25, "note": "一次到位"})
        check("and never more than ten at a time",
              status == 400 and body.get("error") == "bad-delta", f"{status} {body}")
        status, body = stranger.call("POST", adjust_url, {"delta": 10, "note": "别人家的电视"})
        check("another account cannot move this score",
              status == 403, f"{status} {body}")

        # ---- recording a paper: the ≤20-second path ----
        #
        # A second television, all five right, so today's split is S0 with no review slot. That is the
        # only state in which "record a mistake, see it in the next group" can be tested honestly: on a
        # day that already has review slots the topic could have been asked anyway.
        # `today`, not `DAY`: the newcomer slot compares the ledger's real timestamp with the day being
        # planned, so the two have to be the same day or the case would pass only on the right date.
        today = datetime.date.today().isoformat()
        p2b = start(T + 4_300_000)
        p2b_device = p2b["device_id"]
        status, body = dad.call("POST", "/v1/h5/pair",
                                {"pair_code": p2b["pair_code"], "now_ms": T + 4_360_000})
        clean_group = serve(p2b_device, today)
        submit(p2b_device, clean_group, 5, today)
        status, body = dad.call("GET", f"/v1/h5/mastery?day_key={today}&device_id={p2b_device}")
        check("a clean group leaves nothing to practise",
              body["count"] == 0 and body["mix"] and body["mix"]["r"] == 0, f"{status} {body}")

        # A topic with questions left to ask: the ones served above are inside the seven-day window, so
        # a topic whose only question was just asked cannot be practised today and `mix.assess` drops it
        # on purpose. Picking a richer, untouched topic keeps this case about the newcomer slot rather
        # than about a thin bank.
        asked = [int(q["topic_id"]) for q in clean_group["questions"]]
        marks = ",".join("?" for _ in asked)
        probe = Database(dsn)
        probe.connect()
        rich = probe.query_one(
            "SELECT q.topic_id, COUNT(*) AS n FROM question q"
            " WHERE q.retired_at IS NULL AND q.verify_status IN ('sympy_ok', 'manual_ok')"
            "   AND q.difficulty <= 3"
            f"   AND q.topic_id NOT IN ({marks})"
            " GROUP BY q.topic_id ORDER BY n DESC LIMIT 1",
            asked,
        )
        probe.close()
        check("the bank has a topic with questions to spare", rich is not None, str(rich))
        target = int(rich["topic_id"]) if rich else asked[0]

        status, body = dad.call("POST", f"/v1/h5/paper?device_id={p2b_device}", {
            "topic_id": target, "count": 3, "day_key": today,
            # The page chooses the batch id, which is what makes a second tap harmless.
            "paper_id": "P2BATCH0000000000000000001",
        })
        check("recording three mistakes adds 20, then 8, then 8",
              status == 200 and body["added"] == 20 + 2 * 8, f"{status} {body}")
        check("and the topic is on the list right away",
              body["topic"]["score"] == 20 + 2 * 8 and body["topic"]["level"] >= 1,
              str(body.get("topic")))
        status, again = dad.call("POST", f"/v1/h5/paper?device_id={p2b_device}", {
            "topic_id": target, "count": 3, "day_key": today,
            "paper_id": "P2BATCH0000000000000000001",
        })
        check("a repeated batch does not count twice",
              status == 200 and again.get("repeated") is True
              and again["topic"]["score"] == body["topic"]["score"], f"{status} {again.get('topic')}")

        next_group = serve(p2b_device, today)
        asked_next = [int(q["topic_id"]) for q in next_group["questions"]]
        check("the very next group asks it",
              target in asked_next, f"target={target} asked={asked_next}")

        status, body = dad.call(
            "GET", f"/v1/h5/wrongbook?day_key={today}&days=7&device_id={p2b_device}&source=paper")
        check("the wrong list shows the recorded mistakes",
              body["total"] == 3 and [i["source"] for i in body["items"]] == ["paper"] * 3,
              f"{status} {body.get('total')} {[i['source'] for i in body.get('items', [])]}")
        check("with the score of their topic attached",
              all(i["mastery_score"] == 20 + 2 * 8 for i in body["items"]), str(body["items"][:1]))
        status, body = dad.call(
            "GET", f"/v1/h5/wrongbook?day_key={today}&days=7&device_id={p2b_device}&source=tv")
        check("and the television's own list stays separate",
              body["total"] == 0 and body["counts"] == {"all": 3, "tv": 0, "paper": 3},
              f"{body.get('total')} {body.get('counts')}")
        status, body = dad.call(
            "GET", f"/v1/h5/wrongbook?day_key={today}&days=7&device_id={p2b_device}&limit=1&offset=1")
        check("paging moves the window", len(body["items"]) == 1 and body["total"] == 3,
              f"{body.get('total')} {len(body.get('items', []))}")

        # ---- where the child is in the book ----
        status, body = dad.call("GET", f"/v1/h5/curriculum?device_id={p2b_device}")
        check("progress starts on unit one, by calendar",
              status == 200 and body["start_date"] is None
              and all(book["current"] == 1 and book["source"] == "calendar"
                      for book in body["books"]), f"{status} {body.get('books')}")
        check("and every book lists the units it has",
              len(body["books"]) >= 2 and all(book["units"] for book in body["books"]),
              str(len(body.get("books", []))))

        status, body = dad.call("PUT", f"/v1/h5/curriculum?device_id={p2b_device}",
                                {"start_date": "2026-09-01"})
        expected_week = curriculum.weeks_in(datetime.date(2026, 9, 1), datetime.date.today())
        check("a term start turns into a week",
              status == 200 and body["week"] == expected_week, f"{status} {body.get('week')}")
        book = body["books"][0]
        last_ordinal = book["units"][-1]["ordinal"]
        status, body = dad.call("PUT", f"/v1/h5/curriculum/unit?device_id={p2b_device}",
                                {"textbook_id": book["textbook_id"], "ordinal": last_ordinal})
        after = next(b for b in body["books"] if b["textbook_id"] == book["textbook_id"])
        check("a parent can say which unit the class is on",
              after["current"] == last_ordinal and after["source"] == "parent", str(after))
        check("finishing the book is a state, not an error",
              after["reviewing"] is True and after["last"] == last_ordinal, str(after))
        status, body = dad.call("PUT", f"/v1/h5/curriculum/unit?device_id={p2b_device}",
                                {"textbook_id": book["textbook_id"], "mode": "calendar"})
        after = next(b for b in body["books"] if b["textbook_id"] == book["textbook_id"])
        check("and hand that book back to the date",
              after["source"] == "calendar" and after["reviewing"] is False, str(after))
        status, body = dad.call("PUT", f"/v1/h5/curriculum?device_id={p2b_device}",
                                {"start_date": "term started yesterday"})
        check("a date that is not a date is refused",
              status == 400 and body.get("error") == "bad-date", f"{status} {body}")

        # ---- a bad question ----
        #
        # `bad_question_void` is the reason the flag lives on the phone (D5): the parent is the one who
        # notices a typo, and a topic must not stay weak because of it.
        wrong_qid = int(first_group["questions"][1]["id"])   # `submit` marks index 1 as wrong
        probe = Database(dsn)
        probe.connect()
        topic_before = probe.query_one(
            "SELECT topic_id, score FROM topic_mastery WHERE device_id = ? AND topic_id = ?",
            (p2_device, int(first_group["questions"][1]["topic_id"])),
        )
        probe.close()
        status, body = dad.call("POST", f"/v1/h5/question/{wrong_qid}/flag?device_id={p2_device}",
                                {"note": "题干里的单位写错了"})
        check("a flagged question stops being served", status == 200 and body["retired"] is True,
              f"{status} {body}")
        probe = Database(dsn)
        probe.connect()
        retired = probe.scalar("SELECT retired_at FROM question WHERE id = ?", (wrong_qid,))
        after_row = probe.query_one(
            "SELECT score FROM topic_mastery WHERE device_id = ? AND topic_id = ?",
            (p2_device, int(first_group["questions"][1]["topic_id"])),
        )
        probe.close()
        check("and is retired in the bank itself", retired is not None, str(retired))
        check("and the score it caused is given back",
              topic_before is not None and after_row is not None
              and after_row["score"] < topic_before["score"], f"{topic_before} -> {after_row}")
        # Asked of the pool itself rather than by serving another group: that device's failed group
        # bought a ten-minute cooldown, and working around it would be testing the cooldown instead.
        probe = Database(dsn)
        probe.connect()
        servable = [int(row["id"]) for row in picker._candidates(probe, p2_device, 99)]
        probe.close()
        check("so it is out of the pool the picker draws from",
              wrong_qid not in servable, f"{wrong_qid} in {len(servable)} candidates")

    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    failed = [r for r in results if not r[1]]
    for name, ok, detail in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    print(f"\nacceptance: {len(results) - len(failed)}/{len(results)} passed  (db {db_label})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
