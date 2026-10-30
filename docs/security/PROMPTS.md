# Промпты владельцам зон (чат 8 → чаты 0–5)

Каждый промпт — готовый текст для чата-владельца. Полные детали — `docs/security/REPORT.md`, тесты — `backend/tests/security/` в ветке `sec/v1`. Тесты с `xfail(reason="SEC-xx")` описывают критерий «починено»: после исправления они станут XPASS — сообщите чату 8, он снимет маркеры.

Порядок: **SEC-01 → SEC-02/03 → SEC-04 → остальное**. SEC-01 меняет интерфейс (cookie вместо `X-User-Id`), поэтому чаты 4 и 5 начинают после того, как чат 0 вольёт свою часть.

---

## Чат 0 · А. Сессия вместо X-User-Id, лимиты REST (SEC-01, SEC-03, SEC-11)

```text
Задача от чата 8 (безопасность). Сейчас пользователя назначает клиент: get_user_id в
backend/app/api/deps.py берёт X-User-Id (по умолчанию 1). Воспроизведение:
curl localhost:8080/api/items -H 'X-User-Id: 2' — отдаёт записи пользователя 2, без заголовка — пользователя 1.

Сделай минимальную серверную сессию для демо-входа:
1. config.py: session_secret: str = "" (SESSION_SECRET), demo_users: str = "1,2,3" (DEMO_USERS).
   При APP_ENV=prod и len(session_secret) < 32 — RuntimeError при создании приложения.
   Добавь обе переменные в .env.example и таблицу README.
2. Новый модуль app/api/session.py (stdlib hmac, без новых зависимостей):
   token = base64url(json{"uid":int,"sid":uuid4hex,"exp":unix}) + "." + base64url(HMAC-SHA256(secret, payload)).
   Проверка — hmac.compare_digest, exp, uid ∈ demo_users.
   POST /api/session {"user_id": int} → uid ∉ demo_users → 403; иначе Set-Cookie
   pomnyu_session=<token>; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400 и тело {"user_id","session_id"}.
   GET /api/session → {"user_id","session_id"} или 401. DELETE /api/session → удалить cookie.
3. deps.get_user_id: APP_ENV=prod → только из проверенной cookie, иначе HTTPException(401);
   вне prod — cookie, затем X-User-Id (int, 1..2_147_483_647, иначе 422), затем 1 — тесты и eval не ломаются.
   Вынеси функцию verify_cookie(raw) -> (uid, sid) | None, её же использует ws_voice.py (чат 4).
4. SessionStore в container.py: ключ (user_id, session_id); LRU на 10 000 записей, TTL 24 ч.
5. Лимиты (один воркер uvicorn → лимитер в памяти, без Redis): зависимость rate_limit(scope)
   на /api/chat и /api/undo — 20/мин на (user_id, session_id) и 600/час на сервис; /api/tts — 30/мин.
   Превышение → 429 + Retry-After. Значения — из env (RATE_CHAT_PER_MIN и т. п.), в тестах можно поднять.
6. deploy/shared/nginx-voice.conf: в начале файла
   limit_req_zone $binary_remote_addr zone=voice_req:10m rate=5r/s;
   limit_conn_zone $binary_remote_addr zone=voice_conn:10m;
   в location / — limit_req zone=voice_req burst=20 nodelay; отдельный location /ws/ с limit_conn voice_conn 10.
   (Файл сайта включается в http{}, зоны действуют только для voice — Планиметрику не затрагивают.)

Контракт: POST/GET/DELETE /api/session — новые эндпоинты; разошли сводку чатам 4 и 5.
Критерий «починено»: в ветке sec/v1 тесты backend/tests/security/test_access.py::test_prod_* ,
test_session_cookie_flags, test_forged_cookie_rejected, test_resources.py::test_chat_rate_limited,
test_tts_rate_limited — XPASS; make check зелёный.
```

## Чат 0 · Б. Инфраструктура и деплой (SEC-04, SEC-05, SEC-06, SEC-08, SEC-09, SEC-12)

```text
Задача от чата 8. Находки в твоей зоне (детали — docs/security/REPORT.md в ветке sec/v1):

SEC-04 (высокая) — деплой ключом root на общем сервере.
 a) .github/workflows/deploy.yml: job deploy → environment: production (секреты VPS_* перенести
    в Environment с required reviewer — владельцем); в ssh-action добавить
    fingerprint: ${{ secrets.VPS_FINGERPRINT }}; username — deploy; action закрепить по SHA (не тег).
    script: оставить пустую команду — сервер всё равно выполнит forced command.
 b) deploy/remote-deploy.sh (новый): set -euo pipefail; cd /opt/gorky_code;
    git fetch origin main; git reset --hard origin/main;
    docker compose -f docker-compose.yml -f docker-compose.shared.yml up -d --build; docker image prune -f.
    Аргументы и SSH_ORIGINAL_COMMAND игнорирует.
 c) deploy/setup-vps.sh: НЕ добавлять deploy в группу docker; установить скрипт в
    /usr/local/sbin/gorky-deploy (root:root 0755); /etc/sudoers.d/gorky-deploy:
    "deploy ALL=(root) NOPASSWD: /usr/local/sbin/gorky-deploy"; в подсказке —
    строка для authorized_keys: restrict,command="sudo -n /usr/local/sbin/gorky-deploy" ssh-ed25519 … gha-deploy
 d) README/деплой: шаги ротации — новый ключ, удалить старый из /root/.ssh/authorized_keys.

SEC-05 (средняя) — docker-compose.shared.yml: для api/db/web/caddy задать
deploy.resources.limits.cpus (api 1.5, остальные 0.5), memory (web, caddy — 128m),
pids_limit: 256, security_opt: [no-new-privileges:true], cap_drop: [ALL] для api и web,
logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}} для всех.

SEC-06 (средняя) — docker-compose.override.yml: "127.0.0.1:5432:5432" (Docker обходит ufw).
В config.py: при APP_ENV=prod и STORAGE_IMPL=postgres отказ старта, если пароль в DATABASE_URL — "app" или пустой.

SEC-08 (низкая) — api/system.py: _ics_escape вырезает \r и символы < 0x20 (кроме \n → "\\n");
RRULE пропускать только если re.fullmatch(r"[A-Z0-9=;,:+-]+", rrule) и dateutil.rrule.rrulestr
не падает, иначе строку RRULE не писать; сворачивать строки по 75 октетов (RFC 5545 §3.1).

SEC-09 (низкая) — Caddyfile, блок header: сначала Content-Security-Policy-Report-Only
(текст политики — в REPORT.md, раздел SEC-09), после проверки фронта чатом 5 — боевой
Content-Security-Policy; добавить -Server.

SEC-12 (низкая) — nginx:1.27-alpine → актуальная stable-ветка; .github/dependabot.yml
(pip, npm, github-actions, docker); в ci.yml джоб security: gitleaks git --log-opts=--all,
pip-audit (в venv после pip install -e .), npm audit --omit=dev.

Критерий «починено»: в backend/tests/security/test_infra.py и test_input.py тесты с SEC-04/05/06/08/09/12
— XPASS; make check зелёный; на стенде `ssh deploy@host` без ключевой команды выполняет только деплой.
```

## Чат 4 · Голос: аудио-«бомба», WebSocket, TTS (SEC-01/10 в WS, SEC-02, SEC-03, SEC-05, SEC-11)

```text
Задача от чата 8 (безопасность). Детали — docs/security/REPORT.md в ветке sec/v1.

SEC-02 (высокая) — voice/audio.py: decode_to_pcm16k декодирует весь файл, лимит 20 с
применяется только в STT. Воспроизведение:
  ffmpeg -f lavfi -i anullsrc=r=8000:cl=mono -t 9800 -c:a flac s2.flac   # 1,8 МБ
  → decode_to_pcm16k(data, "audio/webm;codecs=opus") = 313 МБ PCM, 8 с CPU, 670 МБ RSS.
Исправь:
 1. Входные опции перед -i: -t {max_utterance_s + 1}, -protocol_whitelist file,pipe,
    -f по белому списку mime (audio/webm→webm, audio/ogg→ogg, audio/mp4→mp4, audio/mpeg→mp3,
    audio/wav|x-wav|wave→wav). Неизвестный mime или контейнер не совпал → AudioError.
 2. Глобальный asyncio.Semaphore(VOICE_MAX_CONCURRENCY, по умолчанию 2) на decode+transcribe
    (и на synthesize — отдельный, 2). Если ждать > 5 с — ответ «Сервер занят, повторите».
 3. Промежуточные транскрипты: не чаще раза в 2 с и только по хвосту ≤ 6 с.

SEC-03 — WebSocket: ≤ 2 соединения на сессию, ≤ 20 всего (VOICE_MAX_SOCKETS), закрывать
после 60 с без сообщений; ≤ 10 реплик в минуту на сессию → {"type":"error","message":"Слишком часто, подождите немного"}.
/api/tts: Semaphore из п. 2.

SEC-01/SEC-10 (после того как чат 0 вольёт app/api/session.py) — ws_voice.py:
до ws.accept() проверить Origin ∈ {схема+хост запроса, CORS_ORIGINS}, иначе ws.close(1008);
в APP_ENV=prod пользователь — только verify_cookie(ws.cookies["pomnyu_session"]), иначе close(4401);
?user_id= и X-User-Id — только вне prod.
SEC-11 — session_id в WS: ^[A-Za-z0-9_-]{1,100}$, иначе close(1008).

SEC-05 — backend/Dockerfile (runtime): RUN useradd -r -u 10001 app && chown -R app /app/.tts-cache /models;
USER app. Проверь, что /dev/shm и кэш TTS доступны на запись, модели читаются.

Критерий «починено»: backend/tests/security/test_resources.py::test_decode_caps_duration,
test_decode_rejects_container_not_matching_mime, test_ws_session_id_length_limited,
test_access.py::test_ws_requires_session_in_prod, test_ws_rejects_foreign_origin,
test_infra.py::test_api_image_runs_as_non_root — XPASS; test_decode_normal_utterance_still_works
и tests/voice — зелёные; на стенде `docker compose exec api id -u` ≠ 0.
```

## Чат 1 · NLU: бюджет LLM (SEC-03)

```text
Задача от чата 8. Каждая фраза с уверенностью правил < NLU_RULES_THRESHOLD идёт в LLM; лимитов нет.
Любой бессмысленный текст в цикле = прямые расходы на GigaChat/YandexGPT.

В HybridNLU (backend/app/nlu/service.py) добавь глобальный бюджет вызовов LLM:
- LLM_MAX_PER_MIN (по умолчанию 60) и LLM_MAX_PER_DAY (2000) — скользящее окно в памяти (один воркер);
- сверх бюджета или после 3 ошибок подряд (circuit breaker на 60 с) — сразу фолбэк на правила,
  source="fallback", в debug — "llm_skipped": "budget" | "breaker";
- текст в LLM — не длиннее 300 символов (обрезать), max_tokens уже 400 — оставить.
Добавь переменные в .env.example.

Не меняй: в схеме функций (nlu/llm/tools.py) не должно появляться user_id/item_id, у моделей
аргументов — extra="ignore". Это проверяют backend/tests/security/test_input.py::test_tool_schema_exposes_no_ids
и test_injected_ids_are_dropped — они должны остаться зелёными.
Критерий: тест в tests/nlu/ — 61-й вызов за минуту не доходит до FakeLLM.
```

## Чат 3 · Данные: демо-пользователи, сброс демо, ICS-токены (BUG, SEC-01, SEC-07)

```text
Задача от чата 8.

1. Баг: README обещает пустых пользователей 2 и 3, но в PostgreSQL их нет — миграция и
   import-csv --user 1 создают только пользователя 1. На чистой базе:
   curl -X POST localhost:8080/api/chat -H 'X-User-Id: 2' -H 'Content-Type: application/json' \
     -d '{"text":"Напомни завтра в 10 тест","session_id":"a"}'
   → ForeignKeyViolation в логе, ответ «Что-то пошло не так». Исправь: при старте (cli/entrypoint)
   ensure_user для всех id из DEMO_USERS (по умолчанию 1,2,3). Тест в tests/storage/.
2. Защита демо на защите: кто угодно может войти как пользователь 1 и удалить записи.
   Команда `python -m app.cli reset-demo` — вернуть пользователя 1 к CSV (удалить созданное,
   восстановить удалённое, сбросить статусы), очистить пользователей 2 и 3. Идемпотентно.
3. На будущее (SEC-07): колонка users.ics_token (secrets.token_urlsafe(16)), уникальная; GET /calendar/{token}.ics
   ищет пользователя по ней; «demo» остаётся для пользователя 1. Можно отложить до после защиты.
```

## Чат 5 · Фронт: вход через сессию, 401/429, CSP (SEC-01, SEC-03, SEC-09)

```text
Задача от чата 8. Начинать после того, как чат 0 вольёт POST/GET/DELETE /api/session.

1. Login.tsx: выбор демо-пользователя → POST /api/session {"user_id"}; при загрузке — GET /api/session,
   401 → экран входа. Выход — DELETE /api/session.
2. api/http.ts: убрать заголовок X-User-Id и ?user_id= из URL WebSocket (cookie браузер приложит сам,
   запросы same-origin). На 401 любого запроса — экран входа; на 429 — сообщение
   «Слишком часто — подождите пару секунд», без повтора в цикле.
3. Мок-бэкенд (VITE_USE_MOCK=1) — тот же интерфейс входа.
4. CSP (SEC-09): чат 0 включит Content-Security-Policy-Report-Only (политика — REPORT.md, раздел SEC-09).
   Открой стенд, пройди сценарий из docs/pitch/DEMO.md с голосом и озвучкой, проверь консоль:
   нарушений CSP быть не должно (нет inline-скриптов, аудио — blob:, WebSocket — wss same-origin, service worker — 'self').
   Нарушения — пришли чату 0 и чату 8.
Критерий: npm run typecheck и build зелёные; в DevTools у запросов нет X-User-Id; после
перезагрузки страницы пользователь сохраняется (cookie).
```

---

# v2 (23.10, после `main@a2fa8cc`)

Подробности — `docs/security/REPORT.md`, раздел «v2». Тесты — ветка `sec/v2`.

## Чат 0 · В. Перед запуском harden-скрипта и деплоем (SEC-15, SEC-16, SEC-14, SEC-03)

```text
Задача от чата 8. Приёмка a2fa8cc: 16 тестов XPASS, маркеры сняты в sec/v2. Перед выкатом — четыре правки.

1. SEC-15 (высокая, блокирует запуск на сервере) — deploy/harden-deploy-user.sh.
   grep -vF "$(awk '{print $2}' "$key")" при пустом шаблоне удаляет ВСЕ строки root/authorized_keys:
   пустой gh_actions.pub или ключ без «ssh-ed25519» → у root 0 ключей. Многострочный .pub → вторая
   строка у deploy без restrict. Нет файла → скрипт молча пишет «Готово».
   Замени блок «key=… fi» на проверенный docs/security/harden-keyblock.proposed.sh (в sec/v2, встроен в 9e5f374;
   переменные KEY/ROOT_AK/DEPLOY_AK — только для теста, по умолчанию реальные пути).
   sudoers: писать во временный файл → visudo -cf tmp → install -m 0440 в /etc/sudoers.d;
   строка — "deploy ALL=(root) NOPASSWD: /usr/local/sbin/gorky-deploy \"\"" (без аргументов).
   В конец скрипта: chown -R root:root /opt/gorky_code; chmod 600 /opt/gorky_code/.env.
   Критерий: tests/security/test_infra.py::test_harden_script_cannot_lock_out_root — XPASS.

2. SEC-16 — deploy/smoke.sh: в prod /api/chat без cookie → 401, деплой красный. Перед чатом:
   curl -fsS -c "$jar" -b "$jar" -X POST "$URL/api/session" -H 'Content-Type: application/json' -d '{"user_id":1}'
   и -b "$jar" у запроса чата (jar=$(mktemp)).

3. SEC-14 — app/api/session.py, verify_cookie: hmac.compare_digest на str с не-ASCII → TypeError → 500.
   Сравнивать байты (mac.encode("utf-8", "surrogateescape"), want.encode()) или ловить TypeError → None.
   Критерий: test_access.py::test_garbage_cookie_gives_401_not_500 — XPASS.

4. SEC-03 (обход) — ключ лимита — session_id из тела: 40 разных id → 40 × 200, и один клиент
   за ~2 мин выбирает общий лимит 600/ч, после чего чат закрыт для всех на час.
   - ключ chat:{user_id}:{sid}, где sid — из cookie (verify_cookie); вне prod без cookie — как сейчас;
   - POST /api/session: 10/мин на IP из X-Real-IP (доверять только от 127.0.0.1/шлюза Docker);
   - nginx-voice.conf: зона voice_chat rate=30r/m для location = /api/chat и /api/undo;
   - общий часовой лимит поднять до 3000 — от расходов на LLM защищает бюджет чата 1.
   Критерий: test_resources.py::test_chat_limit_not_bypassed_by_rotating_session_id — XPASS.

Порядок выката на стенд (в REPORT.md, раздел v2): сначала ALTER USER app PASSWORD в живой базе
(смена POSTGRES_PASSWORD в .env не меняет пароль в существующем томе — иначе api не стартует),
SESSION_SECRET, ALLOW_HEADER_AUTH=true до web/v4; harden-скрипт — только исправленный и с открытой
второй root-сессией.
```

## Чат 4 · напоминание (SEC-01 WS, SEC-10, SEC-02, SEC-05, SEC-11)

```text
От чата 8. REST-часть SEC-01 в main: app.api.session.verify_cookie(settings, raw) -> (user_id, sid) | None.
Сейчас /ws/voice при APP_ENV=prod без cookie принимает ?user_id=2 и отдаёт записи пользователя 2 —
на стенде SEC-01 открыт через WebSocket. В ws_voice.py до ws.accept():
  - Origin ∈ {https://<Host>, CORS_ORIGINS}, иначе ws.close(1008);
  - prod и not settings.allow_header_auth: user из verify_cookie(settings, ws.cookies.get("pomnyu_session")),
    иначе close(4401); ?user_id= / X-User-Id — только вне prod или в переходном режиме;
  - session_id: ^[A-Za-z0-9_-]{1,100}$, иначе close(1008).
Плюс без изменений с v1: SEC-02 (ffmpeg -t/-f перед -i, семафор), USER app в Dockerfile.
Критерий: в sec/v2 XPASS у test_ws_requires_session_in_prod, test_ws_rejects_foreign_origin,
test_ws_session_id_length_limited, test_decode_caps_duration, test_decode_rejects_container_not_matching_mime,
test_api_image_runs_as_non_root.
```

## Чат 5 · напоминание (SEC-01 фронт, SEC-09)

```text
От чата 8. Бэкенд готов: POST/GET/DELETE /api/session (docs/CONTRACT.md, API v1.3). Пока фронт шлёт
X-User-Id, стенд работает только с ALLOW_HEADER_AUTH=true — то есть без защиты. Промпт v1 (раздел
«Чат 5») в силе. После перехода: пройди демо-сценарий и проверь консоль на нарушения
Content-Security-Policy-Report-Only — их список нужен чату 0, чтобы включить боевой CSP
(тест test_infra.py::test_caddy_enforces_csp).
```

---

# v3 (23.10, после `main@82a23b5`)

Подробности и замеры — `docs/security/REPORT.md`, раздел «v3». Тесты — ветка `sec/v3`.

## Чат 0 · Г. Адрес клиента, блокировка деплоя, connect-src (SEC-17, SEC-19, SEC-09)

```text
Задача от чата 8. Приёмка 82a23b5: всё принято, harden-скрипт проверен в песочнице (sshd, sudo,
forced command) — можно запускать по порядку из REPORT.md v2. Три правки, стенд не блокируют.

1. SEC-17 (средняя) — client_ip() в api/session.py верит X-Real-IP от любого «внутреннего» пира.
   За Caddy (на хосте по Caddyfile.snippet или в режиме prod.yml) X-Real-IP клиента проходит
   насквозь → лимит входа обходится, а с ним и лимит чата на cookie. За nginx из nginx-voice.conf
   подделки нет. Корень: uvicorn --forwarded-allow-ips=* берёт самый ЛЕВЫЙ X-Forwarded-For.
   Проверено на цепочке nginx/Caddy → Caddy 2.10 → uvicorn 0.54. Исправление (все три режима дают
   настоящий адрес, присланные X-Real-IP/X-Forwarded-For игнорируются):
   a) Caddyfile, глобальный блок: servers { trusted_proxies static private_ranges }
   b) client_ip(request) → request.client.host; X-Real-IP не читать
   c) чат 4 меняет --forwarded-allow-ips в Dockerfile (см. его промпт) — a) без c) делает ХУЖЕ:
      Caddy начнёт пропускать X-Forwarded-For клиента, а uvicorn с * возьмёт его. Вливать a)+b)+c) вместе.
   Критерий: test_caddy_trusts_private_proxies_for_client_ip, test_internal_peer_header_does_not_choose_rate_key,
   test_uvicorn_trusts_only_private_proxies — XPASS; test_nginx_overwrites_real_ip_in_every_location
   и test_public_client_cannot_reset_login_limit_with_header — зелёные.

2. SEC-19 (низкая) — deploy/remote-deploy.sh, сразу после set -euo pipefail:
     exec 9>/run/gorky-deploy.lock
     flock -n 9 || { echo "деплой уже идёт"; exit 0; }
   После правки — снова запустить harden-deploy-user.sh (он копирует скрипт в /usr/local/sbin).
   Критерий: test_remote_deploy_is_serialized — XPASS.

3. SEC-09 (низкая) — Caddyfile: connect-src 'self' wss://{host} вместо 'self' wss:
   ({host} Caddy подставит сам). Проверить голос на iPhone/Safari после выката.
   Критерий: test_csp_connect_src_same_origin_only — XPASS.
```

## Чат 4 · Лимиты WebSocket по cookie, доверенные прокси (SEC-18, SEC-17)

```text
Задача от чата 8. Приёмка: SEC-01/10/11 в WS, SEC-02 (включая длительность — xfail был ошибкой
теста, исправлен), SEC-05 — приняты.

1. SEC-18 (средняя) — ws_voice.py: ключ лимита реплик voice:{user_id}:{session_id} и ключ
   SocketRegistry берут session_id из query — его выбирает клиент. 15 сокетов с session_id=rot0…rot14
   (prod, с cookie) → 15 ответов, ни одного «Слишком часто». STT тогда ограничен только семафором,
   и один клиент держит очередь занятой.
   Исправление: _resolve_user возвращает и sid из verify_cookie; ключ лимита и SocketRegistry —
   (user_id, cookie_sid); session_id из query — только для состояния диалога. Без cookie (вне prod) — как сейчас.
   Критерий: tests/security/test_resources.py::test_ws_turn_limit_not_bypassed_by_rotating_session_id — XPASS,
   test_ws_turn_limit_same_session — зелёный.

2. SEC-17 — backend/Dockerfile, CMD uvicorn: вместо --forwarded-allow-ips=* —
   --forwarded-allow-ips=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1
   Вливать вместе с правкой Caddyfile чата 0 (trusted_proxies), не раньше.
   Критерий: test_infra.py::test_uvicorn_trusts_only_private_proxies — XPASS.
```
