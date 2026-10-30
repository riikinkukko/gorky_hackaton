#!/bin/sh
# Старт API: при STORAGE_IMPL=postgres — миграции и идемпотентный импорт CSV (повторный запуск пропускает существующие).
# Ошибка импорта не роняет API, но громко пишется в лог; /health покажет seed_items=0.
set -e
echo "entrypoint: STORAGE_IMPL=${STORAGE_IMPL:-memory} SEED_CSV=${SEED_CSV:-<не задан>} SEED_USER_ID=${SEED_USER_ID:-1}"
if [ "${STORAGE_IMPL:-memory}" = "postgres" ]; then
  alembic -c app/storage/alembic.ini upgrade head
  if [ -z "${SEED_CSV:-}" ]; then
    echo "seed: ПРОПУЩЕН — SEED_CSV не задан"
  elif [ ! -f "$SEED_CSV" ]; then
    echo "seed: ПРОПУЩЕН — файла $SEED_CSV нет (смонтирован ли ./data?)"
  elif out=$(python -m app.cli import-csv "$SEED_CSV" --user "${SEED_USER_ID:-1}" 2>&1); then
    echo "seed: ok $(echo "$out" | tr -d '\n ')"
  else
    echo "seed: ОШИБКА импорта CSV:"; echo "$out" | tail -15
  fi
fi
exec "$@"
