"""Grade a submitted group. The only place a right answer becomes minutes.

Three things this file is careful about:

**Equivalence, not string equality.** `3/4`, `0.75` and `75%` are the same answer. A child who writes
the right number in a different form has earned the time; failing them would teach the wrong lesson
about maths. Comparison is exact rational arithmetic (`fractions.Fraction`), never floating point.

**No symbolic parsing of submitted text.** The tempting way to compare `3/4` and `0.75` is to hand both
to sympy. Do not: `sympify` on a string the child typed is an eval-shaped hole in the server. Anything
that reaches `Fraction` here has already passed a character whitelist, so the worst a hostile input can
do is fail to parse.

**Idempotence.** A phone on a bad connection retries. A retried submit must return the same score and
the same ticket `jti`, and must not add a second success. `quiz_session.status` is the lock.
"""

from __future__ import annotations

import datetime
import json
import re
import unicodedata
from fractions import Fraction

from server.app import limits, mastery, ticket
from server.app.ids import new_ulid


class GradeError(Exception):
    def __init__(self, code: str, http_status: int, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.http_status = http_status
        self.detail = detail


#: Everything a numeric answer may contain, after normalisation. Percent is handled before this check.
NUMERIC_SAFE = re.compile(r"^-?\d+(\.\d+)?(/-?\d+(\.\d+)?)?$")


def normalize(text: str | None) -> str:
    """Fold the ways a child's input differs from the stored answer without differing in meaning."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFKC", str(text))     # full-width digits and punctuation -> ASCII
    text = text.strip().replace(" ", "").replace("\u3000", "")
    text = text.replace("％", "%").replace("：", ":").replace("，", ",")
    return text.rstrip("。.")

def as_fraction(text: str | None) -> Fraction | None:
    """Parse a number, a fraction or a percentage. None when it is none of those."""
    text = normalize(text)
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1]
        try:
            return Fraction(text) / 100
        except (ValueError, ZeroDivisionError):
            return None
    if not NUMERIC_SAFE.match(text):
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def answers_match(question: dict, accepted: list[dict], chosen: str | None) -> bool:
    """Is `chosen` one of the accepted answers for this question?"""
    if chosen is None:
        return False
    if question["qtype"] == "choice":
        return normalize(chosen).upper() in {
            normalize(row["text_raw"]).upper() for row in accepted if row["cmp_kind"] == "exact"
        }

    submitted_text = normalize(chosen)
    submitted_number = as_fraction(chosen)
    for row in accepted:
        if row["cmp_kind"] == "numeric":
            if submitted_number is not None:
                stored = as_fraction(row["text_raw"])
                if stored is not None and stored == submitted_number:
                    return True
        elif submitted_text == normalize(row["text_raw"]):
            return True
    return False


def _load_session(db, session_id: str) -> dict:
    session = db.query_one("SELECT * FROM quiz_session WHERE id = ?", (session_id,))
    if session is None:
        raise GradeError("unknown-session", 404)
    return session


def _accepted_answers(db, question_ids: list[int]) -> dict[int, list[dict]]:
    placeholders = ",".join("?" for _ in question_ids)
    rows = db.query(
        f"SELECT question_id, text_raw, cmp_kind FROM question_answer"
        f" WHERE question_id IN ({placeholders}) ORDER BY ordinal",
        question_ids,
    )
    out: dict[int, list[dict]] = {qid: [] for qid in question_ids}
    for row in rows:
        out[int(row["question_id"])].append(dict(row))
    # Choice questions keep the correct label in question_option, not question_answer; flatten it into
    # the same shape so there is one comparison path.
    for row in db.query(
        f"SELECT question_id, label FROM question_option"
        f" WHERE question_id IN ({placeholders}) AND is_correct = 1",
        question_ids,
    ):
        out[int(row["question_id"])].append({"text_raw": row["label"], "cmp_kind": "exact"})
    return out


def _graded_from_rows(rows) -> list[tuple]:
    """The shape [grade_session] grades in, rebuilt from `quiz_answer` for a repeated submit."""
    return [(int(row["question_id"]), row["chosen"], bool(row["is_correct"]), False) for row in rows]


def _review(db, questions: dict[int, dict], accepted: dict[int, list[dict]], graded: list[tuple],
            order: list[int] | None = None) -> list[dict]:
    """The wrong answers of a group, as the television's result page shows them.

    One item per wrong question, with the answer the child gave and the right one — the owner's call
    (docs/PARENTAL.md, 界面): the child has just seen the right answer next to every wrong one, so a
    version without it would only make the page useless. The seven-day book on the phone is still the
    parent's; this one is the group that is on screen.

    `explanation` is empty when the bank has none (~20% of it). The television drops the line rather
    than printing "no explanation yet" — a placeholder reads as a promise.
    """
    wrong = [(qid, chosen) for qid, chosen, correct, _ in graded if not correct]
    if not wrong:
        return []
    # The group's own order, not the order the answers arrived in: a replayed submit rebuilds them
    # from `quiz_answer` and has to draw the same page as the first response did, card for card.
    if order:
        rank = {qid: index for index, qid in enumerate(order)}
        wrong.sort(key=lambda pair: rank.get(pair[0], len(rank)))

    ids = [qid for qid, _ in wrong]
    marks = ",".join("?" for _ in ids)
    option_text: dict[int, dict[str, str]] = {}
    for row in db.query(
        f"SELECT question_id, label, text FROM question_option WHERE question_id IN ({marks})",
        ids,
    ):
        option_text.setdefault(int(row["question_id"]), {})[row["label"]] = row["text"]

    def show(qid: int, label: str) -> str:
        """`B. 3/8` rather than a bare letter: a label alone means nothing a day later."""
        label = (label or "").strip()
        text = option_text.get(qid, {}).get(label)
        return f"{label}. {text}" if text else label

    items = []
    for qid, chosen in wrong:
        question = questions.get(qid, {})
        if question.get("qtype") == "choice":
            correct_label = next(
                (row["text_raw"] for row in accepted.get(qid, []) if row["cmp_kind"] == "exact"),
                "",
            )
            picked, right = show(qid, chosen or ""), show(qid, correct_label)
        else:
            # A fill-in has several accepted spellings; the first is the one the bank considers the
            # answer, the rest are alternatives (`3/4` next to `0.75`).
            answers = accepted.get(qid) or []
            picked, right = (chosen or "").strip(), (answers[0]["text_raw"] if answers else "")
        items.append(
            {
                "question_id": qid,
                "subject": question.get("subject") or "",
                "topic": question.get("topic") or "",
                "stem": question.get("stem") or "",
                "chosen": picked,
                "correct": right,
                "explanation": question.get("explanation_zh") or "",
            }
        )
    return items


def _bump_topic_signal(cur, device_id: str, topic_id: int, correct: bool, flagged: bool, now_iso: str) -> None:
    """Called with the transaction cursor, not the database: this update has to commit or roll back with
    the answers that caused it, or a retry would double-count the wrong answers."""
    row = cur.query_one(
        "SELECT * FROM topic_signal WHERE device_id = ? AND topic_id = ?", (device_id, topic_id)
    )
    right = int(row["right_count"]) if row else 0
    wrong = int(row["wrong_count"]) if row else 0
    unknown = int(row["unknown_count"]) if row else 0
    streak = int(row["unknown_streak"]) if row else 0

    if flagged:
        unknown += 1
        streak += 1
    else:
        streak = 0
        if correct:
            right += 1
        else:
            wrong += 1

    if row is None:
        cur.execute(
            "INSERT INTO topic_signal (device_id, topic_id, right_count, wrong_count, unknown_count,"
            " unknown_streak, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (device_id, topic_id, right, wrong, unknown, streak, now_iso),
        )
    else:
        cur.execute(
            "UPDATE topic_signal SET right_count = ?, wrong_count = ?, unknown_count = ?,"
            " unknown_streak = ?, last_seen_at = ? WHERE device_id = ? AND topic_id = ?",
            (right, wrong, unknown, streak, now_iso, device_id, topic_id),
        )


def _record_wrong_item(cur, *, device_id: str, topic_id: int, question_id: int, stem: str,
                       day_key, now_iso: str) -> str:
    """File a TV mistake where the parent will find it (docs/WRONGBOOK.md §4.1).

    Written for every wrong answer, including one the child flagged as 没学过: the parent should see
    what was actually asked and answered, and the flag is already on `quiz_answer` for the topics that
    need a different conversation. No extra request from the TV — this is the same transaction.
    """
    item_id = new_ulid()
    day = day_key if isinstance(day_key, str) else day_key.strftime("%Y-%m-%d")
    cur.execute(
        "INSERT INTO wrong_item (id, device_id, source, question_id, topic_id, stem_text, occurred_on,"
        " status, created_at) VALUES (?, ?, 'tv', ?, ?, ?, ?, 'confirmed', ?)",
        (item_id, device_id, question_id, topic_id, stem, day, now_iso),
    )
    return item_id


def _existing_ticket(db, session_id: str) -> dict | None:
    return db.query_one("SELECT * FROM ticket WHERE session_id = ?", (session_id,))


def grade_session(
    db,
    *,
    session_id: str,
    device_id: str,
    answers: list[dict],
    now_ms: int,
    signer,
) -> dict:
    session = _load_session(db, session_id)
    if session["device_id"] != device_id:
        raise GradeError("wrong-device", 403)

    now_iso = limits.to_iso(now_ms)
    question_ids = _question_ids(session)
    questions = {
        int(row["id"]): dict(row)
        for row in db.query(
            "SELECT q.id, q.topic_id, q.qtype, q.stem, q.explanation_zh,"
            "       t.name_zh AS topic, s.name_zh AS subject"
            "  FROM question q"
            "  JOIN topic t ON t.id = q.topic_id"
            "  JOIN unit u ON u.id = t.unit_id"
            "  JOIN textbook b ON b.id = u.textbook_id"
            "  JOIN subject s ON s.id = b.subject_id"
            f" WHERE q.id IN ({','.join('?' for _ in question_ids)})",
            question_ids,
        )
    }
    accepted = _accepted_answers(db, question_ids)

    # A retried submit: same answer, no second count, and the ticket is re-signed with the same jti so
    # the TV sees a string it can still spend.
    if session["status"] == "submitted":
        stored = _existing_ticket(db, session_id)
        row = db.query_one(
            "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?",
            (device_id, session["day_key"]),
        )
        payload = {
            "session_id": session_id,
            "score": int(session["score"] or 0),
            "total": len(question_ids),
            "passed": bool(session["passed"]),
            "award_ms": int(session["award_ms"] or 0),
            "ticket": None,
            "replayed": True,
            # The same two fields as a first response: a client that never saw the first one draws
            # the same page from this one.
            "review": _review(db, questions, accepted, _graded_from_rows(
                db.query(
                    "SELECT question_id, chosen, is_correct FROM quiz_answer WHERE session_id = ?",
                    (session_id,),
                )
            ), question_ids),
            "cooldown_seconds": limits.is_cooldown_active(row, now_ms) if row else 0,
        }
        if stored is not None:
            payload["ticket"] = _sign_stored_ticket(stored, signer)
        return payload

    if session["status"] in ("expired", "void"):
        raise GradeError("session-" + session["status"], 409)

    expires_at = limits.parse_iso(session["expires_at"])
    if expires_at is not None and datetime.datetime.fromtimestamp(now_ms / 1000) >= expires_at:
        db.execute("UPDATE quiz_session SET status = 'expired' WHERE id = ?", (session_id,))
        raise GradeError("expired", 409, "the ten minutes for this group ran out")

    submitted = {int(a.get("question_id", 0)): a for a in answers if isinstance(a, dict)}
    unknown_ids = [qid for qid in submitted if qid not in questions]
    if unknown_ids:
        raise GradeError("unknown-question", 400, f"questions not in this session: {unknown_ids[:3]}")

    graded = []
    for qid in question_ids:
        answer = submitted.get(qid, {})
        chosen = answer.get("chosen")
        flagged = bool(answer.get("flagged_unknown"))
        correct = answers_match(questions[qid], accepted[qid], chosen)
        graded.append((qid, chosen, correct, flagged))

    score = sum(1 for _, _, correct, _ in graded if correct)
    total = len(graded)
    need = limits.pass_line(total)
    passed = score >= need
    flagged_any = any(flagged for _, _, _, flagged in graded)

    ticket_row = None
    award_ms = 0
    with db.tx() as cur:
        # The row is created here rather than through limits.get_or_create_daily: that helper commits,
        # and a commit in the middle of this transaction would let half a submit land.
        daily = cur.query_one(
            "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?" + db.lock_clause(),
            (device_id, session["day_key"]),
        )
        if daily is None:
            cur.execute(
                "INSERT INTO daily_state (device_id, day_key, success_count, awarded_ms, attempt_count,"
                " fail_count, cooldown_until, updated_at) VALUES (?, ?, 0, 0, 0, 0, NULL, ?)",
                (device_id, session["day_key"], now_iso),
            )
            daily = cur.query_one(
                "SELECT * FROM daily_state WHERE device_id = ? AND day_key = ?",
                (device_id, session["day_key"]),
            )
        quota = limits.quota_state(daily)

        for qid, chosen, correct, flagged in graded:
            cur.execute(
                "INSERT INTO quiz_answer (session_id, question_id, device_id, answered_day, chosen,"
                " is_correct, flagged_unknown, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, qid, device_id, session["day_key"], chosen, 1 if correct else 0,
                 1 if flagged else 0, now_iso),
            )
            topic_id = int(questions[qid]["topic_id"])
            _bump_topic_signal(cur, device_id, topic_id, correct, flagged, now_iso)
            # The mastery ledger moves in the same transaction as the answers: a retry that rolled
            # back half of this would double-count a wrong answer, which is exactly the number the
            # picker trusts. Nothing here reaches the TV (docs/WRONGBOOK.md §10).
            if not correct:
                _record_wrong_item(cur, device_id=device_id, topic_id=topic_id, question_id=qid,
                                   stem=questions[qid]["stem"], day_key=session["day_key"],
                                   now_iso=now_iso)
            mastery.record_tv_answer(
                cur, device_id=device_id, topic_id=topic_id, correct=correct, flagged=flagged,
                session_id=session_id, question_id=qid, now_ms=now_ms, now_iso=now_iso,
                lock=db.lock_clause(),
            )

        if passed and quota["award_available"]:
            award_ms = limits.AWARD_MS
            new_ticket = ticket.new_ticket(device_id, session["day_key"], award_ms, now_ms=now_ms)
            ticket_row = {
                "jti": new_ticket.jti,
                "device_id": device_id,
                "session_id": session_id,
                "day_key": session["day_key"],
                "award_ms": award_ms,
                "issued_at": now_iso,
                "expires_at": limits.to_iso(new_ticket.exp),
            }
            cur.execute(
                "INSERT INTO ticket (jti, device_id, session_id, day_key, award_ms, issued_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                tuple(ticket_row[k] for k in
                      ("jti", "device_id", "session_id", "day_key", "award_ms", "issued_at", "expires_at")),
            )
            cur.execute(
                "UPDATE daily_state SET success_count = success_count + 1,"
                " awarded_ms = awarded_ms + ?, fail_count = 0, cooldown_until = NULL, updated_at = ?"
                " WHERE device_id = ? AND day_key = ?",
                (award_ms, now_iso, device_id, session["day_key"]),
            )
        elif passed:
            # Passed, but today's ceiling is already spent. Record the pass, award nothing, and do not
            # mint a ticket: the cap has to be worth more than the pass, or it is not a cap.
            cur.execute(
                "UPDATE daily_state SET fail_count = 0, updated_at = ? WHERE device_id = ? AND day_key = ?",
                (now_iso, device_id, session["day_key"]),
            )
        else:
            # B7 escape hatch: flagging "did not learn this" must not start a cooldown, or a child stuck
            # on an untaught topic is punished for telling the truth. Three in a row still reaches the
            # parent through topic_signal.unknown_streak.
            if not flagged_any:
                cooldown = limits.to_iso(now_ms + limits.COOLDOWN_MS)
                cur.execute(
                    "UPDATE daily_state SET fail_count = fail_count + 1, cooldown_until = ?,"
                    " updated_at = ? WHERE device_id = ? AND day_key = ?",
                    (cooldown, now_iso, device_id, session["day_key"]),
                )
            else:
                cur.execute(
                    "UPDATE daily_state SET fail_count = fail_count + 1, updated_at = ?"
                    " WHERE device_id = ? AND day_key = ?",
                    (now_iso, device_id, session["day_key"]),
                )

        cur.execute(
            "UPDATE quiz_session SET status = 'submitted', score = ?, passed = ?, award_ms = ?,"
            " ticket_jti = ? WHERE id = ?",
            (score, 1 if passed else 0, award_ms, ticket_row["jti"] if ticket_row else None, session_id),
        )

    ticket_text = None
    if ticket_row is not None:
        ticket_text = _sign_stored_ticket(ticket_row, signer)

    return {
        "session_id": session_id,
        "score": score,
        "total": total,
        "pass_line": need,
        "passed": passed,
        "award_ms": award_ms,
        "ticket": ticket_text,
        # What the result page's 错题本 draws. Only the wrong ones: a right answer the child already
        # saw in green does not need a second page.
        "review": _review(db, questions, accepted, graded, question_ids),
        # A number the television has to count down with. It is the server's cooldown, not the TV's
        # idea of ten minutes, or the two drift apart by however long the request took.
        "cooldown_seconds": limits.COOLDOWN_MS // 1000 if (not passed and not flagged_any) else 0,
        "flags": {
            "capped": bool(passed and award_ms == 0),
            "cooldown_applied": bool(not passed and not flagged_any),
            "unknown_flagged": sum(1 for _, _, _, flagged in graded if flagged),
        },
    }


def _question_ids(session: dict) -> list[int]:
    import json

    raw = session["question_ids"]
    return [int(x) for x in (json.loads(raw) if isinstance(raw, str) else raw)]


def _sign_stored_ticket(row: dict, signer) -> str:
    """Re-sign from the stored row. Deterministic ES256 (RFC 6979) means the TV sees the same string on
    a retry, which matters if it already stored half of it."""
    exp = limits.parse_iso(row["expires_at"])
    claims = ticket.Ticket(
        jti=row["jti"],
        did=row["device_id"],
        day=row["day_key"],
        ms=int(row["award_ms"]),
        iat=int(limits.parse_iso(row["issued_at"]).timestamp() * 1000),
        exp=int(exp.timestamp() * 1000) if exp else 0,
    )
    return ticket.issue(claims, signer)
