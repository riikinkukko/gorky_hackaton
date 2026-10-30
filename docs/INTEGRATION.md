# Интеграция направлений (ведёт чат 0)

## Ветки и зоны

| Чат | Ветка | Папки |
|---|---|---|
| 0 · Каркас | `main`, `infra/*` | `backend/app/contracts.py`, `container.py`, `api/`, `fakes/`, `docker-compose*.yml`, `Caddyfile`, `Makefile`, `.github/`, `deploy/`, `frontend/src/api/contracts.ts` |
| 1 · NLU и даты | `nlu/*` | `backend/app/nlu/` |
| 2 · Диалог | `dialog/*` | `backend/app/dialog/`, `backend/app/domain/` |
| 3 · Данные | `storage/*` | `backend/app/storage/`, `backend/app/cli.py`, `backend/eval/`, миграции |
| 4 · Голос | `voice/*` | `backend/app/voice/`, `backend/app/api/ws_voice.py`, `backend/Dockerfile` |
| 5 · Фронт | `web/*` | `frontend/` (кроме `src/api/contracts.ts`) |
| 6 · Продукт | `docs/*` | `README.md`, `docs/`, `backend/eval/golden_eval.jsonl` (вместе с 3) |
| 7 · Тестирование | `qa/*` | `qa/` (записи, сценарии, отчёты), `backend/tests/e2e/`, `backend/eval/asr/`; новые строки эталона — через чат 3 |
| 8 · Безопасность | `sec/*` | `docs/security/`, `backend/tests/security/`; исправления в чужих зонах — только промптом владельцу, кроме согласованных с чатом 0 |
| 9 · Состязательные реплики | `qa/adversarial-*` | `qa/adversarial/` (сценарии и `build.py`); прогон — `backend/eval/run_adversarial.py` (чат 0), workflow Adversarial |

Тесты своего модуля кладите в `backend/tests/<модуль>/`.

Чаты 7 и 8 находят проблемы, а чинят их владельцы зон. Каждая находка оформляется как готовый промпт владельцу: что, где, как воспроизвести, критерий «починено».

## Как модуль подключается

Каждый пакет экспортирует фабрики в `__init__.py`; `app/container.py` вызывает их, когда включена переменная:

| Переменная | Фабрика |
|---|---|
| `NLU_IMPL=real` | `app.nlu.build_date_resolver(settings)`, `app.nlu.build_nlu(settings, dates)` |
| `STORAGE_IMPL=postgres` | `app.storage.build_repository(settings)`, `app.storage.build_search(settings, repo)` |
| `STT_PROVIDER≠fake`, `TTS_PROVIDER≠fake` | `app.voice.build_stt(settings)`, `app.voice.build_tts(settings)` |
| `DIALOG_IMPL=real` | `app.dialog.build_orchestrator(*, settings, nlu, dates, repo, search)` |

Модули можно включать по одному: например, реальный NLU поверх заглушки диалога.

## Соглашения поверх контракта

- `Orchestrator.handle` **мутирует** переданный `DialogState`; API сохраняет его после вызова.
- `ItemRepository.create` игнорирует входной `id` и выдаёт свой (в запросе передавайте `id=0`).
- `POST /api/undo` вызывает `orchestrator.handle("отмени", …)` — NLU обязан распознавать «отмени» как `Intent.UNDO`.
- Запись попадает в период по `starts_at`, а если его нет — по `due_at`.
- WebSocket: перед аудио клиент может прислать `{"type":"start","mime":…}`; ответ `reply` содержит `audio_mime`; ошибки — `{"type":"error","message":…}`.
- Время всегда tz-aware; «сейчас» — только через параметр `now` (`settings.now()` учитывает `FREEZE_NOW`).

## Контракт

Актуальная версия и история изменений — `docs/CONTRACT.md`.

## Изменение контракта

Чат просит → вы переносите просьбу в чат 0 → чат 0 меняет `contracts.py`, `frontend/src/api/contracts.ts`, заглушки и этот файл → вы рассылаете сводку остальным.

## Чек-лист перед мержем в main

1. `make check` зелёный (ruff, mypy, tsc, pytest, eval).
2. Чужие папки не тронуты (кроме согласованного).
3. Если появилась зависимость — она в `backend/pyproject.toml` или `frontend/package.json`.
4. Новые переменные окружения — в `.env.example` и в таблице README.
5. Сводка сессии: что сделано, что изменилось в интерфейсах, что дальше.
