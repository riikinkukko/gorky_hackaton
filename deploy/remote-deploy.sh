#!/usr/bin/env bash
# Единственное, что может сделать ключ GitHub Actions на сервере (SEC-04).
# Ставится в /usr/local/sbin/gorky-deploy (root:root 0755), вызывается через sudo по forced command:
#   restrict,command="sudo -n /usr/local/sbin/gorky-deploy" ssh-ed25519 AAAA… gha-deploy
# Аргументы и SSH_ORIGINAL_COMMAND игнорируются.
set -euo pipefail
# SEC-19: второй запуск во время сборки — тихо выходим
exec 9>/run/gorky-deploy.lock
flock -n 9 || { echo "деплой уже идёт"; exit 0; }
cd /opt/gorky_code
git fetch --quiet origin main
git reset --quiet --hard origin/main
docker compose -f docker-compose.yml -f docker-compose.shared.yml up -d --build --remove-orphans
docker image prune -f >/dev/null
docker compose -f docker-compose.yml -f docker-compose.shared.yml ps
