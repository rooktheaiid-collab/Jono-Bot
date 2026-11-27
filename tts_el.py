"""ElevenLabs TTS streaming (HTTP) dengan fallback ke audio Gemini. Key tidak pernah di-log."""
import os, re, time, asyncio, logging
import aiohttp
log = logging.getLogger("voice-agent")
KEY_FILE = os.environ.get("EL_KEY_FILE", os.path.expanduser("~/.elevenlabs_key"))
VOICE_ID = os.environ.get("EL_VOICE_ID", "9375G6zswFk7v9bKTVQF")
MODEL = os.environ.get("EL_MODEL", "eleven_flash_v2_5")
COOLDOWN = 120
_fail_until = 0.0
SENT_END = re.compile(r'(?<=[.!?\n])\s+|(?<=[.!?])$')

import json
def _cfg():
    try: return json.load(open(os.path.join(os.environ.get("VA_DATA", os.path.expanduser("~/voice-agent/data")), "integrations.json")))
    except Exception: return {}

def provider():
    return (_cfg().get("tts_provider") or os.environ.get("TTS_PROVIDER", "gemini")).lower()

def voice_id():
    return _cfg().get("el_voice_id") or VOICE_ID

def key():
    try:
        return open(KEY_FILE).read().strip()
    except Exception:
        return ""

def active():
    """True kalau suara ElevenLabs dipakai (provider diset, key ada, tidak sedang cooldown)."""
    return provider() == "elevenlabs" and bool(key()) and time.time() >= _fail_until

def mark_failed(why):
    global _fail_until
    _fail_until = time.time() + COOLDOWN
    log.info("tts elevenlabs GAGAL (%s) -> fallback ke suara Gemini selama %ds", why, COOLDOWN)

async def stream(text, on_chunk, alive):
    """Stream PCM 24kHz ke on_chunk. Raise kalau gagal. alive() False = berhenti (barge-in)."""
    t0 = time.time(); n = 0; first = None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id()}/stream?output_format=pcm_24000&optimize_streaming_latency=3"
    body = {"text": text, "model_id": MODEL}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.post(url, json=body, headers={"xi-api-key": key()}) as r:
            if r.status != 200:
                raise RuntimeError("http %d" % r.status)
            carry = b""
            async for chunk in r.content.iter_chunked(4800):
                if not alive():
                    return n
                if first is None: first = time.time() - t0
                chunk = carry + chunk
                if len(chunk) % 2:
                    carry = chunk[-1:]; chunk = chunk[:-1]
                else:
                    carry = b""
                if chunk:
                    n += len(chunk); await on_chunk(chunk)
    log.info("tts elevenlabs ok chars=%d bytes=%d first_byte_ms=%d total_ms=%d model=%s", len(text), n, int((first or 0)*1000), int((time.time()-t0)*1000), MODEL)
    return n

def split_ready(pending, final=False):
    """Pisah teks jadi kalimat siap-bicara + sisa."""
    out = []; last = 0
    for m in re.finditer(r'[.!?\n]+\s', pending):
        seg = pending[last:m.end()]
        if len(seg.strip()) >= 12:
            out.append(seg.strip()); last = m.end()
    rest = pending[last:]
    if final and rest.strip():
        out.append(rest.strip()); rest = ""
    return out, rest
