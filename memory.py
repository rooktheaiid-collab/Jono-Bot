"""Memori mandiri: SQLite per-user. Pesan, ringkasan, fakta. Aman dipanggil dari asyncio (query kecil)."""
from zoneinfo import ZoneInfo
import os, re, json, sqlite3, time, threading, tarfile, io, tempfile

DIR = os.environ.get("VA_DATA", os.path.expanduser("~/voice-agent/data"))
DB = os.path.join(DIR, "memory.db")
RECENT_KEEP = 24        # pesan terakhir yang selalu dikirim mentah
FOLD_AT = 40            # kalau pesan belum diringkas > ini, lipat yang lama ke ringkasan
SUMMARY_MAX = 3500      # batas karakter ringkasan
FACTS_MAX = 60
CTX_MAX = 7000          # batas total konteks yang diinjeksi
_lock = threading.Lock()
WIB = ZoneInfo('Asia/Jakarta')
def _fmt(t, f): return time.strftime(f, time.gmtime(t + 7 * 3600))

def _conn():
    os.makedirs(DIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, session TEXT, ts REAL, role TEXT, text TEXT, folded INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS ix_m ON messages(user, folded, id);
    CREATE TABLE IF NOT EXISTS summary(user TEXT PRIMARY KEY, text TEXT, updated REAL);
    CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, ts REAL, text TEXT, UNIQUE(user, text));
    """)
    return c

def clean_user(u):
    u = re.sub(r"[^a-z0-9_-]", "", (u or "reza").lower())[:32]
    return u or "reza"

def add_message(user, session, role, text):
    text = (text or "").strip()
    if not text: return
    with _lock, _conn() as c:
        c.execute("INSERT INTO messages(user,session,ts,role,text) VALUES(?,?,?,?,?)", (user, session, time.time(), role, text[:4000]))

def add_fact(user, text):
    text = " ".join((text or "").split())[:300]
    if len(text) < 3: return False
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO facts(user,ts,text) VALUES(?,?,?)", (user, time.time(), text))
        # cap: buang fakta paling lama
        c.execute("DELETE FROM facts WHERE user=? AND id NOT IN (SELECT id FROM facts WHERE user=? ORDER BY id DESC LIMIT ?)", (user, user, FACTS_MAX))
    return True

def list_facts(user):
    with _lock, _conn() as c:
        return [r[0] for r in c.execute("SELECT text FROM facts WHERE user=? ORDER BY id", (user,))]

def get_summary(user):
    with _lock, _conn() as c:
        r = c.execute("SELECT text FROM summary WHERE user=?", (user,)).fetchone()
    return r[0] if r else ""

def search(user, q, limit=8):
    q = (q or "").strip()
    if not q: return []
    like = f"%{q}%"
    with _lock, _conn() as c:
        rows = c.execute("SELECT ts,role,text FROM messages WHERE user=? AND text LIKE ? ORDER BY id DESC LIMIT ?", (user, like, limit)).fetchall()
        facts = c.execute("SELECT ts,'fakta',text FROM facts WHERE user=? AND text LIKE ? LIMIT ?", (user, like, limit)).fetchall()
        s = c.execute("SELECT text FROM summary WHERE user=?", (user,)).fetchone()
    out = [{"waktu": _fmt(t, "%Y-%m-%d %H:%M"), "dari": r, "isi": x[:300]} for t, r, x in facts + rows]
    if s and q.lower() in s[0].lower():
        out.append({"waktu": "", "dari": "ringkasan", "isi": s[0][:600]})
    return out

def context(user):
    """Teks ingatan untuk diinjeksi ke system prompt. Dibatasi CTX_MAX."""
    with _lock, _conn() as c:
        s = c.execute("SELECT text FROM summary WHERE user=?", (user,)).fetchone()
        facts = [r[0] for r in c.execute("SELECT text FROM facts WHERE user=? ORDER BY id", (user,))]
        recent = c.execute("SELECT ts,role,text FROM messages WHERE user=? ORDER BY id DESC LIMIT ?", (user, RECENT_KEEP)).fetchall()[::-1]
    if not (s or facts or recent): return ""
    parts = ["\n\nINGATAN DARI SESI SEBELUMNYA (pakai secara natural, jangan dibacakan mentah, jangan sebut 'database'):"]
    if facts: parts.append("Fakta yang kamu ingat tentang user:\n" + "\n".join("- " + f for f in facts))
    if s and s[0]: parts.append("Ringkasan obrolan lama:\n" + s[0])
    if recent:
        lines = []
        for t, r, x in recent:
            lines.append(f"[{_fmt(t, '%d/%m %H:%M')}] {'User' if r == 'user' else 'Instinct'}: {x[:300]}")
        parts.append("Obrolan terakhir:\n" + "\n".join(lines))
    txt = "\n".join(parts)
    if len(txt) > CTX_MAX:  # potong bagian paling lama (awal obrolan terakhir), pertahankan ekor
        txt = txt[:1500] + "\n...\n" + txt[-(CTX_MAX - 1520):]
    return txt

def fold_candidates(user):
    """Pesan lama (di luar RECENT_KEEP) yang perlu dilipat, kalau jumlah belum-diringkas sudah > FOLD_AT."""
    with _lock, _conn() as c:
        rows = c.execute("SELECT id,role,text FROM messages WHERE user=? AND folded=0 ORDER BY id", (user,)).fetchall()
    if len(rows) <= FOLD_AT: return []
    return rows[:-RECENT_KEEP]

def apply_fold(user, new_summary, upto_id):
    new_summary = (new_summary or "").strip()
    if len(new_summary) > SUMMARY_MAX: new_summary = new_summary[-SUMMARY_MAX:]
    with _lock, _conn() as c:
        c.execute("INSERT INTO summary(user,text,updated) VALUES(?,?,?) ON CONFLICT(user) DO UPDATE SET text=excluded.text, updated=excluded.updated", (user, new_summary, time.time()))
        c.execute("UPDATE messages SET folded=1 WHERE user=? AND id<=?", (user, upto_id))
        # hapus pesan mentah yang sudah dilipat dan lebih tua dari 30 hari supaya DB tidak membengkak
        c.execute("DELETE FROM messages WHERE user=? AND folded=1 AND ts<?", (user, time.time() - 30 * 86400))

def crude_fold(old_summary, rows):
    """Fallback tanpa LLM: tambah baris pendek ke ringkasan, ambil ekor."""
    add = "\n".join(f"- {'User' if r == 'user' else 'Instinct'}: {t[:120]}" for _, r, t in rows)
    return ((old_summary + "\n" if old_summary else "") + add)[-SUMMARY_MAX:]

def snapshot(dest_db):
    """Salinan konsisten DB (SQLite backup API) ke file dest_db."""
    with _lock:
        src = _conn(); dst = sqlite3.connect(dest_db)
        with dst: src.backup(dst)
        dst.close(); src.close()

def export_json(user=None):
    with _lock, _conn() as c:
        d = {}
        for name in ("messages", "summary", "facts"):
            cur = c.execute(f"SELECT * FROM {name}" + (" WHERE user=?" if user else ""), (user,) if user else ())
            cols = [x[0] for x in cur.description]
            d[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return d

def export_targz():
    """Bytes tar.gz berisi memory.db (snapshot konsisten) + memory.json (terbaca manusia)."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "memory.db"); snapshot(p)
        j = os.path.join(td, "memory.json")
        with open(j, "w") as f: json.dump(export_json(), f, ensure_ascii=False, indent=1)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            t.add(p, "memory.db"); t.add(j, "memory.json")
        return buf.getvalue()

async def maintain(user, key, session=None):
    """Lipat pesan lama jadi ringkasan. Pakai Gemini REST; kalau gagal pakai crude_fold. Tidak pernah melempar."""
    try:
        rows = fold_candidates(user)
        if not rows: return
        old = get_summary(user)
        transcript = "\n".join(f"{'User' if r == 'user' else 'Instinct'}: {t[:400]}" for _, r, t in rows)[-12000:]
        new = None
        if key:
            import aiohttp
            prompt = ("Perbarui ringkasan ingatan percakapan antara user (Reza) dan asisten suara Instinct. "
                      "Pertahankan fakta penting, preferensi, keputusan, janji/tugas yang masih terbuka, dan topik berjalan. "
                      f"Bahasa Indonesia, ringkas, poin-poin, maksimal {SUMMARY_MAX-500} karakter.\n\n"
                      f"RINGKASAN LAMA:\n{old or '(kosong)'}\n\nOBROLAN BARU:\n{transcript}\n\nRINGKASAN BARU:")
            for model in os.environ.get("SUMMARY_MODELS", "gemini-flash-lite-latest,gemini-flash-latest").split(","):
                try:
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
                        async with s.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                          headers={"x-goog-api-key": key}, json={"contents": [{"parts": [{"text": prompt}]}]}) as r:
                            if r.status != 200: continue
                            j = await r.json()
                    t = j["candidates"][0]["content"]["parts"][0]["text"].strip()
                    if len(t) > 40: new = t; break
                except Exception:
                    continue
        if not new: new = crude_fold(old, rows)
        apply_fold(user, new, rows[-1][0])
    except Exception:
        pass
