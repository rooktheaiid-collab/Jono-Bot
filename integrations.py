"""Menu integrasi: simpan API key / token, status koneksi, OAuth (Gmail, OneDrive), permintaan akses.
Semua endpoint butuh passcode sesi (?pc=) DAN kode admin (header X-Admin). Rahasia disimpan di ~/.integrations (mode 600), tidak pernah dikembalikan ke klien."""
import os, json, time, hmac, hashlib, secrets, asyncio, re, subprocess, tempfile, logging
from urllib.parse import urlencode
import aiohttp
from aiohttp import web
import agent_core, memory

log = logging.getLogger("voice-agent")
HOME = os.path.expanduser("~")
SD = os.path.join(HOME, ".integrations")
CFG = os.path.join(memory.DIR, "integrations.json")
ADMIN_FILE = os.path.join(HOME, ".admin_code_hash")
PASSCODE_FILE = os.path.join(HOME, ".voice_agent_passcode")
BASE_URL = os.environ.get("PUBLIC_URL", "https://101-32-114-234.sslip.io")
REDIRECT = BASE_URL + "/api/integrasi/oauth/callback"
REQUEST_EMAIL = "rook.the.ai.id@gmail.com"
SECRET_PATHS = {   # nama rahasia -> file
    "gemini_key": os.path.join(HOME, ".gemini_api_key"),
    "elevenlabs_key": os.path.join(HOME, ".elevenlabs_key"),
}
SECRET_NAMES = ["gemini_key", "elevenlabs_key", "github_token", "gmail_client_secret", "gmail_refresh", "onedrive_client_secret", "onedrive_refresh", "tailscale_authkey"]
CFG_KEYS = {"tts_provider": ("elevenlabs", "gemini"), "el_voice_id": None, "github_repo": None, "gmail_client_id": None, "onedrive_client_id": None, "gmail_scope": ("baca", "baca_kirim"), "onedrive_scope": ("baca", "baca_tulis")}
_fail = {}

def _path(name): return SECRET_PATHS.get(name) or os.path.join(SD, name)
def _read(p):
    try: return open(p).read().strip()
    except Exception: return ""
def has(name): return bool(_read(_path(name)))
def _write_secret(name, val):
    p = _path(name); os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    if os.path.exists(p):
        try:
            bk = p + ".bak"; open(bk, "w").write(open(p).read()); os.chmod(bk, 0o600)
        except Exception: pass
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: f.write(val.strip())
    os.replace(tmp, p)
def _del_secret(name):
    try: os.remove(_path(name))
    except FileNotFoundError: pass

def cfg():
    try: return json.load(open(CFG))
    except Exception: return {}
def cfg_set(k, v):
    c = cfg(); c[k] = v
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    tmp = CFG + ".tmp"; json.dump(c, open(tmp, "w")); os.replace(tmp, CFG)

# ---------- auth ----------
def _ip(request): return request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote or "?"
def verify_admin(code):
    h = _read(ADMIN_FILE)
    if not h or "$" not in h or not code: return False
    salt, want = h.split("$", 1)
    got = hashlib.scrypt(code.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32).hex()
    return hmac.compare_digest(got, want)

def auth(request):
    now = time.time(); ip = _ip(request)
    fl = [t for t in _fail.get(ip, []) if now - t < 600]; _fail[ip] = fl
    if len(fl) >= 5: raise web.HTTPTooManyRequests(text="terlalu banyak percobaan, tunggu 10 menit")
    pc = _read(PASSCODE_FILE)
    ok = (not pc or hmac.compare_digest(request.query.get("pc", ""), pc)) and verify_admin(request.headers.get("X-Admin", ""))
    if not ok:
        fl.append(now); _fail[ip] = fl
        log.info("integrasi: auth gagal dari %s", ip)
        raise web.HTTPForbidden(text=json.dumps({"error": "kode admin/passcode salah"}), content_type="application/json")

# ---------- validasi / tes ----------
async def _http(method, url, **kw):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        async with s.request(method, url, **kw) as r:
            try: j = await r.json(content_type=None)
            except Exception: j = None
            return r.status, j

async def test_gemini(key=None):
    st, j = await _http("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1", headers={"x-goog-api-key": key or _read(_path("gemini_key"))})
    return st == 200, ("key valid" if st == 200 else f"ditolak (http {st})")

async def test_elevenlabs(key=None):
    key = key or _read(_path("elevenlabs_key"))
    st, j = await _http("GET", "https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key})
    if st == 200: return True, "key valid"
    det = json.dumps(j)[:200] if j else ""
    if st == 401 and "missing_permissions" in det: return True, "key valid tapi izin terbatas (aktifkan Text to Speech)"
    return False, f"ditolak (http {st})"

async def test_github(token=None):
    st, j = await _http("GET", "https://api.github.com/user", headers={"Authorization": "Bearer " + (token or _read(_path("github_token"))), "User-Agent": "voice-agent"})
    if st == 200 and j: return True, "login: " + str(j.get("login"))
    return False, f"ditolak (http {st})"

async def test_mac():
    if os.environ.get("VA_LOCAL", "0") == "1":
        return True, "Mode lokal: perintah Mac jalan langsung di mesin ini (tanpa SSH)"
    key = os.path.join(HOME, ".ssh", "macbook_reza_key")
    if not os.path.exists(key): return False, "key SSH Mac tidak ada"
    try:
        p = await asyncio.create_subprocess_exec("ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=accept-new", "macbookprom1@100.126.45.100", "echo ok",
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 12)
        return out.strip() == b"ok", ("SSH ke Mac OK" if out.strip() == b"ok" else "SSH gagal: " + err.decode()[:80])
    except Exception as e: return False, "SSH error " + type(e).__name__

async def tailscale_status():
    try:
        p = await asyncio.create_subprocess_exec("tailscale", "status", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(p.communicate(), 8)
        j = json.loads(out)
        peers = [{"nama": v.get("HostName"), "ip": (v.get("TailscaleIPs") or [""])[0], "online": bool(v.get("Online")), "os": v.get("OS")} for v in (j.get("Peer") or {}).values()]
        me = j.get("Self") or {}
        return {"state": j.get("BackendState"), "self": {"nama": me.get("HostName"), "ip": (me.get("TailscaleIPs") or [""])[0]}, "peers": peers}
    except Exception as e: return {"state": "error: " + type(e).__name__, "peers": []}

def oauth_state(name):
    secret = (_read(ADMIN_FILE) or "x").encode()
    nonce = secrets.token_hex(8); ts = str(int(time.time()))
    sig = hmac.new(secret, f"{name}|{ts}|{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{name}.{ts}.{nonce}.{sig}"
def oauth_state_ok(state):
    try:
        name, ts, nonce, sig = state.split(".")
        want = hmac.new((_read(ADMIN_FILE) or "x").encode(), f"{name}|{ts}|{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
        return name if hmac.compare_digest(sig, want) and time.time() - int(ts) < 600 else None
    except Exception: return None

GMAIL_SCOPES = {"baca": "https://www.googleapis.com/auth/gmail.readonly", "baca_kirim": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"}
OD_SCOPES = {"baca": "offline_access User.Read Files.Read", "baca_tulis": "offline_access User.Read Files.ReadWrite"}

def oauth_url(name):
    c = cfg()
    if name == "gmail":
        cid = c.get("gmail_client_id")
        if not cid or not has("gmail_client_secret"): return None
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "access_type": "offline", "prompt": "consent",
                "scope": GMAIL_SCOPES.get(c.get("gmail_scope", "baca"), GMAIL_SCOPES["baca"]), "state": oauth_state(name)})
    if name == "onedrive":
        cid = c.get("onedrive_client_id")
        if not cid or not has("onedrive_client_secret"): return None
        return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urlencode({"client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
                "scope": OD_SCOPES.get(c.get("onedrive_scope", "baca"), OD_SCOPES["baca"]), "state": oauth_state(name)})
    return None

async def oauth_exchange(name, code):
    c = cfg()
    if name == "gmail": url, cid, sec = "https://oauth2.googleapis.com/token", c.get("gmail_client_id"), _read(_path("gmail_client_secret"))
    else: url, cid, sec = "https://login.microsoftonline.com/common/oauth2/v2.0/token", c.get("onedrive_client_id"), _read(_path("onedrive_client_secret"))
    st, j = await _http("POST", url, data={"client_id": cid, "client_secret": sec, "code": code, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    if st == 200 and j and j.get("refresh_token"):
        _write_secret(name + "_refresh", j["refresh_token"]); cfg_set(name + "_connected_at", int(time.time())); return True
    return False

# ---------- status ----------
def mask_status():
    c = cfg()
    def chip(ok, partial=False): return "terhubung" if ok else ("setengah" if partial else "belum")
    el_prov = (c.get("tts_provider") or os.environ.get("TTS_PROVIDER", "gemini"))
    return {
        "gemini": {"status": chip(has("gemini_key")), "field": "gemini_key"},
        "elevenlabs": {"status": chip(has("elevenlabs_key")), "provider": el_prov, "voice": c.get("el_voice_id") or os.environ.get("EL_VOICE_ID", ""), "model": os.environ.get("EL_MODEL", "eleven_flash_v2_5")},
        "gmail": {"status": chip(has("gmail_refresh"), bool(c.get("gmail_client_id") and has("gmail_client_secret"))), "client_id_set": bool(c.get("gmail_client_id")), "secret_set": has("gmail_client_secret"), "scope": c.get("gmail_scope", "baca"), "redirect_uri": REDIRECT},
        "onedrive": {"status": chip(has("onedrive_refresh"), bool(c.get("onedrive_client_id") and has("onedrive_client_secret"))), "client_id_set": bool(c.get("onedrive_client_id")), "secret_set": has("onedrive_client_secret"), "scope": c.get("onedrive_scope", "baca"), "redirect_uri": REDIRECT},
        "github": {"status": chip(has("github_token")), "repo": c.get("github_repo", ""), "login": c.get("github_login", "")},
        "tailscale": {"authkey_set": has("tailscale_authkey")},
    }

# ---------- handlers ----------
async def h_status(request):
    auth(request)
    st = mask_status(); st["tailscale"].update(await tailscale_status())
    return web.json_response(st, headers={"Cache-Control": "no-store"})

async def h_set(request):
    auth(request)
    try: d = await request.json()
    except Exception: return web.json_response({"error": "json tidak valid"}, status=400)
    f, v = str(d.get("field", "")), str(d.get("value", "")).strip()
    if f in CFG_KEYS:
        allowed = CFG_KEYS[f]
        if allowed and v not in allowed: return web.json_response({"error": "nilai tidak valid"}, status=400)
        if f == "el_voice_id" and not re.fullmatch(r"[A-Za-z0-9]{10,40}", v): return web.json_response({"error": "voice id tidak valid"}, status=400)
        if f in ("gmail_client_id", "onedrive_client_id") and not re.fullmatch(r"[\w.\-~:/@]{8,200}", v): return web.json_response({"error": "client id tidak valid"}, status=400)
        if f == "github_repo" and not re.fullmatch(r"[\w.\-]+/[\w.\-]+", v): return web.json_response({"error": "format harus pemilik/repo"}, status=400)
        cfg_set(f, v); log.info("integrasi: config %s diubah", f); return web.json_response({"ok": True})
    if f in SECRET_NAMES and f not in ("gmail_refresh", "onedrive_refresh"):
        if len(v) < 8 or len(v) > 600 or re.search(r"\s", v): return web.json_response({"error": "nilai tidak valid"}, status=400)
        if f == "gemini_key":
            ok, msg = await test_gemini(v)
            if not ok: return web.json_response({"error": "Gemini menolak key: " + msg}, status=400)
        elif f == "elevenlabs_key":
            ok, msg = await test_elevenlabs(v)
            if not ok: return web.json_response({"error": "ElevenLabs menolak key: " + msg}, status=400)
        elif f == "github_token":
            ok, msg = await test_github(v)
            if not ok: return web.json_response({"error": "GitHub menolak token: " + msg}, status=400)
            cfg_set("github_login", msg.replace("login: ", ""))
        else: msg = "tersimpan"
        _write_secret(f, v); log.info("integrasi: rahasia %s diganti", f)
        return web.json_response({"ok": True, "info": msg})
    return web.json_response({"error": "field tidak dikenal"}, status=400)

async def h_clear(request):
    auth(request)
    d = await request.json()
    f = str(d.get("field", ""))
    if f in ("gemini_key",): return web.json_response({"error": "key Gemini tidak boleh dihapus (bot mati), ganti saja"}, status=400)
    if f in SECRET_NAMES: _del_secret(f); log.info("integrasi: rahasia %s dihapus", f); return web.json_response({"ok": True})
    return web.json_response({"error": "field tidak dikenal"}, status=400)

async def h_test(request):
    auth(request)
    n = (await request.json()).get("name")
    fn = {"gemini": test_gemini, "elevenlabs": test_elevenlabs, "github": test_github, "mac": test_mac}.get(n)
    if not fn: return web.json_response({"error": "tidak ada tes untuk itu"}, status=400)
    ok, msg = await fn(); return web.json_response({"ok": ok, "info": msg})

async def h_oauth_start(request):
    auth(request)
    n = request.query.get("name", "")
    u = oauth_url(n)
    if not u: return web.json_response({"error": "isi client id dan client secret dulu"}, status=400)
    return web.json_response({"url": u})

async def h_oauth_cb(request):
    name = oauth_state_ok(request.query.get("state", ""))
    if not name or not request.query.get("code"):
        return web.Response(text="Permintaan tidak valid atau kedaluwarsa.", status=400)
    ok = await oauth_exchange(name, request.query["code"])
    log.info("integrasi: oauth %s %s", name, "ok" if ok else "gagal")
    return web.Response(text=("<h3>%s terhubung. Tutup halaman ini.</h3>" % name) if ok else "<h3>Gagal menghubungkan. Coba lagi.</h3>", content_type="text/html")

def request_access(user, name, reason):
    name = re.sub(r"[^a-z0-9_ -]", "", (name or "").lower())[:40]; reason = (reason or "")[:300]
    with agent_core._lock, agent_core._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS access_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user TEXT, name TEXT, reason TEXT)")
        c.execute("INSERT INTO access_requests(ts,user,name,reason) VALUES(?,?,?,?)", (time.time(), user, name, reason))
    agent_core.notify(user, 0, "Permintaan akses: " + name,
                      f"Bot minta akses/key untuk '{name}'. Alasan: {reason}. Request lewat email ke {REQUEST_EMAIL} (buka menu Integrasi di app untuk menyambungkan).")
    return {"dicatat": True, "tujuan_email": REQUEST_EMAIL}

async def h_request(request):
    auth(request)
    d = await request.json()
    return web.json_response(request_access("reza", d.get("name"), d.get("reason")))

def routes():
    return [web.get("/api/integrasi/status", h_status), web.post("/api/integrasi/set", h_set), web.post("/api/integrasi/hapus", h_clear),
            web.post("/api/integrasi/tes", h_test), web.get("/api/integrasi/oauth/start", h_oauth_start), web.get("/api/integrasi/oauth/callback", h_oauth_cb),
            web.post("/api/integrasi/minta_akses", h_request)]
