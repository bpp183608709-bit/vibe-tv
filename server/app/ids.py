"""ULIDs for anything the TV or the parent page sees.

26 characters, Crockford base32: 48 bits of millisecond timestamp followed by 80 bits of randomness.
Chosen over a UUID because the schema is CHAR(26) and because these ids appear in URLs
(`/v1/quiz/{id}/grade`) and in an audit trail a parent may read — sortable by time is worth the four
extra lines.

Not monotonic within a millisecond: two ids minted in the same millisecond sort by their random tail.
That is fine here — nothing depends on strict ordering, and a monotonic counter would need shared state
between workers.
"""

from __future__ import annotations

import os
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford: no I, L, O, U


def new_ulid(now_ms: int | None = None) -> str:
    value = int(time.time() * 1000) if now_ms is None else now_ms
    chars = []
    for _ in range(16):                       # 80 bits of randomness
        chars.append(ALPHABET[os.urandom(1)[0] & 31])
    random_part = "".join(chars)

    time_part = []
    for _ in range(10):                       # 48 bits, 10 characters, most significant first
        time_part.append(ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(time_part)) + random_part


def _selfcheck() -> None:
    a = new_ulid(0)
    assert len(a) == 26 and a.startswith("0" * 10), a
    assert all(c in ALPHABET for c in a), a
    ids = {new_ulid() for _ in range(2000)}
    assert len(ids) == 2000, "collision in 2000 ids"
    assert new_ulid(1_760_000_000_000) > new_ulid(1_760_000_000_000 - 1000), "time order broken"
    print("ulid: ok")


if __name__ == "__main__":
    _selfcheck()
