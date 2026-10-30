#!/usr/bin/env bash
# Чат 0: раскладывает архивы направлений из incoming/ по веткам и сливает в main.
#   incoming/storage.zip  → storage/v1  (backend/app/storage, backend/app/cli.py, backend/eval)
#   incoming/dialog.zip   → dialog/v1   (backend/app/dialog, backend/app/domain, backend/tests/dialog)
#   incoming/web.zip      → web/v1      (frontend/, кроме src/api/contracts.ts)
#   incoming/voice.zip    → voice/v1    (backend/app/voice, ws_voice.py, Dockerfile, THIRD_PARTY.md)
#   incoming/nlu.zip      → nlu/v1      (backend/app/nlu, backend/tests/nlu)
# После каждой ветки: make check. Красное — ветка не мержится, причина пишется в сводку.
set -euo pipefail
for name in storage dialog web voice nlu; do
  f="incoming/$name.zip"; [ -f "$f" ] || f="incoming/$name.tar.gz"; [ -f "$f" ] || continue
  echo "== $name =="
  git checkout -q main && git checkout -q -B "$name/v1"
  tmp=$(mktemp -d); case "$f" in *.zip) unzip -q "$f" -d "$tmp";; *) tar -xzf "$f" -C "$tmp";; esac
  rsync -a --exclude node_modules --exclude .venv --exclude 'frontend/src/api/contracts.ts' "$tmp"/ ./
  git add -A && git commit -qm "merge($name): архив направления" || true
done
