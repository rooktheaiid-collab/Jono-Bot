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
Instalasi lokal butuh KODE AKSES yang diterbitkan pemilik Jono Bot. Alurnya:

1. Minta kode: kirim email ke rook.the.ai.id@gmail.com (nama dan keperluan). Pemilik menerbitkan dua hal: KODE AKSES (token panjang berawalan `JB1.`) dan KODE ADMIN (dikirim lewat jalur terpisah).
2. Pasang (cara tercepat, satu perintah):
```
curl -fsSL https://raw.githubusercontent.com/rooktheaiid-collab/Jono-Bot/main/get.sh | bash
```
Kalau repo masih private: `GITHUB_TOKEN=<token read-only dari pemilik> bash get.sh`. Atau manual:
```
git clone https://github.com/rooktheaiid-collab/Jono-Bot.git
cd Jono-Bot
bash install_mac.sh        # tempel KODE AKSES saat diminta (atau JONOBOT_CODE=... bash install_mac.sh)
```
Installer memverifikasi tanda tangan kode (offline), menolak kode palsu atau kedaluwarsa, lalu: menyalin kode ke `~/JonoBot`, membuat venv + dependensi, menanyakan key Gemini (boleh dilewati, isi nanti di tab Integrasi), memasang passcode sesi dan hash kode admin dari kode akses, dan memasang service launchd `id.reza.jonobot` (autostart, restart otomatis).
3. Buka `http://127.0.0.1:8780/` di Safari atau Chrome. Tab Integrasi dibuka dengan KODE ADMIN dari pemilik.

Di mode lokal (`VA_LOCAL=1`) plugin kontrol_mac dan baca_dokumen menjalankan perintah langsung di Mac ini, bukan lewat SSH. Opsi installer: `--no-autostart`, `--port N`, `--uninstall`. Log di `~/JonoBot/logs/`. Update: `git pull && bash install_mac.sh` (kode akses dan data tidak ditimpa).

Batasan jujur: verifikasi kode berjalan offline di mesin penerima, jadi ini gerbang untuk instalasi resmi, bukan penguncian mutlak. Kode sumber terbuka bagi yang punya repo, dan kode yang sudah terbit tidak bisa dicabut dari jauh sebelum kedaluwarsa. Karena itu kode diberi masa berlaku.

## Persona
Kepribadian bot (gaya JARVIS, sapaan "Sir") ada di `data/persona.txt` (salinan awal: `persona.default.txt`). Ubah file itu untuk mengganti gaya, tanpa restart; hapus file untuk kembali ke persona bawaan kode.

## Penerbit kode
Kode akses diterbitkan dengan tooling terpisah yang private (bukan bagian repo ini). Repo ini hanya memuat kunci publik verifikasi (`tools/issuer_pub.txt`) dan `tools/verify_code.py`.

Catatan lain: notifikasi proaktif ke Telegram tidak aktif di instalasi lokal. Redirect URI OAuth untuk lokal: `http://127.0.0.1:8780/api/integrasi/oauth/callback`.
