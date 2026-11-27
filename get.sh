#!/bin/bash
# Pemasang satu perintah Jono Bot (macOS).
#   Repo public : curl -fsSL https://raw.githubusercontent.com/rooktheaiid-collab/Jono-Bot/main/get.sh | bash
#   Repo private: GITHUB_TOKEN=xxx bash get.sh   (token read-only dari pemilik)
# Kode akses: JONOBOT_CODE=JB1.... bash get.sh  (atau akan ditanya)
set -euo pipefail
REPO="rooktheaiid-collab/Jono-Bot"
DEST="${JONOBOT_SRC:-$HOME/Jono-Bot}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
AUTH=(); [ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")
echo "Mengunduh Jono Bot..."
curl -fsSL "${AUTH[@]}" "https://api.github.com/repos/$REPO/tarball/main" -o "$TMP/jb.tgz" \
  || { echo "Gagal unduh. Kalau repo private, set GITHUB_TOKEN."; exit 1; }
mkdir -p "$TMP/x" && tar xzf "$TMP/jb.tgz" -C "$TMP/x" --strip-components=1
mkdir -p "$DEST" && cp -R "$TMP/x/." "$DEST/"
cd "$DEST"
# saat dijalankan lewat curl | bash, stdin adalah pipe; ambil input dari terminal
if [ ! -t 0 ] && [ -r /dev/tty ]; then exec bash install_mac.sh "$@" </dev/tty; fi
exec bash install_mac.sh "$@"
