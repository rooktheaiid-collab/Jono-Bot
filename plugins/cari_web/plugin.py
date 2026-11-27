import re, html, asyncio, base64, urllib.parse
import xml.etree.ElementTree as ET
import aiohttp

SEARX = "http://127.0.0.1:8888"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "id,en;q=0.8"}
TO = aiohttp.ClientTimeout(total=10)

def _strip(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())

async def news(q, n=5):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "id", "gl": "ID", "ceid": "ID:id"})
    async with aiohttp.ClientSession(headers=UA, timeout=TO) as s:
        async with s.get(url) as r:
            if r.status != 200: raise RuntimeError(f"news {r.status}")
            root = ET.fromstring(await r.read())
    out = []
    for it in root.iter("item"):
        title = _strip(it.findtext("title"))
        src = _strip(it.findtext("source")) or (title.rsplit(" - ", 1)[1] if " - " in title else "")
        if " - " in title and src and title.endswith(" - " + src): title = title[: -len(src) - 3]
        out.append({"judul": title, "sumber": src, "tanggal": (it.findtext("pubDate") or "")[:25]})
        if len(out) >= n: break
    return out

def _bing_url(href):
    href = html.unescape(href)
    m = re.search(r"[?&]u=a1([A-Za-z0-9_-]+)", href)
    if m:
        try:
            b = m.group(1); b += "=" * (-len(b) % 4)
            return base64.urlsafe_b64decode(b).decode()
        except Exception:
            pass
    return href

async def web(q, n=5):
    async with aiohttp.ClientSession(headers=UA, timeout=TO) as s:
        async with s.get("https://www.bing.com/search", params={"q": q, "setlang": "id"}) as r:
            if r.status != 200: raise RuntimeError(f"bing {r.status}")
            page = await r.text()
    out = []
    for blk in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
        m = re.search(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", blk, re.S)
        if not m: continue
        link = _bing_url(m.group(1))
        host = urllib.parse.urlparse(link).netloc.replace("www.", "")
        sn = re.search(r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>', blk, re.S) or re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
        out.append({"judul": _strip(m.group(2)), "sumber": host, "ringkasan": _strip(sn.group(1))[:300] if sn else ""})
        if len(out) >= n: break
    if not out: raise RuntimeError("bing kosong")
    return out

async def searx(q, kind, n=6):
    """Backend utama: SearXNG lokal (127.0.0.1:8888, gabungan Brave/Bing/dll)."""
    params = {"q": q, "format": "json", "language": "id", "categories": "news" if kind == "berita" else "general"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        async with s.get(SEARX + "/search", params=params) as r:
            if r.status != 200: raise RuntimeError(f"searx {r.status}")
            j = await r.json()
    out, seen = [], set()
    for x in j.get("results", []):
        link = x.get("url", ""); host = urllib.parse.urlparse(link).netloc.replace("www.", "")
        title = _strip(x.get("title"))
        if not title or title in seen: continue
        seen.add(title)
        item = {"judul": title, "sumber": host}
        if x.get("content"): item["ringkasan"] = _strip(x["content"])[:300]
        if x.get("publishedDate"): item["tanggal"] = str(x["publishedDate"])[:16]
        out.append(item)
        if len(out) >= n: break
    if not out: raise RuntimeError("searx kosong")
    return out

async def handle(tool, args, ctx):
    q = str(args.get("query", "")).strip()[:200]
    if not q: return {"error": "query kosong"}
    jenis = str(args.get("jenis", "")).lower()
    order = [lambda q: searx(q, jenis), news, web] if jenis == "berita" else [lambda q: searx(q, jenis), web, news]
    for fn in order:
        try:
            res = await fn(q)
            if res: return {"query": q, "hasil": res, "petunjuk": "Rangkum singkat dan santai, sebut nama sumber. Jangan bacakan URL."}
        except Exception:
            continue
    return {"query": q, "hasil": [], "petunjuk": "Pencarian gagal atau kosong. Bilang 'gak nemu' dengan jujur, jangan mengarang."}
