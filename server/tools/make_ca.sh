#!/bin/sh
# A certificate authority for the quiz server, and a certificate for it to use.
#
#     server/tools/make_ca.sh quiz.qust.me 47.95.198.148
#
# Every argument becomes a subjectAltName; the first one is also the CN. Listing both the name and the
# address means one certificate serves the domain after DNS is pointed at the box and the address while
# it is not — which is exactly the state this deployment is in until the ICP filing clears.
#
# Writes, under server/local/tls/ (gitignored):
#
#     ca.pem      the CA certificate — also copied to app/src/main/res/raw/quiz_ca.pem, which is the
#                 only certificate the television trusts for the quiz connection (QuizHttp)
#     ca.key      the CA's private key; keep it, or the next server certificate cannot be issued
#     server.pem  the server's certificate, with the address in its subjectAltName
#     server.key  the server's private key
#
# Then: python -m server.app.api --port 8443 --tls-cert server/local/tls/server.pem \
#                                --tls-key server/local/tls/server.key
#
# **RSA for TLS, ECDSA for tickets.** The ticket key is P-256 because the APK only carries a public
# key (see server/tools/genkey.py). The TLS certificate is RSA because the televisions this runs on
# go back to Android 5.0, and a 2048-bit RSA certificate is the one every stack on that range agrees
# on; there is nothing to gain from a faster handshake on a request that happens twice a day.
#
# Regenerating the CA means rebuilding and reinstalling the APK: the old certificate is pinned, and a
# television with the old pin will refuse the new server. That is the point of pinning, and it is
# also why the CA key is worth keeping somewhere safe.
set -eu

if [ $# -lt 1 ]; then
    echo "usage: $0 <host-or-ip> [more names...]" >&2
    exit 2
fi

HOST="$1"
shift
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DIR="$ROOT/server/local/tls"
RAW="$ROOT/app/src/main/res/raw"

mkdir -p "$DIR" "$RAW"

if [ -f "$DIR/ca.key" ]; then
    echo "CA exists at $DIR/ca.key; issuing a new server certificate from it" >&2
else
    # Subject key identifier and basicConstraints are not decoration: Python 3.12+ (OpenSSL 3)
    # refuses a chain whose leaf has no authorityKeyIdentifier, and a certificate authority that
    # does not say it is one is refused by plenty of other stacks too. Android's own verifier is
    # lenient about it; every other client this ever meets is not.
    cat > "$DIR/ca.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Aerial Quiz CA
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
EOF
    openssl genrsa -out "$DIR/ca.key" 2048 2>/dev/null
    openssl req -x509 -new -key "$DIR/ca.key" -sha256 -days 3650 \
        -config "$DIR/ca.cnf" -extensions v3_ca -out "$DIR/ca.pem"
    rm -f "$DIR/ca.cnf"
    echo "new CA: $DIR/ca.pem"
fi

openssl genrsa -out "$DIR/server.key" 2048 2>/dev/null
openssl req -new -key "$DIR/server.key" -subj "/CN=$HOST" -out "$DIR/server.csr"

# Every name has to be in subjectAltName: a certificate that only carries a CN is refused by every
# TLS stack that follows RFC 6125, and Android has done so since 5.0. Anything that is not a dotted
# quad is a DNS name; anything that is, is an address.
ALT=""
for name in "$HOST" "$@"; do
    case "$name" in
        *[!0-9.]*) entry="DNS:$name" ;;
        *)         entry="IP:$name" ;;
    esac
    ALT="${ALT:+$ALT,}$entry"
done

cat > "$DIR/ext.cnf" <<EOF
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = $ALT
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
EOF

openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.pem" -CAkey "$DIR/ca.key" \
    -CAcreateserial -days 825 -sha256 -extfile "$DIR/ext.cnf" -out "$DIR/server.pem" 2>/dev/null
rm -f "$DIR/server.csr" "$DIR/ext.cnf"

cp "$DIR/ca.pem" "$RAW/quiz_ca.pem"

echo "server certificate: $DIR/server.pem  (CN=$HOST, $ALT)"
echo "pinned CA copied to: $RAW/quiz_ca.pem"
echo
echo "run:"
echo "  python -m server.app.api --host 0.0.0.0 --port 8443 \\"
echo "      --tls-cert $DIR/server.pem --tls-key $DIR/server.key"
