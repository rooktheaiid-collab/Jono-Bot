#!/bin/bash
# Snapshot harian memori voice-agent -> ~/backups/voice-memory-TIMESTAMP.tar.gz, simpan 14 terakhir.
set -euo pipefail
D=/home/ubuntu/voice-agent; B=/home/ubuntu/backups; mkdir -p "$B"
[ -f "$D/data/memory.db" ] || exit 0
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
python3 - "$D/data/memory.db" "$T/memory.db" <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()
PY
tar czf "$B/voice-memory-$(date +%Y%m%d-%H%M%S).tar.gz" -C "$T" memory.db
ls -1t "$B"/voice-memory-*.tar.gz | tail -n +15 | xargs -r rm -f
