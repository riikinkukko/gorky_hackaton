#!/usr/bin/env bash
# Смоук-тест стенда: ./deploy/smoke.sh https://assistant.example.ru
set -euo pipefail
URL="${1:-http://localhost:8080}"
URL="${URL%/}"
echo "→ $URL"

for i in $(seq 1 30); do
  if curl -fsS "$URL/health" -o /tmp/health.json; then break; fi
  echo "  ждём /health ($i)…"; sleep 3
done
echo "health: $(cat /tmp/health.json)"
grep -q '"db":"ok"' /tmp/health.json
if grep -q '"seed_items":0' /tmp/health.json; then echo "FAIL: база пуста — CSV не загрузился (см. docker compose logs api | grep seed)"; exit 1; fi

# В prod чат требует сессию (SEC-01/SEC-16): входим демо-пользователем 1, cookie — в jar
jar=$(mktemp); trap 'rm -f "$jar"' EXIT
curl -fsS -c "$jar" -b "$jar" -X POST "$URL/api/session" -H 'Content-Type: application/json' \
  -d '{"user_id":1}' >/dev/null
reply=$(curl -fsS -b "$jar" -X POST "$URL/api/chat" -H 'Content-Type: application/json' \
  -d '{"text":"Что у меня сегодня?","session_id":"smoke"}')
echo "chat: ${reply:0:200}…"
echo "$reply" | grep -q '"action":"listed"'

curl -fsS "$URL/" | grep -qi '<div id="root">'
echo "web: ok"

case "$URL" in https://*) echo "https: ok (микрофон в браузере будет работать)";; esac
echo "SMOKE OK"
