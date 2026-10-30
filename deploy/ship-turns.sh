#!/usr/bin/env bash
# Реплики со стенда → приватный репозиторий логов (раз в час по cron).
# Уходят только строки «turn session» (фраза, intent, ответ); почты и длинные числа маскируются.
# Установка — deploy/README-logs.md. Только отправка: входящих подключений не открывает.
set -euo pipefail
exec 8>/run/gorky-ship-turns.lock
flock -n 8 || exit 0

APP_DIR=${APP_DIR:-/opt/gorky_code}
LOGS_DIR=${LOGS_DIR:-/opt/gorky_logs}
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

cd "$APP_DIR"
docker compose -f docker-compose.yml -f docker-compose.shared.yml \
  logs api --since 2h --no-log-prefix --no-color 2>/dev/null \
  | grep -F "turn session" \
  | perl -pe 'if (/^(.*? text=)(.*)$/s) { my ($h, $t) = ($1, $2);
      $t =~ s/[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}/<email>/g; $t =~ s/\d[\d ()-]{8,}\d/<num>/g;
      $_ = $h . $t }' \
  > "$tmp" || true
[ -s "$tmp" ] || exit 0

cd "$LOGS_DIR"
git pull -q --rebase || true
mkdir -p turns
# по файлу на день; повторы (окно 2 ч перекрывает прошлый запуск) убирает sort -u
awk -v d="turns" '$1 ~ /^20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]$/ {print >> (d "/" $1 ".log")}' "$tmp"
for f in turns/*.log; do sort -u -o "$f" "$f"; done
git add turns
git diff --cached --quiet && exit 0
git -c user.name="gorky-stand" -c user.email="stand@gorky.local" commit -qm "turns $(date -u +'%F %H:%M')"
git push -q
