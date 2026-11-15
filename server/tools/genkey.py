"""Generate the ES256 key pair that signs award tickets.

    python3 -m server.tools.genkey --out server/local

Writes `ticket-signing-key.pem` (PKCS#8, chmod 600, stays on the ECS and in the offline backup, never
in git) and prints the public half twice, in the two shapes the two sides need:

  * SPKI base64, one line — for the Kotlin constant. `KeyFactory.getInstance("EC")` +
    `X509EncodedKeySpec(Base64.decode(...))` turns it into a `PublicKey` and
    `Signature.getInstance("SHA256withECDSA")` verifies our tickets. No library involved, and nothing
    in the APK can mint a ticket.
  * raw X||Y hex — for eyeballing, and for a client that would rather build the point itself.

The private key is the whole security of the scheme: anyone holding it can mint 30-minute tickets, and
no amount of client-side checking can tell a minted one from a real one.

`server/tools/interop_es256.py` is the proof that these keys work with the rest of the world; run it
after generating a pair on a new machine.
"""

from __future__ import annotations

import argparse
import base64
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from server.app import es256  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the ticket signing key pair (P-256).")
    parser.add_argument("--out", default=".", help="directory for the private key (default: cwd)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing key")
    args = parser.parse_args(argv)

    out_dir = pathlib.Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    private_path = out_dir / "ticket-signing-key.pem"
    if private_path.exists() and not args.force:
        print(f"{private_path} exists; pass --force to replace it", file=sys.stderr)
        return 1

    private_key, public_key = es256.generate()
    private_path.write_bytes(es256.private_to_pem(private_key, public_key))
    private_path.chmod(0o600)

    public_pem = es256.public_to_pem(public_key)
    spki_der = base64.b64decode(b"".join(
        line for line in public_pem.splitlines() if line and not line.startswith(b"-----")
    ))

    print(f"private key  {private_path}  (chmod 600, never commit)")
    print()
    print("public key, base64 SPKI — for the Kotlin constant:")
    print(base64.b64encode(spki_der).decode("ascii"))
    print()
    print("public key, raw X||Y hex (64 bytes):")
    print(es256.public_raw(public_key).hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
