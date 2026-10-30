SHELL := /bin/bash
PY ?= $(shell [ -x backend/.venv/bin/python ] && echo $(CURDIR)/backend/.venv/bin/python || echo python3)
COMPOSE_PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: help install up down logs ps test lint fmt typecheck eval eval-real migrate seed check dev-api dev-web adversarial sessions \
        web-build prod-up shared-up prod-logs smoke

help:  ## список команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

install:  ## venv + зависимости бэкенда и фронта
	cd backend && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"
	cd frontend && npm ci

# ---------- docker ----------
up:  ## поднять всё локально → http://localhost:8080
	docker compose up -d --build
	@echo "→ http://localhost:8080   (API docs: http://localhost:8080/docs)"

down:  ## остановить
	docker compose down

logs:  ## логи всех сервисов
	docker compose logs -f --tail=100

ps:
	docker compose ps

# ---------- качество ----------
test:  ## pytest
	cd backend && $(PY) -m pytest -q

lint:  ## ruff + mypy + tsc
	cd backend && $(PY) -m ruff check . && $(PY) -m ruff format --check . && $(PY) -m mypy app
	cd frontend && npm run typecheck

fmt:  ## автоформат
	cd backend && $(PY) -m ruff check --fix . && $(PY) -m ruff format .

eval:  ## golden_eval на заглушках (без БД) → таблица метрик
	cd backend && $(PY) -m eval.run_eval eval/golden_eval.jsonl $(EVAL_ARGS)

eval-real:  ## golden_eval на настоящих модулях + PostgreSQL (нужен DATABASE_URL, см. make up)
	cd backend && NLU_IMPL=real DIALOG_IMPL=real STORAGE_IMPL=postgres $(PY) -m eval.run_eval eval/golden_eval.jsonl --min-recall 0.9 $(EVAL_ARGS)

adversarial:  ## состязательные реплики чата 9 + оракулы (≈10 мин; TASK=find_old ONLY=fo-001,fo-002)
	cd backend && NLU_IMPL=real DIALOG_IMPL=real STORAGE_IMPL=postgres $(PY) -m eval.run_adversarial $(if $(TASK),--task $(TASK)) $(if $(ONLY),--only $(ONLY)) $(ADV_ARGS)

sessions:  ## разговоры целиком + все кнопки-подсказки (qa/sessions); демо: make sessions SES=../qa/sessions/demo.jsonl
	cd backend && NLU_IMPL=real DIALOG_IMPL=real STORAGE_IMPL=postgres $(PY) -m eval.run_sessions $(SES) $(SES_ARGS)

migrate:  ## миграции БД вручную (при старте API выполняются сами)
	docker compose exec api alembic -c app/storage/alembic.ini upgrade head

seed:  ## импорт CSV организаторов в пользователя 1 (на пустой базе id = id из CSV)
	docker compose exec api python -m app.cli import-csv /app/data/voice_assistant_events_1000.csv --user 1

check: lint test eval  ## всё перед мержем в main

# ---------- разработка без docker ----------
dev-api:  ## uvicorn с автоперезагрузкой на :8000
	cd backend && SEED_CSV=../data/voice_assistant_events_1000.csv $(PY) -m uvicorn app.main:app --reload

dev-web:  ## vite на :5173 (проксирует /api и /ws на :8000)
	cd frontend && npm run dev

web-build:
	cd frontend && npm run build

# ---------- прод (запускать на VPS) ----------
prod-up:  ## пересобрать и поднять на VPS с HTTPS
	git pull --ff-only
	$(COMPOSE_PROD) up -d --build
	$(COMPOSE_PROD) ps

shared-up:  ## прод «соседом» на занятом сервере: только 127.0.0.1:8080, HTTPS у существующего веб-сервера
	git pull --ff-only
	docker compose -f docker-compose.yml -f docker-compose.shared.yml up -d --build
	docker compose -f docker-compose.yml -f docker-compose.shared.yml ps

prod-logs:
	$(COMPOSE_PROD) logs -f --tail=100

smoke:  ## смоук-тест стенда: make smoke URL=https://домен
	./deploy/smoke.sh $(or $(URL),http://localhost:8080)
