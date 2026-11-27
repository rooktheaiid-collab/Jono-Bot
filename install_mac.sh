#!/bin/bash
# Jono Bot: pasang lokal di macOS (jalan di 127.0.0.1:8780, autostart lewat launchd).
# Pakai:  bash install_mac.sh [--no-autostart] [--port 8780] [--uninstall]
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/JonoBot"
LABEL="id.reza.jonobot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=8780; AUTOSTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-autostart) AUTOSTART=0 ;;
    --port) shift; PORT="$1" ;;
    --uninstall)
      launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
      rm -f "$PLIST"
      echo "Service dihapus. Kode dan data tetap di $APP (hapus manual kalau mau)."; exit 0 ;;
  esac; shift
done
[ "$(uname -s)" = "Darwin" ] || { echo "Script ini cuma buat macOS."; exit 1; }
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 belum ada. Jalankan: xcode-select --install (atau brew install python), lalu ulangi."; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' || { echo "Butuh Python 3.9 atau lebih baru."; exit 1; }

echo "1/5 Salin kode ke $APP"
mkdir -p "$APP/data"
if [ "$SRC" != "$APP" ]; then
  rsync -a --exclude venv --exclude data --exclude .git --exclude '*.bak*' --exclude __pycache__ "$SRC/" "$APP/"
fi

echo "2/5 Pasang dependensi (venv)"
[ -d "$APP/venv" ] || "$PY" -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q aiohttp websockets cryptography pypdf python-docx openpyxl pymupdf

echo "3/5 Kunci dan kode akses"
if [ -s "$HOME/.voice_agent_passcode" ] && [ -s "$HOME/.admin_code_hash" ]; then
  echo "Kode akses sudah terpasang (instalasi sebelumnya), dipakai lagi."
else
  CODE="${JONOBOT_CODE:-}"
  if [ -z "$CODE" ]; then
    echo "Kode akses diterbitkan oleh pemilik Jono Bot. Belum punya? Minta dulu ke rook.the.ai.id@gmail.com."
    printf "Tempel KODE AKSES: "; read -r CODE
  fi
  "$APP/venv/bin/python" "$APP/tools/verify_code.py" "$CODE" || { echo "Instalasi dibatalkan."; exit 1; }
fi
if [ ! -s "$HOME/.gemini_api_key" ]; then
  printf "API key Gemini (Enter untuk lewati, bisa diisi nanti di tab Integrasi): "
  read -rs GK; echo
  if [ -n "$GK" ]; then ( umask 077; printf '%s' "$GK" > "$HOME/.gemini_api_key" ); fi
fi
chmod 700 "$HOME/.ssh" 2>/dev/null || true

echo "4/5 Service launchd"
if [ "$AUTOSTART" = "1" ]; then
  mkdir -p "$HOME/Library/LaunchAgents" "$APP/logs"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/venv/bin/python</string><string>$APP/server.py</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>EnvironmentVariables</key><dict>
    <key>VA_LOCAL</key><string>1</string>
    <key>HOST</key><string>127.0.0.1</string>
    <key>PORT</key><string>$PORT</string>
    <key>PUBLIC_URL</key><string>http://127.0.0.1:$PORT</string>
    <key>VA_DATA</key><string>$APP/data</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$APP/logs/out.log</string>
  <key>StandardErrorPath</key><string>$APP/logs/err.log</string>
</dict></plist>
PL
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl kickstart -k "gui/$(id -u)/$LABEL" || true
else
  echo "Autostart dilewati. Jalankan manual: VA_LOCAL=1 VA_DATA=$APP/data PORT=$PORT PUBLIC_URL=http://127.0.0.1:$PORT $APP/venv/bin/python $APP/server.py"
fi

echo "5/5 Cek"
OK=0
for i in 1 2 3 4 5 6 7 8; do
  sleep 1
  if curl -fs -m 3 "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then OK=1; break; fi
done
if [ "$OK" = "1" ]; then echo "Jalan: http://127.0.0.1:$PORT/"; else echo "Belum merespons. Lihat $APP/logs/err.log"; fi
echo
echo "Buka di Safari/Chrome: http://127.0.0.1:$PORT/  (mikrofon boleh diizinkan di localhost)"
echo "Kode admin menu Integrasi: pakai kode admin yang dikirim pemilik (terpisah dari kode akses)."
