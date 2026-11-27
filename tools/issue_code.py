#!/usr/bin/env python3
"""Penerbit kode akses Jono Bot. HANYA untuk pemilik (Reza): butuh kunci privat penerbit.
Pakai: python3 tools/issue_code.py --name "Nama Penerima" --days 30 --key-file ~/jonobot_issuer.key
Output: KODE AKSES (token panjang, kirim ke penerima) dan KODE ADMIN (kirim lewat jalur lain, tampil sekali).
Perlu: pip install cryptography
"""
import argparse, base64, hashlib, json, os, secrets, sys, time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True); ap.add_argument("--days", type=int, default=30)
ap.add_argument("--key-file", required=True)
ap.add_argument("--log", default=os.path.expanduser("~/jonobot_issued.log"))
a = ap.parse_args()
seed = base64.urlsafe_b64decode(open(os.path.expanduser(a.key_file)).read().strip() + "==")
key = Ed25519PrivateKey.from_private_bytes(seed)
pc = secrets.token_urlsafe(18); admin = secrets.token_urlsafe(12); salt = os.urandom(16)
ah = salt.hex() + "$" + hashlib.scrypt(admin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()
now = int(time.time())
payload = {"v": 1, "id": secrets.token_hex(4), "n": a.name[:60], "iat": now, "exp": now + a.days * 86400, "pc": pc, "ah": ah}
body = b64(json.dumps(payload, separators=(",", ":")).encode())
token = "JB1." + body + "." + b64(key.sign(body.encode()))
with open(a.log, "a") as f:  # catatan penerbitan, tanpa rahasia
    f.write(json.dumps({"id": payload["id"], "nama": a.name, "iat": now, "exp": payload["exp"]}) + "\n")
print("ID kode   :", payload["id"], "| penerima:", a.name, "| berlaku sampai:", time.strftime("%Y-%m-%d", time.localtime(payload["exp"])))
print("\nKODE AKSES (kirim ke penerima):\n" + token)
print("\nKODE ADMIN menu Integrasi (kirim lewat jalur terpisah, tampil sekali):\n" + admin)
