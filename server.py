import asyncio, json, os, sys, logging, re, subprocess, time
from aiohttp import web, WSMsgType
import websockets
import memory
import tts_el, plugins_loader, agent_core, integrations
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("voice")
KEY_FILE = os.path.expanduser(os.environ.get("GEMINI_KEY_FILE", "~/.gemini_api_key"))
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-live")
VOICE = os.environ.get("GEMINI_VOICE", "")
PASSCODE_FILE = os.path.expanduser("~/.voice_agent_passcode")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8780"))
SYSTEM = """Kamu adalah Instinct, asisten pribadi Reza. Kamu ngobrol lewat suara secara real-time.
Gaya: santai, hangat, to the point, Bahasa Indonesia gaul, sesekali humor kering. Bukan formal. Panggil user "bro".
Tentang user: Reza Agus Afandi, 38 tahun, tinggal di Cileungsi, Jawa Barat. Perusahaannya PT Pinnacle Analytics Indonesia (PAI).
Proyek yang sedang berjalan:
1) Bot sinyal trading di VPS yang sama dengan aplikasi suara ini. Memantau BTC dan emas (XAUUSDT) di Binance testnet, mode sinyal saja. Sinyal masuk lewat bot Telegram @Tukang_Trading369_bot.
2) Baru selesai submit dokumen prakualifikasi tender BKPM 2027 atas nama PAI.
Aturan:
- Jawaban singkat kecuali diminta detail.
- Jangan pernah menjanjikan profit trading.
- Kalau diminta hal yang butuh aksi nyata (ubah server, kirim pesan, bayar), bilang itu harus lewat Instinct di chat Telegram atau WhatsApp, kamu di sini cuma ngobrol suara.
- Jangan mengarang fakta di luar konteks ini. Kalau tidak tahu, bilang tidak tahu.
- Di awal sesi, perkenalkan diri singkat: "Halo bro, gue Instinct".
Alat: kamu punya tool. Untuk berita, kejadian terkini, harga, skor, atau fakta yang bisa sudah berubah, WAJIB cari di internet (pakai Google Search bawaan; kalau tidak ada, pakai cari_web dengan jenis 'berita' untuk berita), lalu rangkum singkat dan santai sambil sebut nama sumbernya. Kalau hasilnya kosong, bilang "gak nemu" dengan jujur. Pakai waktu_sekarang untuk jam/tanggal dan cuaca untuk cuaca. Pakai simpan_fakta kalau user minta kamu mengingat sesuatu. Kamu punya ingatan lintas sesi (lihat bagian INGATAN kalau ada); pakai untuk nyambung dengan obrolan sebelumnya. Jangan bilang sedang memanggil tool, langsung jawab."""
def _persona():
    try:
        t = open(os.path.join(os.environ.get("VA_DATA", os.path.expanduser("~/voice-agent/data")), "persona.txt")).read().strip()
        if len(t) > 200: return t
    except OSError: pass
    return SYSTEM

GREETING = "Sesi suara baru dimulai. Sapa Reza sekarang sesuai instruksi pembukaan, singkat."
URL = ("wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage."
       "v1beta.GenerativeService.BidiGenerateContent?key=")

VOICES = ["Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]
BOT_LOG = os.path.expanduser("~/binance-futures-bot/bot.log")
CORS_ORIGIN = "https://appassets.local"

def _svc(prop):
    try:
        return subprocess.run(["systemctl", "show", "trading-bot", "-p", prop, "--value"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def _clean(line):
    line = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "[ip]", line)
    return line[:170]

def bot_info():
    info = {"active": _svc("ActiveState") or "unknown", "since": _svc("ActiveEnterTimestamp"),
            "lines": [], "last_signal": None, "log_age_s": None}
    try:
        info["log_age_s"] = int(time.time() - os.path.getmtime(BOT_LOG))
        with open(BOT_LOG, "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - 60000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return info
    sig = [l for l in lines if " INFO SINYAL " in l]
    if sig: info["last_signal"] = _clean(sig[-1])
    info["lines"] = [_clean(l) for l in lines if " INFO " in l or " WARNING " in l or " ERROR " in l][-8:]
    return info

@web.middleware
async def cors(request, handler):
    resp = await handler(request)
    if request.path in ("/status", "/api/bot") and request.headers.get("Origin") in (CORS_ORIGIN,):
        resp.headers["Access-Control-Allow-Origin"] = CORS_ORIGIN
        resp.headers["Vary"] = "Origin"
    return resp

async def api_bot(request):
    pc = read(PASSCODE_FILE)
    if pc and request.query.get("pc") != pc:
        return web.json_response({"error": "passcode salah"}, status=403)
    return web.json_response(bot_info(), headers={"Cache-Control": "no-store"})

def read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return ""

def _auth(request):
    pc = read(PASSCODE_FILE)
    return (not pc) or request.query.get("pc") == pc

async def api_export(request):
    if not _auth(request): return web.json_response({"error": "passcode salah"}, status=403)
    data = memory.export_targz()
    return web.Response(body=data, content_type="application/gzip", headers={
        "Content-Disposition": 'attachment; filename="voice-memory-%s.tar.gz"' % time.strftime("%Y%m%d-%H%M%S"), "Cache-Control": "no-store"})

async def api_memory(request):
    if not _auth(request): return web.json_response({"error": "passcode salah"}, status=403)
    u = memory.clean_user(request.query.get("user"))
    return web.json_response({"user": u, "fakta": memory.list_facts(u), "ringkasan": memory.get_summary(u), "ctx_chars": len(memory.context(u))}, headers={"Cache-Control": "no-store"})

async def api_plugins(request):
    if not _auth(request): return web.json_response({"error": "passcode salah"}, status=403)
    return web.json_response(plugins_loader.info())

async def api_plugins_reload(request):
    if not _auth(request): return web.json_response({"error": "passcode salah"}, status=403)
    return web.json_response(plugins_loader.load_all())

async def index(request):
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

async def status(request):
    return web.json_response({"key_present": bool(read(KEY_FILE)), "model": MODEL, "voices": VOICES, "voice": VOICE or "default"})

_native = {"ok": False, "until": 0}
async def native_ok(key, decls):
    """Cek (di-cache) apakah Google Search bawaan Gemini Live bisa dipakai (kuota/plan API key). Gagal -> pakai cari_web."""
    if time.time() < _native["until"]:
        return _native["ok"]
    ok = False
    try:
        probe = {"setup": {"model": f"models/{MODEL}", "generationConfig": {"responseModalities": ["AUDIO"]},
                 "tools": [{"googleSearch": {}}, {"functionDeclarations": [d for d in decls if d["name"] != "cari_web"]}]}}
        async with websockets.connect(URL + key, max_size=2**20) as w:
            await w.send(json.dumps(probe))
            ok = "setupComplete" in json.loads(await asyncio.wait_for(w.recv(), 8))
    except Exception as e:
        log.info("native search tidak tersedia: %s", type(e).__name__)
    _native.update(ok=ok, until=time.time() + (6 * 3600 if ok else 1800))
    log.info("native google search: %s", ok)
    return ok

NOTIFY_TOKEN_FILE = os.path.expanduser("~/.notify_token")
_nfail = []
def _notify_ok(request):
    import hmac
    now = time.time()
    _nfail[:] = [t for t in _nfail if now - t < 600]
    if len(_nfail) >= 20: raise web.HTTPTooManyRequests()
    tok = read(NOTIFY_TOKEN_FILE)
    given = request.query.get("t", "")
    if not tok or not hmac.compare_digest(tok.encode(), given.encode()):
        _nfail.append(now); raise web.HTTPNotFound()
async def api_notify(request):
    _notify_ok(request)
    return web.json_response({"notifications": agent_core.notif_pending()})
async def api_notify_ack(request):
    _notify_ok(request)
    ids = [x for x in request.query.get("ids", "").split(",") if x]
    return web.json_response({"acked": agent_core.notif_ack(ids)})

async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=2**22)
    await ws.prepare(request)
    pc = read(PASSCODE_FILE)
    if pc and request.query.get("pc") != pc:
        await ws.send_json({"type": "error", "message": "passcode salah"})
        await ws.close()
        return ws
    key = read(KEY_FILE)
    if not key:
        await ws.send_json({"type": "error", "message": "API key Gemini belum dipasang di server"})
        await ws.close()
        return ws
    user = memory.clean_user(request.query.get("user"))
    session = "s%d" % int(time.time())
    try: mem_ctx = memory.context(user)
    except Exception as e:
        log.info("memory context error: %s", type(e).__name__); mem_ctx = ""
    try: _pend = agent_core.pending_report(user)
    except Exception as e:
        log.info("agent pending error: %s", type(e).__name__); _pend = ""
    setup = {"setup": {"model": f"models/{MODEL}",
             "generationConfig": {"responseModalities": ["AUDIO"]},
             "systemInstruction": {"parts": [{"text": _persona() + "".join("\n\n" + p for p in plugins_loader.prompts()) + mem_ctx + _pend + "\n\nWaktu sekarang (WIB): " + time.strftime("%A %d-%m-%Y %H:%M", time.gmtime(time.time() + 7 * 3600))}]},
             "inputAudioTranscription": {}, "outputAudioTranscription": {}}}
    decls = plugins_loader.declarations()
    native_search = os.environ.get("VA_NATIVE_SEARCH", "1") == "1" and await native_ok(key, decls)
    tl = []
    if native_search:
        tl.append({"googleSearch": {}})          # grounding/search bawaan Gemini
        decls = [d for d in decls if d["name"] != "cari_web"]   # scraper hanya fallback
    if decls and os.environ.get("VA_TOOLS", "1") == "1":
        tl.append({"functionDeclarations": decls})
    if tl:
        setup["setup"]["tools"] = tl
    buf = {"role": None, "text": ""}
    def flush():
        if buf["role"] and buf["text"].strip():
            try: memory.add_message(user, session, buf["role"], buf["text"])
            except Exception as e: log.info("memory write error: %s", type(e).__name__)
        buf["role"], buf["text"] = None, ""
    def push(role, text):
        if buf["role"] != role: flush(); buf["role"] = role
        buf["text"] += text
    ctx = {"user": user, "memory": memory}
    voice = request.query.get("voice", "")
    voice = voice if voice in VOICES else VOICE
    if voice:
        setup["setup"]["generationConfig"]["speechConfig"] = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
    import base64 as _b64
    T = {"el": {}, "epoch": 0, "gen": 0, "gbuf": {}, "pending": "", "failed": {}, "spoken": {}}
    tq = asyncio.Queue()
    async def tts_worker():
        while True:
            ep, gen, item = await tq.get()
            if ep != T["epoch"]: continue
            if item is None:   # akhir turn
                if T["failed"].get(gen):
                    for b in T["gbuf"].pop(gen, []):
                        await ws.send_bytes(b)
                    log.info("tts fallback: audio Gemini dikirim (turn %d)", gen)
                else: T["gbuf"].pop(gen, None)
                continue
            if T["failed"].get(gen): continue
            try:
                async def oc(c): await ws.send_bytes(c)
                await tts_el.stream(item, oc, lambda: ep == T["epoch"])
                T["spoken"][gen] = True
            except Exception as e:
                T["failed"][gen] = True
                tts_el.mark_failed(type(e).__name__ + " " + str(e)[:60])
    def new_turn():
        T["epoch"] += 1; T["gen"] += 1; T["pending"] = ""; T["gbuf"].clear(); T["failed"].clear(); T["spoken"].clear()
    wtask = asyncio.create_task(tts_worker())
    try:
        async with websockets.connect(URL + key, max_size=2**24) as up:
            await up.send(json.dumps(setup))
            async def c2g():
                async for m in ws:
                    if m.type == WSMsgType.BINARY:
                        import base64
                        await up.send(json.dumps({"realtimeInput": {"audio": {
                            "data": base64.b64encode(m.data).decode(), "mimeType": "audio/pcm;rate=16000"}}}))
                    elif m.type == WSMsgType.TEXT:
                        d = json.loads(m.data)
                        if d.get("type") == "text":
                            await up.send(json.dumps({"realtimeInput": {"text": d["text"]}}))
                    elif m.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
            async def g2c():
                import base64
                async for raw in up:
                    r = json.loads(raw)
                    if "setupComplete" in r:
                        await ws.send_json({"type": "ready"})
                        await up.send(json.dumps({"realtimeInput": {"text": GREETING}}))
                    if os.environ.get("VA_DEBUG"): log.info("up keys=%s sc=%s", list(r.keys()), list((r.get('serverContent') or {}).keys()))
                    tc = r.get("toolCall")
                    if tc:
                        async def run_tools(calls):
                            resps = []
                            for fc in calls:
                                res = await plugins_loader.call(fc.get("name", ""), fc.get("args") or {}, ctx)
                                log.info("tool %s ok=%s", fc.get("name"), "error" not in res)
                                resps.append({"id": fc.get("id"), "name": fc.get("name"), "response": {"result": res}})
                            await up.send(json.dumps({"toolResponse": {"functionResponses": resps}}))
                        asyncio.create_task(run_tools(tc.get("functionCalls", [])))
                        continue
                    sc = r.get("serverContent")
                    if not sc:
                        continue
                    use_el = T["el"].setdefault(T["gen"], tts_el.active())
                    for p in (sc.get("modelTurn") or {}).get("parts", []):
                        if "inlineData" in p:
                            ab = base64.b64decode(p["inlineData"]["data"])
                            if use_el: T["gbuf"].setdefault(T["gen"], []).append(ab)   # simpan utk fallback, jangan diputar
                            else: await ws.send_bytes(ab)
                    if sc.get("interrupted"):
                        flush()
                        new_turn()
                        await ws.send_json({"type": "interrupted"})
                    if "inputTranscription" in sc:
                        push("user", sc["inputTranscription"].get("text", ""))
                        await ws.send_json({"type": "user", "text": sc["inputTranscription"].get("text", "")})
                    if "outputTranscription" in sc:
                        push("model", sc["outputTranscription"].get("text", ""))
                        if use_el:
                            T["pending"] += sc["outputTranscription"].get("text", "")
                            sents, T["pending"] = tts_el.split_ready(T["pending"])
                            for x in sents: tq.put_nowait((T["epoch"], T["gen"], x))
                        await ws.send_json({"type": "model", "text": sc["outputTranscription"].get("text", "")})
                    if sc.get("turnComplete"):
                        flush()
                        if use_el or T["gbuf"].get(T["gen"]) is not None:
                            sents, T["pending"] = tts_el.split_ready(T["pending"], True)
                            for x in sents: tq.put_nowait((T["epoch"], T["gen"], x))
                            tq.put_nowait((T["epoch"], T["gen"], None))
                            T["gen"] += 1
                        await ws.send_json({"type": "turn_complete"})
            t = [asyncio.create_task(c2g()), asyncio.create_task(g2c())]
            done, pend = await asyncio.wait(t, return_when=asyncio.FIRST_COMPLETED)
            for p in pend: p.cancel()
            wtask.cancel()
            for d in done:
                if d.exception(): raise d.exception()
    except Exception as e:
        log.info("session error: %s", type(e).__name__)  # never log URL/key
        try: await ws.send_json({"type": "error", "message": f"koneksi Gemini putus ({type(e).__name__})"})
        except Exception: pass
    flush()
    asyncio.create_task(memory.maintain(user, key, session))
    await ws.close()
    return ws

plugins_loader.load_all()
app = web.Application(middlewares=[cors])
async def _start_sched(app):
    app['sched'] = asyncio.create_task(agent_core.scheduler())
app.on_startup.append(_start_sched)
MAC_FILES={"install.sh":"text/x-shellscript","InstinctVoiceMac.tar.gz":"application/gzip"}
async def mac_file(request):
    name=request.match_info["name"]
    if name not in MAC_FILES:
        raise web.HTTPNotFound()
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", "mac", name), headers={"Content-Type":MAC_FILES[name],"Cache-Control":"no-cache"})

app.add_routes([web.get("/", index), web.get("/status", status), web.get("/api/bot", api_bot), web.get("/ws", ws_handler), web.get("/mac/{name}", mac_file),
                web.get("/api/export", api_export), web.get("/api/memory", api_memory), web.get("/api/plugins", api_plugins), web.post("/api/plugins/reload", api_plugins_reload),
                web.get("/api/notify", api_notify), web.post("/api/notify/ack", api_notify_ack)] + integrations.routes())
if __name__ == "__main__":
    web.run_app(app, host=HOST, port=PORT, print=lambda *_: None)
