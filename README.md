# Voice Agent (Instinct Voice)

Agen suara berbahasa Indonesia berbasis Gemini Live, dengan suara ElevenLabs, memori, plugin, mode agen terjadwal, dan kontrol Mac lewat Tailscale SSH.

## Isi
- `server.py` : web server (aiohttp), sesi suara, API
- `agent_core.py` : mode agen, scheduler, notifikasi proaktif
- `memory.py` : memori jangka panjang (SQLite)
- `tts_el.py` : TTS ElevenLabs dengan fallback Gemini
- `integrations.py` : menu Integrasi (key API, OAuth Gmail/OneDrive, GitHub, tes Mac SSH, minta akses)
- `plugins/` : plugin (tiap plugin = `manifest.json` + `plugin.py`)
- `plugin_forge.py`, `forge_harness.py` : pembuat plugin otomatis
- `static/` : UI web dan installer app Mac
- `voice-agent.service` : unit systemd

## Rahasia (TIDAK ada di repo)
Semua rahasia ada di home user server, bukan di repo:
`~/.gemini_api_key`, `~/.elevenlabs_key`, `~/.notify_token`, `~/.admin_code_hash`, `~/.integrations/*`, dan folder `data/`.

## Pasang di server (Ubuntu)
1. `python3 -m venv venv && venv/bin/pip install aiohttp`
2. Isi key Gemini di `~/.gemini_api_key` (chmod 600).
3. Salin `voice-agent.service` ke `/etc/systemd/system/`, lalu `systemctl enable --now voice-agent`.
4. Taruh di belakang nginx (port 8780) dengan HTTPS.
5. Buat kode admin: simpan hash scrypt di `~/.admin_code_hash`.

## Menu Integrasi
Buka tab Integrasi, masukkan kode admin. Redirect URI OAuth: `https://<domain>/api/integrasi/oauth/callback`.
Key Gemini, ElevenLabs, dan GitHub divalidasi ke penyedia sebelum disimpan. Rahasia tidak pernah dikembalikan lewat API.
