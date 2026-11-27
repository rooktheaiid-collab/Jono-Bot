"""Plugin maker: generate plugin baru dengan Gemini -> cek statis (AST) -> uji di subprocess terbatas -> promosi ke plugins/ + reload.
Kode generate TIDAK PERNAH dieksekusi di proses utama sebelum lulus semua tahap. Semua riwayat tercatat di agent.db (tabel forge)."""
import os, re, ast, json, time, shutil, sqlite3, asyncio, logging, sys, tempfile
import aiohttp
import agent_core, plugins_loader

log = logging.getLogger("voice-agent")
BASE = os.path.dirname(os.path.abspath(__file__))
STAGING = os.path.join(BASE, "plugins_staging")
LIVE = plugins_loader.DIR
MAX_PER_DAY = int(os.environ.get("FORGE_MAX_PER_DAY", "3"))
MODELS = os.environ.get("FORGE_MODELS", "gemini-flash-latest,gemini-flash-lite-latest").split(",")
ALLOWED_IMPORTS = {"json", "re", "time", "math", "datetime", "zoneinfo", "asyncio", "urllib.parse", "html", "base64", "hashlib", "random", "statistics",
                   "collections", "itertools", "typing", "decimal", "textwrap", "string", "functools", "unicodedata", "calendar", "fractions", "xml.etree.ElementTree", "aiohttp"}
BAD_CALLS = {"eval", "exec", "compile", "open", "__import__", "input", "globals", "locals", "vars", "getattr", "setattr", "delattr", "breakpoint", "exit", "quit"}
BAD_ATTRS = {"create_subprocess_exec", "create_subprocess_shell", "open_connection", "start_server", "create_server", "create_connection", "open_unix_connection",
             "run_in_executor", "to_thread", "system", "popen", "memory", "loop"}
BAD_NAMES = {"plugins_loader", "agent_core", "memory", "server", "os", "sys", "subprocess", "shutil", "socket", "ctypes", "importlib", "builtins"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,30}$")

def _conn():
    c = agent_core._conn()
    c.execute("CREATE TABLE IF NOT EXISTS forge(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, user TEXT, name TEXT, request TEXT, status TEXT, detail TEXT)")
    return c

def _log(user, name, req, status, detail):
    try:
        with agent_core._lock, _conn() as c:
            c.execute("INSERT INTO forge(ts,user,name,request,status,detail) VALUES(?,?,?,?,?,?)", (time.time(), user, name, req[:500], status, str(detail)[:1500]))
    except Exception: pass
    log.info("forge: %s %s %s", name, status, str(detail)[:120])

def attempts_today():
    with agent_core._lock, _conn() as c:
        return c.execute("SELECT COUNT(*) FROM forge WHERE ts>? AND status IN ('mulai')", (time.time() - 86400,)).fetchone()[0]

# ---------- cek statis ----------
def static_check(code, network):
    try: tree = ast.parse(code)
    except SyntaxError as e: return f"syntax error: {e.msg} baris {e.lineno}"
    if "async def handle" not in code: return "tidak ada 'async def handle(tool, args, ctx)'"
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name not in ALLOWED_IMPORTS: return f"import terlarang: {a.name}"
                if a.name == "aiohttp" and not network: return "aiohttp dipakai tapi network tidak dideklarasikan"
        elif isinstance(n, ast.ImportFrom):
            if n.level or not n.module or n.module not in ALLOWED_IMPORTS and n.module.split(".")[0] not in {"datetime", "zoneinfo", "collections", "typing", "urllib", "xml"}:
                return f"import terlarang: {n.module}"
            if n.module in ("urllib",) : return "import terlarang: urllib"
            if n.module.startswith("urllib") and n.module != "urllib.parse": return f"import terlarang: {n.module}"
            if n.module == "aiohttp" and not network: return "aiohttp dipakai tapi network tidak dideklarasikan"
            if any(a.name == "*" for a in n.names): return "import * dilarang"
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in BAD_CALLS: return f"panggilan terlarang: {n.func.id}()"
        elif isinstance(n, ast.Attribute):
            if n.attr.startswith("__") or n.attr in BAD_ATTRS: return f"atribut terlarang: .{n.attr}"
        elif isinstance(n, ast.Name):
            if n.id in BAD_NAMES or (n.id.startswith("__") and n.id not in ("__name__",)): return f"nama terlarang: {n.id}"
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and ("__" in n.value or n.value == "memory"): return "string terlarang (dunder/memory)"
        elif isinstance(n, (ast.Global, ast.Nonlocal)): return "global/nonlocal dilarang"
    return None

def validate_manifest(m, existing_tools):
    if not isinstance(m, dict): return "manifest bukan objek"
    if not NAME_RE.match(str(m.get("name", ""))): return "nama plugin harus huruf kecil/angka/_ (3-31)"
    tools = m.get("tools")
    if not isinstance(tools, list) or not 1 <= len(tools) <= 5: return "tools harus 1-5"
    seen = set()
    for t in tools:
        if not isinstance(t, dict) or not NAME_RE.match(str(t.get("name", ""))): return "nama tool tidak valid"
        if t["name"] in existing_tools or t["name"] in seen: return f"nama tool bentrok: {t['name']}"
        seen.add(t["name"])
        if not str(t.get("description", "")).strip(): return "deskripsi tool kosong"
        p = t.get("parameters")
        if p is not None and (not isinstance(p, dict) or p.get("type") != "OBJECT"): return "parameters harus {type: OBJECT, properties: ...} dengan tipe UPPERCASE"
    return None

# ---------- generate ----------
SPEC = """Kamu menulis PLUGIN PYTHON untuk asisten suara. Kontrak plugin:
- plugin.py berisi 'async def handle(tool, args, ctx)' yang mengembalikan dict (JSON-serializable, ringkas, <20KB). ctx hanya boleh dipakai ctx["user"]; JANGAN pakai ctx lain.
- Tiap tool punya nama unik snake_case (huruf kecil/angka/_), deskripsi bahasa Indonesia, parameters JSON schema gaya Gemini (type UPPERCASE: OBJECT, STRING, INTEGER, NUMBER, BOOLEAN, ARRAY).
- Impor yang BOLEH hanya: json, re, time, math, datetime, zoneinfo, asyncio, urllib.parse, html, base64, hashlib, random, statistics, collections, itertools, typing, decimal, textwrap, string, functools, unicodedata, calendar, xml.etree.ElementTree, dan aiohttp (hanya kalau plugin perlu internet; set network=true).
- DILARANG: os, sys, subprocess, shutil, socket, open(), eval/exec/compile, getattr/setattr, atribut dunder, akses file, shell, pustaka lain. Tidak boleh baca/ubah plugin lain atau memori.
- Untuk data internet pakai API publik GRATIS tanpa API key (mis. open-meteo, wikipedia REST, exchange rate publik) lewat aiohttp dengan timeout ketat (maks 10 detik), tangani error dengan return {"error": "..."}. Jangan mengarang URL; pakai endpoint yang kamu yakin ada.
- Hasil dalam Bahasa Indonesia untuk teks yang dilihat user.
Balas HANYA JSON: {"name":..., "description":..., "network":bool, "prompt":"(opsional, 1-2 kalimat petunjuk kapan bot memakai tool)", "tools":[{"name","description","parameters"}], "code":"isi plugin.py", "tests":[{"tool":..., "args":{...}}]} dengan 1-3 tests memakai argumen contoh yang realistis."""

async def generate(request, feedback=None):
    key = agent_core._key()
    if not key: raise RuntimeError("API key Gemini tidak ada")
    prompt = SPEC + "\n\nPERMINTAAN KEMAMPUAN BARU: " + request[:800]
    if feedback: prompt += "\n\nPERCOBAAN SEBELUMNYA GAGAL dengan alasan: " + feedback[:500] + "\nPerbaiki dan kirim JSON lengkap lagi."
    last = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as s:
        for model in MODELS:
            try:
                async with s.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": key},
                                  json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}) as r:
                    if r.status != 200: last = f"http {r.status}"; continue
                    j = await r.json()
                txt = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought"))
                return json.loads(txt)
            except Exception as e:
                last = type(e).__name__
    raise RuntimeError("generate gagal: " + str(last))

# ---------- uji di subprocess ----------
async def run_tests(stage_dir, network, tests):
    tp = os.path.join(stage_dir, "_tests.json")
    json.dump(tests, open(tp, "w"))
    proc = await asyncio.create_subprocess_exec(sys.executable, "-I", os.path.join(BASE, "forge_harness.py"), stage_dir, "1" if network else "0", tp,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env={}, cwd=tempfile.gettempdir())
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
    except asyncio.TimeoutError:
        proc.kill(); return {"ok": False, "error": "timeout uji 60 detik", "results": []}
    txt = out.decode("utf-8", "replace")
    m = re.search(r"@@RESULT@@(.*)", txt)
    if not m: return {"ok": False, "error": "harness gagal: " + txt[-200:], "results": []}
    return json.loads(m.group(1))

async def _attempt(req, feedback):
    spec = await generate(req, feedback)
    name = str(spec.get("name", ""))
    code = str(spec.get("code", ""))
    net = bool(spec.get("network"))
    man = {"name": name, "version": "1.0", "enabled": True, "generated": True, "network": net,
           "description": str(spec.get("description", ""))[:300], "tools": spec.get("tools")}
    if spec.get("prompt"): man["prompt"] = str(spec["prompt"])[:400]
    existing = set(plugins_loader._tools.keys())
    err = validate_manifest(man, existing)
    if not err and os.path.exists(os.path.join(LIVE, name)): err = "plugin dengan nama itu sudah ada"
    if not err: err = static_check(code, net)
    tests = spec.get("tests") or []
    if not err and not (isinstance(tests, list) and tests and all(isinstance(t, dict) and t.get("tool") in {x["name"] for x in man["tools"]} for t in tests)):
        err = "tests tidak valid"
    return name, man, code, net, tests[:3], err

async def forge(user, request):
    """Pipeline lengkap. Mengembalikan (ok, pesan)."""
    _log(user, "-", request, "mulai", "")
    if attempts_today() > MAX_PER_DAY: 
        _log(user, "-", request, "ditolak", "batas harian"); return False, f"Batas {MAX_PER_DAY} plugin buatan per hari sudah tercapai."
    feedback, name = None, "-"
    stage = None
    for attempt in range(2):
        try:
            name, man, code, net, tests, err = await _attempt(request, feedback)
            if err:
                feedback = err; _log(user, name, request, "ditolak_cek", err); continue
            stage = os.path.join(STAGING, name)
            shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage)
            open(os.path.join(stage, "plugin.py"), "w").write(code)
            json.dump(man, open(os.path.join(stage, "manifest.json"), "w"), ensure_ascii=False, indent=1)
            res = await run_tests(stage, net, tests)
            bad = [r for r in res.get("results", []) if r.get("has_error")]
            if not res.get("ok") or bad:
                feedback = res.get("error") or ("tool mengembalikan error: " + bad[0]["result"][:200])
                _log(user, name, request, "gagal_uji", feedback)
                shutil.rmtree(stage, ignore_errors=True); stage = None; continue
            # lulus: promosi
            dst = os.path.join(LIVE, name)
            os.makedirs(os.path.join(stage, "..", "..", "plugins"), exist_ok=True)
            os.remove(os.path.join(stage, "_tests.json"))
            open(os.path.join(stage, "GENERATED"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
            shutil.move(stage, dst); stage = None
            info = plugins_loader.load_all()
            ok_loaded = any(p["name"] == name for p in info["plugins"]) and name not in info["errors"] and not any(name in k for k in info["errors"])
            if not ok_loaded:
                shutil.rmtree(dst, ignore_errors=True); plugins_loader.load_all()
                _log(user, name, request, "rollback", "gagal dimuat setelah promosi"); return False, f"Plugin {name} gagal dimuat, dibatalkan."
            _log(user, name, request, "live", "tools=" + ",".join(t["name"] for t in man["tools"]) + " tes=" + json.dumps(res["results"], ensure_ascii=False)[:600])
            return True, f"Plugin '{name}' sudah aktif. Kemampuan baru: " + "; ".join(f"{t['name']} ({t['description'][:80]})" for t in man["tools"]) + ". Sesi suara baru memakai tool ini otomatis."
        except Exception as e:
            feedback = f"{type(e).__name__}: {str(e)[:150]}"; _log(user, name, request, "error", feedback)
            if stage: shutil.rmtree(stage, ignore_errors=True); stage = None
    return False, "Gagal bikin plugin setelah 2 percobaan. Alasan terakhir: " + str(feedback)[:200]

def list_generated():
    out = []
    for d in sorted(os.listdir(LIVE)) if os.path.isdir(LIVE) else []:
        if os.path.exists(os.path.join(LIVE, d, "GENERATED")):
            out.append(d)
    return out

def remove_generated(name):
    if not NAME_RE.match(name or "") or name not in list_generated(): return {"error": "bukan plugin buatan (atau tidak ada)"}
    shutil.move(os.path.join(LIVE, name), os.path.join(STAGING, name + "_dihapus_" + str(int(time.time()))))
    plugins_loader.load_all(); _log("-", name, "", "dihapus", "")
    return {"dihapus": name}

def record_result(user, title, text, status):
    """Taruh hasil ke antrean laporan sesi berikutnya (memakai tabel tasks/runs milik agent_core)."""
    with agent_core._lock, agent_core._conn() as c:
        tid = c.execute("INSERT INTO tasks(user,title,instruction,kind,spec,next_run,enabled,created,last_run,last_status) VALUES(?,?,?,?,?,?,0,?,?,?)",
                        (user, title[:80], "plugin_maker", "sekali", "0", 0, time.time(), time.time(), status)).lastrowid
        rid = c.execute("INSERT INTO runs(task_id,user,started,finished,status,steps,result) VALUES(?,?,?,?,?,0,?)", (tid, user, time.time(), time.time(), status, text)).lastrowid
    agent_core.notify(user, rid, title, text)
