import asyncio, os, re, shlex, logging
log = logging.getLogger("voice")
HOST = os.environ.get("MAC_HOST", "100.126.45.100")
USER = os.environ.get("MAC_USER", "macbookprom1")
KEY = os.path.expanduser("~/.ssh/macbook_reza_key")
HOME = f"/Users/{USER}"
LOCAL = os.environ.get("VA_LOCAL", "0") == "1"
if LOCAL:
    HOME = os.path.expanduser("~")
    USER = os.path.basename(HOME)
TIMEOUT = 20
MAXOUT = 2000

# Pagar: pola yang ditolak (best-effort; bukan sandbox. Akses SSH tetap user utama).
DENY = [
    r"\bsudo\b", r"\bsu\b\s", r"\bdoas\b",
    r"\brm\b[^|;&]*\s-\w*[rRfF]", r"\brm\b[^|;&]*--(recursive|force)", r"\brmdir\b", r"\bunlink\b", r"\bsrm\b", r"\bshred\b",
    r"\bmkfs", r"\bdd\b", r"\bdiskutil\b", r"\bhdiutil\b", r"\bnewfs", r"\bfdisk\b", r"\bformat\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b", r"\bkillall\b", r"\bkill\s+-9\s+(-1|1)\b", r"\bpkill\b",
    r"\bchmod\b[^|;&]*-\w*R", r"\bchown\b[^|;&]*-\w*R", r"\bchflags\b", r"\bxattr\b[^|;&]*-\w*[cd]",
    r"\blaunchctl\b", r"\bcsrutil\b", r"\bnvram\b", r"\bpmset\b", r"\bdscl\b", r"\bsysadminctl\b", r"\bpasswd\b", r"\bsecurity\b\s+(delete|dump|find-generic|find-internet|export)",
    r":\(\)\s*\{", r">\s*/dev/(sd|disk|r?disk)", r"\bmv\b[^|;&]*\s/dev/null", r"\btruncate\b",
    r"(curl|wget)[^|;]*\|\s*(sudo\s+)?(ba|z)?sh", r"\bbase64\b[^|;]*(-d|--decode)[^;]*\|\s*(ba|z)?sh", r"\beval\b",
    r"\.ssh/", r"authorized_keys", r"\bkeychain", r"osascript[^|;]*(shut ?down|restart|log ?out|quit)",
    r"\bcrontab\b\s+-r", r"\btccutil\b", r"\bdefaults\s+delete\b", r"\bgit\s+push\b[^|;]*--force", r"\bscp\b|\brsync\b[^|;]*--delete",
]
DENY_RE = [re.compile(p, re.I) for p in DENY]
APP_RE = re.compile(r"^[\w .&+'\-]{1,60}$", re.U)

def blocked(cmd):
    c = " ".join(cmd.split())
    for r in DENY_RE:
        if r.search(c): return True
    return False

async def ssh(remote_cmd, timeout=TIMEOUT):
    if LOCAL:
        args = ["/bin/bash", "-c", remote_cmd]
    else:
      args = ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", f"{USER}@{HOST}", remote_cmd]
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout + 12)
    except asyncio.TimeoutError:
        try: p.kill()
        except Exception: pass
        return 124, "timeout"
    return p.returncode, out.decode("utf-8", "replace")

def _cut(s):
    s = s.strip()
    return s if len(s) <= MAXOUT else s[:MAXOUT] + "\n...(dipotong)"

def _unreachable(rc, out):
    return (rc == 255 and not LOCAL) or (rc == 124)

def _expand(folder):
    f = (folder or "").strip()
    if not f: return ""
    if f == "~" or f.startswith("~/"): f = HOME + f[1:]
    if not re.match(r"^[\w /.\-]+$", f) or ".." in f or not f.startswith(HOME): return None
    return f

async def handle(tool, args, ctx):
    if tool == "buka_aplikasi":
        nama = str(args.get("nama", "")).strip()
        if not APP_RE.match(nama): return {"error": "nama aplikasi tidak valid"}
        rc, out = await ssh("open -a " + shlex.quote(nama), 15)
    elif tool == "atur_volume":
        try: lv = max(0, min(100, int(args.get("level"))))
        except Exception: return {"error": "level harus angka 0-100"}
        rc, out = await ssh(f"osascript -e 'set volume output volume {lv}'", 10)
        if rc == 0: return {"ok": True, "volume": lv}
    elif tool == "cari_file":
        nama = str(args.get("nama", "")).strip()[:80]
        if not nama or "\n" in nama: return {"error": "nama kosong"}
        folder = _expand(args.get("folder"))
        if folder is None: return {"error": "folder harus di dalam home Mac"}
        cmd = "mdfind -name " + shlex.quote(nama) + (" -onlyin " + shlex.quote(folder) if folder else "") + " | head -15"
        rc, out = await ssh(cmd, 15)
        if rc == 0:
            lines = [l.replace(HOME, "~") for l in out.splitlines() if l.startswith("/")]
            return {"hasil": lines, "jumlah": len(lines)}
    elif tool == "jalankan_perintah":
        cmd = str(args.get("perintah", "")).strip()
        if not cmd or len(cmd) > 500 or "\x00" in cmd: return {"error": "perintah kosong atau terlalu panjang"}
        if blocked(cmd):
            log.info("kontrol_mac: perintah ditolak pagar")
            return {"error": "ditolak pagar keamanan", "petunjuk": "Bilang ke user perintah itu tidak boleh lewat suara."}
        # perl alarm = timeout di sisi Mac (macOS tidak punya `timeout`)
        rc, out = await ssh(f"perl -e 'alarm {TIMEOUT}; exec @ARGV' /bin/bash -c " + shlex.quote(cmd) + " 2>&1; echo __RC=$?", TIMEOUT)
        m = re.search(r"__RC=(\d+)\s*$", out)
        if m:   # sampai ke Mac
            code = int(m.group(1)); body = _cut(out[:m.start()])
            if code == 142: return {"error": f"timeout {TIMEOUT} detik", "keluaran": body}
            return {"keluaran": body or "(kosong)"} if code == 0 else {"kode": code, "keluaran": body}
    else:
        return {"error": "tool tidak dikenal"}
    if rc == 0: return {"ok": True}
    if _unreachable(rc, out): return {"error": "MacBook tidak terjangkau (mati/tidur/Tailscale off)"}
    return {"error": "gagal", "kode": rc, "detail": _cut(out)[:300]}
