#!/usr/bin/env bash
# Слепой тест: вернуть пользователя 3 к исходным данным перед КАЖДЫМ участником.
# Запуск на сервере из каталога репозитория:  bash qa/blind/reset_user3.sh
# Трогает только пользователя 3: удаляет его записи и состояние диалога, затем импортирует
# qa/blind/seed_user3.csv (12 записей: 6 прошлых, 6 будущих). Демо-данные пользователя 1 не меняются.
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.shared.yml"
PGUSER_="${POSTGRES_USER:-app}"
PGDB_="${POSTGRES_DB:-app}"

$COMPOSE exec -T db psql -U "$PGUSER_" -d "$PGDB_" -v ON_ERROR_STOP=1 -q -c \
  "DELETE FROM dialog_sessions WHERE user_id = 3; DELETE FROM items WHERE user_id = 3;"
$COMPOSE cp qa/blind/seed_user3.csv api:/tmp/seed_user3.csv
$COMPOSE exec -T api python -m app.cli import-csv /tmp/seed_user3.csv --user 3
$COMPOSE exec -T db psql -U "$PGUSER_" -d "$PGDB_" -t -A -c \
  "SELECT 'у пользователя 3 записей: ' || count(*) FROM items WHERE user_id = 3 AND deleted_at IS NULL;"
echo "Готово. Время сброса: $(date '+%H:%M:%S') — впишите в лист ведущего."
