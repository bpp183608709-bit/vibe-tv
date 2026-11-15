"""The award ticket: what the server signs after a quiz is passed, and what the TV verifies offline.

Spec: docs/QUIZ.md §7. This module is the *format* and nothing else — no database, no HTTP, no
knowledge of who passed. Its only job is that a TV holding `AQ1.<payload>.<sig>` can decide, with no
network, whether to add minutes to today.

Wire form, ASCII, no line breaks::

    AQ1.<base64url(payload JSON, unpadded)>.<base64url(signature)>

The signature covers the bytes ``AQ1.<base64url payload>`` — that is, everything left of the last dot,
including the prefix. Re-encoding the payload with a different key order therefore breaks the
signature, so a payload can be copied but not edited.

Two signers share the format and differ only in `alg`:

  * ``ES256`` — ECDSA P-256 / SHA-256, DER signature. **What production uses.** The app ships the
    public key only, so unpacking the APK yields nothing that can mint a ticket.
  * ``HS256`` — HMAC-SHA-256 with a shared secret. Kept for local development and for a TV build that
    cannot do EC. The secret has to be inside the APK, so anyone who unpacks it can sign freely; do
    not ship this one.

The TV re-checks the award against an allow-list, the device, the day and a local set of spent `jti`.
Those checks are not redundant with the server's: they are what makes the minute count survive a
restored backup, a copied file, or a replayed ticket.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from server.app.ids import new_ulid

PREFIX = "AQ1"
PAYLOAD_VERSION = 1

#: A passed group is 30 minutes, and passing means 80% right (B2): four of five, or five of five.
#: There is deliberately no half award — an earlier draft had one, and a single number is easier for a
#: child to understand and for this file to police. Nothing else is signable, so a client that only
#: accepts these values cannot be talked into accepting a larger one.
AWARD_FULL_MS = 30 * 60_000
ALLOWED_AWARD_MS = (AWARD_FULL_MS,)

#: A ticket outlives the day it was issued on only in theory: `day` is checked first (see `verify`).
TICKET_TTL_MS = 24 * 60 * 60_000

#: Allowed clock difference between the TV and the server, for the `iat` check.
CLOCK_SKEW_MS = 5 * 60_000


class TicketError(Exception):
    """A ticket the TV must refuse. `reason` is for the log, not for the child."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Ticket:
    """The signed claims. `ms` is the only number that turns into watching time."""

    jti: str          # one per award; the TV keeps spent ones so a copied ticket is worthless
    did: str          # device that earned it
    day: str          # "YYYY-MM-DD", the TV's local day at issue time
    ms: int           # AWARD_FULL_MS; the only amount a pass can be worth
    iat: int          # issued at, epoch milliseconds
    exp: int          # expires at, epoch milliseconds

    def claims(self, alg: str) -> dict:
        return {
            "v": PAYLOAD_VERSION,
            "alg": alg,
            "jti": self.jti,
            "did": self.did,
            "day": self.day,
            "ms": self.ms,
            "iat": self.iat,
            "exp": self.exp,
        }


def new_ticket(device_id: str, day: str, award_ms: int, now_ms: int | None = None) -> Ticket:
    """Award `award_ms` to `device_id` for `day`. Refuses an amount that is not in the allow-list."""
    if award_ms not in ALLOWED_AWARD_MS:
        raise ValueError(f"award must be one of {ALLOWED_AWARD_MS}, got {award_ms}")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return Ticket(
        jti=new_ulid(now),
        did=device_id,
        day=day,
        ms=award_ms,
        iat=now,
        exp=now + TICKET_TTL_MS,
    )


# ---- the two signers ----

class HmacSigner:
    """HMAC-SHA-256 over a shared secret. Development and fallback only — see the module docstring."""

    alg = "HS256"

    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("secret must be at least 32 bytes")
        self._secret = secret

    def sign(self, message: bytes) -> bytes:
        return hmac.new(self._secret, message, hashlib.sha256).digest()

    def verify(self, message: bytes, signature: bytes) -> bool:
        return hmac.compare_digest(self.sign(message), signature)


class Es256Signer:
    """ECDSA P-256 / SHA-256, DER signature. The private key never leaves the server.

    Uses `server/app/es256.py` — about 300 lines of standard-library ECDSA, chosen so the server
    deploys with nothing but Python. Interop with Android's JCA is proven by
    `server/tools/interop_es256.py`, which round-trips signatures through openssl in all four
    directions.
    """

    alg = "ES256"

    def __init__(self, private_key_pem: bytes | None = None, public_key_pem: bytes | None = None):
        from server.app import es256

        self._es256 = es256
        if private_key_pem is not None:
            self._private = es256.load_private_pem(private_key_pem)
            self._public = es256.public_point(self._private)
        elif public_key_pem is not None:
            self._private = None
            self._public = es256.load_public_pem(public_key_pem)
        else:
            raise ValueError("one of private_key_pem / public_key_pem is required")

    def sign(self, message: bytes) -> bytes:
        if self._private is None:
            raise RuntimeError("signing needs the private key")
        return self._es256.sign(self._private, message)

    def verify(self, message: bytes, signature: bytes) -> bool:
        return self._es256.verify(self._public, message, signature)


# ---- base64url ----

def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except (binascii.Error, ValueError) as exc:
        raise TicketError("bad-base64") from exc


# ---- issuing and verifying ----

def issue(ticket: Ticket, signer) -> str:
    """The string the server hands back with a passed quiz."""
    body = _b64u_encode(json.dumps(ticket.claims(signer.alg), separators=(",", ":")).encode("utf-8"))
    signed = f"{PREFIX}.{body}"
    return f"{signed}.{_b64u_encode(signer.sign(signed.encode('ascii')))}"


def verify(
    raw: str,
    signer,
    *,
    device_id: str,
    day_key: str,
    now_ms: int | None = None,
    spent_jti: frozenset[str] | set[str] = frozenset(),
) -> Ticket:
    """Decide whether `raw` may be turned into minutes on this TV, today.

    Raises `TicketError` with a short reason. The order matters — signature before claims, and the
    day before the expiry: a ticket from yesterday is void even though it has hours left on it,
    because the counter it adds to was reset at four in the morning.
    """
    if not isinstance(raw, str) or not raw:
        raise TicketError("empty")
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise TicketError("bad-shape")

    body, signature = parts[1], _b64u_decode(parts[2])
    signed = f"{PREFIX}.{body}".encode("ascii")
    if not signer.verify(signed, signature):
        raise TicketError("bad-signature")

    try:
        claims = json.loads(_b64u_decode(body))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TicketError("bad-payload") from exc
    if not isinstance(claims, dict):
        raise TicketError("bad-payload")
    if claims.get("v") != PAYLOAD_VERSION:
        raise TicketError("bad-version")
    if claims.get("alg") != signer.alg:
        # A ticket signed with one algorithm must not be accepted by the other's verifier.
        raise TicketError("bad-alg")

    ms = claims.get("ms")
    if ms not in ALLOWED_AWARD_MS:
        raise TicketError("bad-award")
    if claims.get("did") != device_id:
        raise TicketError("wrong-device")
    if claims.get("day") != day_key:
        raise TicketError("wrong-day")

    now = int(time.time() * 1000) if now_ms is None else now_ms
    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, int) or not isinstance(exp, int):
        raise TicketError("bad-time")
    if iat > now + CLOCK_SKEW_MS:
        raise TicketError("not-yet")
    if now >= exp:
        raise TicketError("expired")

    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        raise TicketError("bad-jti")
    if jti in spent_jti:
        raise TicketError("spent")

    return Ticket(jti=jti, did=claims["did"], day=claims["day"], ms=ms, iat=iat, exp=exp)


def _selfcheck() -> None:
    """python -m server.app.ticket — round-trips a ticket and refuses the ways it should be refused."""
    signer = HmacSigner(b"development-only-secret-32-bytes!!")
    now = 1_760_000_000_000
    ticket = new_ticket("01JC0DEVICE000000000000000", "2026-11-11", AWARD_FULL_MS, now_ms=now)
    raw = issue(ticket, signer)

    ok = verify(raw, signer, device_id=ticket.did, day_key="2026-11-11", now_ms=now + 1_000)
    assert ok == ticket, ok

    def refuses(reason: str, **kwargs) -> None:
        try:
            verify(raw, signer, **kwargs)
        except TicketError as exc:
            assert exc.reason == reason, f"expected {reason}, got {exc.reason}"
            return
        raise AssertionError(f"expected {reason}, but the ticket was accepted")

    base = dict(device_id=ticket.did, day_key="2026-11-11", now_ms=now + 1_000)
    refuses("wrong-device", **{**base, "device_id": "01JC0OTHER0000000000000000"})
    refuses("wrong-day", **{**base, "day_key": "2026-11-12"})
    refuses("spent", **{**base, "spent_jti": {ticket.jti}})
    refuses("expired", **{**base, "now_ms": ticket.exp + 1})
    # A copied payload with an extra zero on the award: the signature no longer covers it.
    forged = json.loads(_b64u_decode(raw.split(".")[1]))
    forged["ms"] = AWARD_FULL_MS * 100
    body = _b64u_encode(json.dumps(forged, separators=(",", ":")).encode())
    try:
        verify(f"{PREFIX}.{body}.{raw.split('.')[2]}", signer, **base)
    except TicketError as exc:
        assert exc.reason == "bad-signature", exc.reason
    else:
        raise AssertionError("a re-signed payload was accepted")

    print("ticket: ok")


def _selfcheck_es256() -> None:
    """The same round trip with the production signer, which is the one that ships."""
    from server.app import es256

    private_key, public_key = es256.generate()
    signer = Es256Signer(private_key_pem=es256.private_to_pem(private_key, public_key))
    verifier = Es256Signer(public_key_pem=es256.public_to_pem(public_key))

    now = 1_760_000_000_000
    ticket = new_ticket("01JC0DEVICE000000000000000", "2026-11-11", AWARD_FULL_MS, now_ms=now)
    raw = issue(ticket, signer)
    assert verify(raw, verifier, device_id=ticket.did, day_key="2026-11-11", now_ms=now + 1_000) == ticket

    # A verifier handed the wrong key must not accept it — the check that makes a copied key useless.
    other_key, other_public = es256.generate()
    try:
        verify(raw, Es256Signer(public_key_pem=es256.public_to_pem(other_public)), device_id=ticket.did,
               day_key="2026-11-11", now_ms=now + 1_000)
    except TicketError as exc:
        assert exc.reason == "bad-signature", exc.reason
    else:
        raise AssertionError("a ticket verified under an unrelated key")

    # An HS256 ticket must not be accepted by the ES256 verifier even though both are "valid".
    hmac_ticket = issue(ticket, HmacSigner(b"development-only-secret-32-bytes!!"))
    try:
        verify(hmac_ticket, verifier, device_id=ticket.did, day_key="2026-11-11", now_ms=now + 1_000)
    except TicketError as exc:
        assert exc.reason == "bad-signature", exc.reason
    else:
        raise AssertionError("an HS256 ticket passed the ES256 verifier")

    print("ticket es256: ok")


if __name__ == "__main__":
    _selfcheck()
    _selfcheck_es256()
