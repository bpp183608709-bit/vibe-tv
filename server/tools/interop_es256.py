"""Prove that our stdlib ECDSA interoperates with the rest of the world.

The TV verifies with Android's JCA (``Signature.getInstance("SHA256withECDSA")``), which speaks
X9.62/DER over P-256 exactly like OpenSSL does. So OpenSSL is the stand-in we can run here: if a
signature made by ``server/app/es256.py`` verifies under ``openssl dgst -verify`` and a signature made
by OpenSSL verifies under ours, the TV will accept both.

Four directions, all asserted:

  1. our key   -> our signature   -> openssl verifies
  2. openssl key -> our signature -> openssl verifies   (key parsing: SEC1 PEM, as `openssl ecparam` writes)
  3. openssl key -> openssl signature -> we verify      (key parsing: SPKI PEM)
  4. a flipped bit in a genuine signature must not verify

Run: ``.venv/bin/python server/tools/interop_es256.py``
Artifacts land in ``server/local/interop/`` (gitignored) because this sandbox refuses to delete files,
so the script must not depend on cleaning up after itself.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from server.app import es256  # noqa: E402

WORK = Path(__file__).resolve().parent.parent / "local" / "interop"
MESSAGE = b"AQ1.eyJ2IjoxLCJhbGciOiJFUzI1NiJ9"


def openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True)


def verify_with_openssl(public_pem: Path, signature: bytes, message: bytes) -> bool:
    (WORK / "msg.bin").write_bytes(message)
    (WORK / "sig.der").write_bytes(signature)
    result = openssl(
        "dgst", "-sha256",
        "-verify", str(public_pem),
        "-signature", str(WORK / "sig.der"),
        str(WORK / "msg.bin"),
    )
    return b"Verified OK" in result.stdout


def main() -> int:
    if shutil.which("openssl") is None:
        print("openssl not found; cannot run the interop check", file=sys.stderr)
        return 2
    WORK.mkdir(parents=True, exist_ok=True)

    # 1. our key, our signature, openssl's opinion
    private_key, public_key = es256.generate()
    our_public_pem = WORK / "ours.pub.pem"
    our_private_pem = WORK / "ours.priv.pem"
    our_public_pem.write_bytes(es256.public_to_pem(public_key))
    our_private_pem.write_bytes(es256.private_to_pem(private_key, public_key))
    if not verify_with_openssl(our_public_pem, es256.sign(private_key, MESSAGE), MESSAGE):
        print("FAIL: openssl rejected our signature", file=sys.stderr)
        return 1
    # The private key file has to be readable by openssl too, or the second direction is untested.
    if openssl("pkey", "-in", str(our_private_pem), "-noout").returncode != 0:
        print("FAIL: openssl cannot read our PKCS#8 private key", file=sys.stderr)
        return 1

    # 2. and 3. keys made by openssl, in both SEC1 (private) and SPKI (public) form
    foreign_private = WORK / "foreign.priv.pem"
    foreign_public = WORK / "foreign.pub.pem"
    generated = openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(foreign_private))
    if generated.returncode != 0:
        print(f"FAIL: openssl keygen: {generated.stderr.decode()}", file=sys.stderr)
        return 1
    exported = openssl("ec", "-in", str(foreign_private), "-pubout", "-out", str(foreign_public))
    if exported.returncode != 0:
        print(f"FAIL: openssl pubout: {exported.stderr.decode()}", file=sys.stderr)
        return 1

    foreign_scalar = es256.load_private_pem(foreign_private.read_bytes())
    foreign_point = es256.load_public_pem(foreign_public.read_bytes())
    if es256.public_point(foreign_scalar) != foreign_point:
        print("FAIL: the parsed private key does not derive the parsed public key", file=sys.stderr)
        return 1

    if not verify_with_openssl(foreign_public, es256.sign(foreign_scalar, MESSAGE), MESSAGE):
        print("FAIL: openssl rejected a signature made with its own key", file=sys.stderr)
        return 1

    (WORK / "msg.bin").write_bytes(MESSAGE)
    signed = openssl(
        "dgst", "-sha256", "-sign", str(foreign_private),
        "-out", str(WORK / "openssl.der"), str(WORK / "msg.bin"),
    )
    if signed.returncode != 0:
        print(f"FAIL: openssl sign: {signed.stderr.decode()}", file=sys.stderr)
        return 1
    foreign_signature = (WORK / "openssl.der").read_bytes()
    if not es256.verify(foreign_point, MESSAGE, foreign_signature):
        print("FAIL: we rejected openssl's signature", file=sys.stderr)
        return 1

    # 4. one flipped bit
    broken = bytearray(foreign_signature)
    broken[-1] ^= 0x01
    if es256.verify(foreign_point, MESSAGE, bytes(broken)):
        print("FAIL: a corrupted signature verified", file=sys.stderr)
        return 1
    if es256.verify(foreign_point, MESSAGE + b" ", foreign_signature):
        print("FAIL: a signature verified against a different message", file=sys.stderr)
        return 1

    print("es256 interop: ok (4 directions, openssl as the stand-in for Android JCA)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
