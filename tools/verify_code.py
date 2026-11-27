#!/usr/bin/env python3
"""Dipakai installer: verifikasi tanda tangan kode akses (offline), lalu tulis passcode dan hash admin."""
import base64, json, os, sys, time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
d = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
here = os.path.dirname(os.path.abspath(__file__))
token = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
try:
    tag, body, sig = token.split(".")
    assert tag == "JB1"
    Ed25519PublicKey.from_public_bytes(d(open(os.path.join(here, "issuer_pub.txt")).read().strip())).verify(d(sig), body.encode())
    p = json.loads(d(body))
except (ValueError, AssertionError, InvalidSignature, KeyError):
    print("Kode akses tidak valid."); sys.exit(2)
if time.time() > p["exp"]:
    print("Kode akses sudah kedaluwarsa. Minta kode baru."); sys.exit(3)
def w(path, text):
    fd = os.open(os.path.expanduser(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, text.encode()); os.close(fd)
w("~/.voice_agent_passcode", p["pc"]); w("~/.admin_code_hash", p["ah"])
w("~/.jonobot_license", json.dumps({k: p[k] for k in ("id", "n", "iat", "exp")}))
print("Kode akses OK untuk:", p["n"], "(id", p["id"] + ", berlaku sampai", time.strftime("%Y-%m-%d", time.localtime(p["exp"])) + ")")
