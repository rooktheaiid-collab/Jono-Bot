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
1. `python3 -m venv venv && venv/bin/pip install aiohttp websockets pypdf python-docx openpyxl pymupdf`
2. Isi key Gemini di `~/.gemini_api_key` (chmod 600).
3. Salin `voice-agent.service` ke `/etc/systemd/system/`, lalu `systemctl enable --now voice-agent`.
4. Taruh di belakang nginx (port 8780) dengan HTTPS.
5. Buat kode admin: simpan hash scrypt di `~/.admin_code_hash`.

## Menu Integrasi
Buka tab Integrasi, masukkan kode admin. Redirect URI OAuth: `https://<domain>/api/integrasi/oauth/callback`.
Key Gemini, ElevenLabs, dan GitHub divalidasi ke penyedia sebelum disimpan. Rahasia tidak pernah dikembalikan lewat API.

## Pasang lokal di macOS
Jono Bot bisa jalan penuh di Mac sendiri (tanpa VPS):

```
git clone https://github.com/rooktheaiid-collab/Jono-Bot.git
cd Jono-Bot
bash install_mac.sh
```
Script ini: menyalin kode ke `~/JonoBot`, membuat venv + dependensi, menanyakan key Gemini (boleh dilewati, isi nanti di tab Integrasi), membuat passcode sesi dan kode admin baru (kode admin tampil sekali di terminal), lalu memasang service launchd `id.reza.jonobot` (autostart saat login, restart otomatis).

Buka `http://127.0.0.1:8780/` di Safari atau Chrome. Tab Integrasi dan semua plugin jalan. Di mode lokal (`VA_LOCAL=1`) plugin kontrol_mac dan baca_dokumen menjalankan perintah langsung di Mac ini, bukan lewat SSH.

Opsi: `--no-autostart`, `--port N`, `--uninstall`. Log di `~/JonoBot/logs/`. Update: `git pull && bash install_mac.sh` (data dan kunci tidak ditimpa).

Catatan: pencarian web memakai SearXNG lokal kalau ada, kalau tidak otomatis pakai Google News RSS dan Bing. Notifikasi proaktif ke Telegram butuh poller dari sisi Instinct dan tidak aktif di instalasi lokal. Untuk OAuth Gmail/OneDrive di lokal, daftarkan redirect URI `http://127.0.0.1:8780/api/integrasi/oauth/callback`.
