import asyncio, hashlib, io, os, re, shlex, time, logging, zipfile
log = logging.getLogger("voice")
HOST = os.environ.get("MAC_HOST", "100.126.45.100")
USER = os.environ.get("MAC_USER", "macbookprom1")
KEY = os.path.expanduser("~/.ssh/macbook_reza_key")
HOME = f"/Users/{USER}"
LOCAL = os.environ.get("VA_LOCAL", "0") == "1"
if LOCAL:
    HOME = os.path.expanduser("~")
    USER = os.path.basename(HOME)
WORK = "/tmp/baca_dokumen"
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 500
CHUNK = 8000
CACHE_TTL = 3600
EXT = {"pdf", "docx", "xlsx", "xlsm", "pptx", "txt", "md", "csv", "rtf", "json", "log", "tsv"}
OLD = {"doc", "xls", "ppt", "pages", "numbers", "key"}
BAD_PARTS = ("/.ssh", "/Keychains", "/.gnupg", "/.aws", "/.kube", "/.config/gcloud", "/.netrc")
_sem = asyncio.Semaphore(2)

async def ssh(remote_cmd, timeout=20, binary=False, limit=None):
    if LOCAL:
        args = ["/bin/bash", "-c", remote_cmd]
    else:
      args = ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", f"{USER}@{HOST}", remote_cmd]
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        try: p.kill()
        except Exception: pass
        return 124, b""
    return p.returncode, out

def _cleanup():
    os.makedirs(WORK, exist_ok=True)
    now = time.time()
    for f in os.listdir(WORK):
        fp = os.path.join(WORK, f)
        try:
            if now - os.path.getmtime(fp) > CACHE_TTL: os.remove(fp)
        except OSError: pass

def _expand(p):
    p = p.strip()
    if p == "~" or p.startswith("~/"): p = HOME + p[1:]
    return p

def _path_ok(p):
    if "\x00" in p or "\n" in p or ".." in p.split("/"): return False
    if not (p.startswith(HOME + "/") or p.startswith("/tmp/") or p.startswith("/private/tmp/")): return False
    if any(b in p for b in BAD_PARTS): return False
    return p.rsplit(".", 1)[-1].lower() in EXT if "." in p.rsplit("/", 1)[-1] else False

# ---------- ekstraksi (jalan di thread) ----------
def _pdf(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    if r.is_encrypted:
        try:
            if not r.decrypt(""): raise ValueError("pdf terkunci password")
        except Exception: raise ValueError("pdf terkunci password")
    n = len(r.pages)
    if n > MAX_PAGES: raise ValueError(f"terlalu panjang ({n} halaman, batas {MAX_PAGES})")
    parts, nchar = [], 0
    for i, pg in enumerate(r.pages, 1):
        t = (pg.extract_text() or "").strip()
        nchar += len(t)
        parts.append(f"--- halaman {i} ---\n{t}")
    return "\n".join(parts), f"{n} halaman", n, nchar

def _docx(path):
    import docx
    d = docx.Document(path)
    out = [p.text for p in d.paragraphs if p.text.strip()]
    for ti, t in enumerate(d.tables, 1):
        out.append(f"--- tabel {ti} ---")
        for row in t.rows:
            out.append(" | ".join(c.text.strip() for c in row.cells))
    txt = "\n".join(out)
    return txt, f"{len(d.paragraphs)} paragraf, {len(d.tables)} tabel", None, len(txt)

def _xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"--- sheet {ws.title} ---")
        for ri, row in enumerate(ws.iter_rows(values_only=True), 1):
            if ri > 3000: out.append("(baris dipotong di 3000)"); break
            if any(c is not None for c in row):
                out.append(" | ".join("" if c is None else str(c) for c in row).rstrip(" |"))
    txt = "\n".join(out)
    return txt, f"{len(wb.worksheets)} sheet", None, len(txt)

def _pptx(path):
    from pptx import Presentation
    pr = Presentation(path)
    out = []
    for i, s in enumerate(pr.slides, 1):
        out.append(f"--- slide {i} ---")
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip(): out.append(sh.text_frame.text.strip())
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows: out.append(" | ".join(c.text.strip() for c in row.cells))
        if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
            out.append("[catatan] " + s.notes_slide.notes_text_frame.text.strip())
    txt = "\n".join(out)
    return txt, f"{len(pr.slides)} slide", len(pr.slides), len(txt)

def _rtf(path):
    s = open(path, "r", errors="replace").read()
    s = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), s)
    for _ in range(3): s = re.sub(r"\{\\(fonttbl|colortbl|stylesheet|info)(?:[^{}]|\{[^{}]*\})*\}", "", s)
    s = re.sub(r"\\par[d]?", "\n", s)
    s = re.sub(r"\{\\\*[^{}]*\}", "", s)
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", s)
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"\n\s*\n+", "\n", s).strip()
    return s, "rtf", None, len(s)

def _text(path):
    b = open(path, "rb").read()
    for enc in ("utf-8", "utf-16", "cp1252"):
        try: s = b.decode(enc); break
        except Exception: continue
    else: s = b.decode("utf-8", "replace")
    return s, f"{s.count(chr(10)) + 1} baris", None, len(s)

def _extract(path, ext):
    f = {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "xlsm": _xlsx, "pptx": _pptx, "rtf": _rtf}.get(ext, _text)
    return f(path)

async def _find(nama, folder):
    q = nama.strip()
    if q.startswith("/") or q.startswith("~"):
        return [_expand(q)]
    if not q or "\n" in q: return []
    cmd = "mdfind -name " + shlex.quote(q[:80])
    if folder:
        f = _expand(folder)
        if not (f == HOME or f.startswith(HOME + "/") or f.startswith("/tmp/")) or ".." in f.split("/"): return None
        cmd += " -onlyin " + shlex.quote(f)
    else:
        cmd += " -onlyin " + shlex.quote(HOME)
    rc, out = await ssh(cmd + " 2>/dev/null | head -60", 20)
    if rc != 0: raise ConnectionError
    paths = [l for l in out.decode("utf-8", "replace").splitlines() if l.startswith("/") and _path_ok(l)]
    return paths

OCR_MAX_PAGES = 10
OCR_DPI = 150
OCR_MODELS = os.environ.get("OCR_MODELS", "gemini-flash-lite-latest,gemini-flash-latest").split(",")
OCR_PROMPT = ("Transkripsikan SEMUA teks yang terlihat di gambar halaman dokumen ini persis apa adanya, dalam bahasa aslinya. "
              "Tabel tulis per baris dengan pemisah ' | '. Jangan menambah komentar, ringkasan, atau terjemahan. "
              "Kalau tidak ada teks sama sekali tulis: (kosong)")

def _render_pages(path, first, last):
    import pymupdf
    doc = pymupdf.open(path)
    out = []
    try:
        for i in range(first - 1, min(last, doc.page_count)):
            pix = doc[i].get_pixmap(dpi=OCR_DPI)
            out.append((i + 1, pix.tobytes("jpeg", jpg_quality=70)))
    finally:
        doc.close()
    return out

async def _ocr_one(session, key, num, jpg):
    import base64
    body = {"contents": [{"parts": [{"text": OCR_PROMPT}, {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpg).decode()}}]}]}
    for model in OCR_MODELS:
        for attempt in range(2):
            try:
                async with session.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                        headers={"x-goog-api-key": key}, json=body) as r:
                    if r.status == 200:
                        j = await r.json()
                        parts = j["candidates"][0]["content"]["parts"]
                        t = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                        return num, t
                    if r.status in (429, 503): await asyncio.sleep(1.5); continue
                    break
            except Exception:
                await asyncio.sleep(1)
    return num, None

async def ocr_pdf(path, first, total):
    """Render halaman scan -> gambar sementara (di memori) -> Gemini vision -> teks."""
    key = open(os.path.expanduser("~/.gemini_api_key")).read().strip()
    last = min(total, first + OCR_MAX_PAGES - 1)
    pages = await asyncio.get_running_loop().run_in_executor(None, _render_pages, path, first, last)
    import aiohttp
    sem = asyncio.Semaphore(3)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
        async def one(n, jpg):
            async with sem: return await _ocr_one(s, key, n, jpg)
        res = await asyncio.gather(*[one(n, j) for n, j in pages])
    parts, fail = [], 0
    for n, t in sorted(res):
        if t is None: fail += 1; t = "(gagal dibaca)"
        parts.append(f"--- halaman {n} (hasil baca gambar) ---\n{t}")
    return "\n".join(parts), last, fail

async def handle(tool, args, ctx):
    if tool != "baca_dokumen": return {"error": "tool tidak dikenal"}
    nama = str(args.get("nama", "")).strip()
    if not nama: return {"error": "nama file kosong"}
    try: mulai = max(0, int(args.get("mulai") or 0))
    except Exception: mulai = 0
    try:
        _cleanup()
        try: cands = await _find(nama, args.get("folder"))
        except ConnectionError: return {"error": "MacBook tidak terjangkau (mati/tidur/Tailscale off)"}
        if cands is None: return {"error": "folder harus di dalam home Mac"}
        if not cands:
            return {"error": "dokumen tidak ditemukan", "petunjuk": "Bilang gak nemu, tanya nama file atau foldernya."}
        if len(cands) > 1:
            base = nama.lower()
            exact = [c for c in cands if os.path.basename(c).lower() == base]
            if len(exact) == 1: cands = exact
            else:
                return {"banyak_kandidat": [c.replace(HOME, "~") for c in cands[:6]], "jumlah": len(cands),
                        "petunjuk": "Ada beberapa file cocok. Sebut nama-nama file saja dan tanya user yang mana, lalu panggil lagi dengan path lengkap."}
        path = cands[0]
        ext = path.rsplit(".", 1)[-1].lower() if "." in os.path.basename(path) else ""
        if ext in OLD: return {"error": f"format .{ext} belum didukung (simpan jadi PDF/DOCX/XLSX/PPTX dulu)"}
        if not _path_ok(path):
            return {"error": "path tidak diizinkan (harus di dalam home Mac dan tipe dokumen biasa)"}
        rc, out = await ssh("stat -f '%z %m %HT' -- " + shlex.quote(path), 15)
        if rc == 255 or rc == 124: return {"error": "MacBook tidak terjangkau (mati/tidur/Tailscale off)"}
        st = out.decode().strip().split(" ", 2)
        if rc != 0 or len(st) < 3: return {"error": "dokumen tidak ditemukan", "petunjuk": "Bilang gak nemu filenya."}
        size, mtime, ftype = int(st[0]), st[1], st[2]
        if "Regular" not in ftype: return {"error": "bukan file biasa (symlink/folder ditolak)"}
        if size > MAX_BYTES: return {"error": f"file terlalu besar ({size // 1048576} MB, batas {MAX_BYTES // 1048576} MB)"}
        try: hm = max(1, int(args.get("halaman_mulai") or 1))
        except Exception: hm = 1
        key = hashlib.sha1(f"{path}|{size}|{mtime}|{hm}".encode()).hexdigest()[:16]
        cache = os.path.join(WORK, key + ".txt"); meta = os.path.join(WORK, key + ".meta")
        extra = {}
        if not os.path.exists(cache):
            tmp = os.path.join(WORK, key + "." + ext)
            async with _sem:
                rc, data = await ssh("head -c %d -- %s" % (MAX_BYTES + 1, shlex.quote(path)), 60, binary=True)
                if rc != 0 or not data: return {"error": "gagal mengambil file dari Mac"}
                with open(tmp, "wb") as f: f.write(data)
                try:
                    txt, info, pages, nchar = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, _extract, tmp, ext), 60)
                    if ext == "pdf" and (len(txt.strip()) < 30 or (pages and nchar < 20 * pages)):
                        # PDF scan: baca pakai mata Gemini
                        if hm > pages: return {"error": "halaman_mulai melebihi jumlah halaman", "info": info}
                        try:
                            txt, last, fail = await asyncio.wait_for(ocr_pdf(tmp, hm, pages), 150)
                        except Exception as e:
                            log.info("baca_dokumen ocr gagal: %s", type(e).__name__)
                            return {"nama": os.path.basename(path), "info": info, "error": "PDF scan, tapi gagal dibaca pakai mata Gemini",
                                    "petunjuk": "Bilang terus terang gagal membaca dokumen scan ini."}
                        info = f"{pages} halaman (dokumen scan, dibaca pakai mata Gemini, halaman {hm}-{last})"
                        meta_extra = {"scan": True, "total_halaman": pages, "halaman_dibaca": f"{hm}-{last}", "gagal_halaman": fail}
                        if last < pages: meta_extra["halaman_berikutnya"] = last + 1
                        open(os.path.join(WORK, key + ".json"), "w").write(__import__("json").dumps(meta_extra))
                except asyncio.TimeoutError: return {"error": "ekstraksi terlalu lama"}
                except ValueError as e: return {"error": str(e)}
                except Exception as e:
                    log.info("baca_dokumen ekstraksi gagal: %s", type(e).__name__)
                    return {"error": f"file rusak atau format tidak terbaca ({type(e).__name__})"}
                finally:
                    try: os.remove(tmp)
                    except OSError: pass
            if len(txt.strip()) < 30:
                return {"nama": os.path.basename(path), "info": info, "error": "tidak ada teks yang bisa diambil",
                        "petunjuk": "Dokumen kosong atau tidak terbaca. Bilang terus terang."}
            open(cache, "w").write(txt); open(meta, "w").write(info)
        jp = os.path.join(WORK, key + ".json")
        if os.path.exists(jp):
            try: extra = __import__("json").load(open(jp))
            except Exception: extra = {}
        txt = open(cache).read(); info = open(meta).read()
        total = len(txt)
        if mulai >= total: return {"nama": os.path.basename(path), "info": info, "error": "sudah di akhir dokumen"}
        part = txt[mulai: mulai + CHUNK]
        end = mulai + len(part)
        res = {"nama": os.path.basename(path), "info": info, "total_karakter": total, "mulai": mulai, "teks": part, "sisa_karakter": total - end}
        res.update(extra)
        if end < total:
            res["mulai_berikutnya"] = end
            res["petunjuk"] = "Ini baru bagian awal/tengah. Ringkas, lalu tawarkan lanjut bagian berikutnya."
        elif extra.get("halaman_berikutnya"):
            res["petunjuk"] = f"Dokumen scan, baru halaman {extra['halaman_dibaca']} dari {extra['total_halaman']}. Ringkas, tawarkan lanjut; kalau mau panggil lagi dengan halaman_mulai={extra['halaman_berikutnya']}."
        else:
            res["petunjuk"] = "Ini sampai akhir dokumen. Ringkas dengan kata-katamu sendiri."
        return res
    except Exception as e:
        log.info("baca_dokumen error: %s", type(e).__name__)
        return {"error": f"gagal ({type(e).__name__})"}
