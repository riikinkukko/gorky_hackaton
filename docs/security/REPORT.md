# Безопасность «Помню» — отчёт v3

Зона: чат 8 · ветка `sec/v3` · v1 — `main@67f049f` (22.10), v2 — `main@a2fa8cc`, v3 — `main@82a23b5` (23.10) · тесты — `backend/tests/security/`

## Коротко (v3)

Все находки v1 и v2 закрыты в коде, включая SEC-02 по длительности: остававшийся xfail был ошибкой моего теста, а не кода (см. ниже). Harden-скрипт проверен целиком в песочнице, с настоящими `adduser`/`visudo`/`sshd` и входом по ключу, — **его можно запускать** по порядку из раздела v2.

**Новое в v3 (средняя и ниже, стенд не блокирует):**
1. **SEC-17 · адрес клиента подделывается, если перед стеком стоит Caddy, а не nginx.** С nginx из `deploy/shared/nginx-voice.conf` всё верно. Корень — `uvicorn --forwarded-allow-ips=*`.
2. **SEC-18 · лимиты WebSocket обходятся сменой `?session_id=`** — та же ошибка, что была в REST до v2.
3. SEC-19 · `remote-deploy.sh` без блокировки от параллельного запуска; SEC-09 · `connect-src` разрешает `wss:` к любому хосту — низкие.

| ID | Находка | Серьёзность | Владелец | Статус (`82a23b5`) |
|---|---|---|---|---|
| SEC-01 | Пользователь назначается клиентом | **Высокая** | 0, 4, 5 | ✅ REST, WS, фронт (web/v4) — на стенде после `ALLOW_HEADER_AUTH=false` |
| SEC-02 | Аудио-«бомба» | **Высокая** | 4 | ✅ `-f`, `-t`, `-protocol_whitelist`, семафоры |
| SEC-03 | Лимиты частоты | **Высокая** | 0, 1, 4 | ✅ REST (ключ из cookie, вход на IP, nginx 30r/m) · ❌ WS — SEC-18 · LLM — чат 1 (не проверял) |
| SEC-04 | Ключ деплоя = root | **Высокая** | 0 + владелец | ✅ код; на сервере — после запуска скрипта |
| SEC-05 | Контейнеры | Средняя | 4, 0 | ✅ `USER app`, лимиты, `cap_drop` |
| SEC-06…08, 10…14, 16 | Postgres, ICS, Origin, сессии, цепочка поставки, cookie, смоук | — | 0, 4 | ✅ |
| SEC-09 | CSP | Низкая | 0 | ✅ боевой; ⚠ `connect-src … wss:` — любой хост |
| SEC-15 | Harden-скрипт мог стереть ключи root | **Высокая** | 0 | ✅ проверен в песочнице (ниже) |
| SEC-17 | **Новое.** Подделка адреса клиента за Caddy | Средняя | 0 | ❌ |
| SEC-18 | **Новое.** Лимиты WS по `session_id` из query | Средняя | 4 | ❌ |
| SEC-19 | **Новое.** Нет блокировки параллельного деплоя | Низкая | 0 | ❌ |

**Как устроены тесты.** Исправленное проверяют обычные тесты (регрессия). Неисправленное — тесты с `xfail(reason="SEC-xx")`: они описывают критерий «починено». Сейчас в `tests/security`: **67 passed, 6 xfailed** (с PostgreSQL); каждый xfail проверен через `--runxfail` — падает именно по своей причине.

## v3 · Проверка исправлений (`main@82a23b5`)

### Принято

| Исправление | Как проверено |
|---|---|
| SEC-14: `compare_digest` по байтам, лимит длины cookie | `test_garbage_cookie_gives_401_not_500` |
| SEC-03: ключ лимита чата — `sid` из cookie; вход 10/мин на IP; nginx `voice_chat 30r/m`; 3000/ч на сервис | `test_chat_limit_not_bypassed_by_rotating_session_id`, `test_login_rate_limited_per_ip` (переписан, см. ниже) |
| SEC-01/10/11 (WS): cookie, `Origin` против Host и `CORS_ORIGINS`, `session_id` по регулярке, 4401 без входа | `test_ws_requires_session_in_prod`, `test_ws_rejects_foreign_origin`, `test_ws_session_id_length_limited` |
| SEC-02: белый список контейнеров (`-f`), `-t` до чтения, `-protocol_whitelist file,pipe`, семафоры STT/TTS, хвост для промежуточных | `test_decode_caps_duration[120, 600]` — ≤ 21 с звука и < 5 с; `test_decode_rejects_container_not_matching_mime` |
| SEC-05: `USER app` (uid 10001), `COPY --chown` весов | `test_api_image_runs_as_non_root` |
| SEC-09: боевой CSP, шрифты локально | `test_caddy_enforces_csp` |
| SEC-16: смоук входит через cookie | прогон `deploy/smoke.sh` на локальном prod-API по http: вход и чат проходят (curl отдаёт Secure-cookie на localhost) |
| SEC-15: harden-скрипт | песочница, см. ниже |

**SEC-02, «остался только duration».** Исправление чата 4 работает. Тест v1 подавал FLAC с mime `audio/webm` — теперь это законно отклоняется ещё до декодирования (`EBML header parsing failed`), поэтому тест не мог стать XPASS. Новый тест подаёт настоящий webm/opus на 120 и 600 с: на выходе ≤ 21 с, время < 5 с. Маркер снят.

**`test_harden_script_installs_restricted_key`.** Ослабление принято: регулярка `\\?"` допускает и `\"` внутри `echo "…"`, и `"` внутри `printf '…'` — проверяет то, что окажется в файле, а не способ записи.

**`test_login_rate_limited_per_ip`** переписан. Он имитировал «другой IP» заголовком `X-Real-IP` от `testclient`, а это закрепляло поведение из SEC-17. Теперь адрес задаётся как `request.client` (`TestClient(client=(ip, port))`), так что тест не зависит от того, как будет закрыт SEC-17.

### SEC-15 · harden-скрипт в песочнице

Одноразовый контейнер: настоящие `adduser`, `gpasswd`, `visudo`, `sshd` (OpenSSH, `UsePAM yes` — как в Ubuntu); `remote-deploy.sh` заменён заглушкой, которая пишет `id` и аргументы.

| Случай | Результат |
|---|---|
| `gh_actions.pub` пустой / без типа ключа / две строки | отказ до любых изменений, у root 2 ключа из 2 |
| Ключ Actions — единственный у root | у root ничего не удалено, предупреждение |
| Обычный случай (ключ владельца + ключ Actions, deploy в группе docker) | у root остался только ключ владельца, есть резервная копия; deploy выведен из docker; sudoers `… gorky-deploy ""` прошёл `visudo`; `/opt/gorky_code` — `root:root`, `.env` — `0600` |
| Повторный запуск | идемпотентен |
| `ssh deploy@` без команды / с `id; cat /etc/shadow` / с `-tt bash` | каждый раз выполняется только деплой от root, `args=[]`, `SSH_ORIGINAL_COMMAND` пуст; pty — «PTY allocation request failed» |
| `ssh -N -L` (проброс порта) | sshd: «refused local port forward» |
| `sudo -n gorky-deploy evil`, `sudo -n id` под deploy | «a password is required» — запрещено |

Две оговорки для сервера:
- При `UsePAM no` в `sshd_config` OpenSSH не пускает заблокированную учётку (`deploy:!` после `adduser --disabled-password`) даже по ключу — деплой упадёт, но безопасно. Лечится `usermod -p '*' deploy`.
- `AllowUsers`/`AllowGroups` в `sshd_config` должны включать `deploy`.

### SEC-17 · адрес клиента подделывается за Caddy — средняя

`client_ip()` доверяет `X-Real-IP`, если `request.client` «внутренний». Но `request.client` — не TCP-адрес, а результат uvicorn `--proxy-headers --forwarded-allow-ips=*`: при `*` uvicorn берёт **самый левый** `X-Forwarded-For`. Проверил на локальной цепочке: внешний адрес `198.51.100.10` на `lo`, nginx 1.24 с `nginx-voice.conf`, Caddy 2.10.2 (как в контейнере и как на хосте по `Caddyfile.snippet`), uvicorn 0.54. Атакующий шлёт `X-Real-IP: 6.6.6.6` и `X-Forwarded-For: 7.7.7.7`:

| Перед стеком | Сейчас (`client_ip()`) | Предложение |
|---|---|---|
| nginx (`nginx-voice.conf`) | 198.51.100.10 ✅ — nginx перезаписывает `X-Real-IP` | 198.51.100.10 ✅ |
| Caddy на хосте (`Caddyfile.snippet`) | **6.6.6.6** — Caddy пропускает `X-Real-IP` клиента | 198.51.100.10 ✅ |
| Только Caddy стека (`docker-compose.prod.yml`) | 198.51.100.10 — но лишь потому, что адрес публичный; клиент из 172.0.0.0/8 (проверка по префиксу `"172."` захватывает и публичные 172.x) подделывает | 198.51.100.10 ✅ |

**Последствие:** обход лимита входа (10/мин на IP) → сколько угодно свежих cookie. Лимит чата на cookie обходится, остаётся только общий лимит 3000/ч, и без nginx его можно выбрать — чат закрыт для всех до конца часа. Данные не затрагиваются.

**Чего не делать:** просто добавить в Caddy `trusted_proxies`. Проверил: тогда Caddy пропускает присланный клиентом `X-Forwarded-For`, и при `*` в uvicorn подделывается уже `request.client` (`7.7.7.7`) — в том числе за nginx.

**Исправление (проверено на той же цепочке, все три варианта дают настоящий адрес):**
1. `backend/Dockerfile` (чат 4): `--forwarded-allow-ips=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1` вместо `*`. uvicorn тогда берёт самый правый **недоверенный** адрес, а nginx и Caddy дописывают настоящий адрес справа.
2. `Caddyfile` (чат 0): в глобальном блоке `servers { trusted_proxies static private_ranges }`.
3. `client_ip()` (чат 0): вернуть `request.client.host`, `X-Real-IP` не читать.

Тесты: `test_uvicorn_trusts_only_private_proxies`, `test_caddy_trusts_private_proxies_for_client_ip`, `test_internal_peer_header_does_not_choose_rate_key`; регрессия — `test_nginx_overwrites_real_ip_in_every_location`, `test_public_client_cannot_reset_login_limit_with_header`.

**Что на стенде.** Если перед стеком nginx с нашим `voice.conf` — сейчас подделки нет. Проверить на сервере: `grep -rn "X-Real-IP" /etc/nginx/sites-enabled/voice*`, либо `systemctl is-active caddy`.

### SEC-18 · лимиты WebSocket обходятся сменой `session_id` — средняя

`ws_voice.py`: ключи `voice:{user_id}:{session_id}` (10 реплик в минуту) и `SocketRegistry` (2 сокета на сессию) берут `session_id` из query, то есть его выбирает клиент. Воспроизведение (локально, prod, с cookie): 15 сокетов с `session_id=rot0…rot14`, по одной реплике — 15 ответов, ни одного «Слишком часто»; в одной сессии 11-я реплика получает отказ (`test_ws_turn_limit_same_session`).

**Последствие:** STT ограничен только семафором (2 потока) и общим лимитом 3000/ч. Один клиент держит очередь распознавания занятой — остальные получают «Сервер занят». Плюс 20 сокетов на процесс: через nginx — 10 на IP, без nginx — все 20 у одного клиента.

**Исправление (чат 4):** ключ лимита реплик и ключ `SocketRegistry` — `sid` из cookie (`verify_cookie(...)[1]`); `session_id` из query — только для состояния диалога. Вне prod без cookie — как сейчас. Тест `test_ws_turn_limit_not_bypassed_by_rotating_session_id`.

### SEC-19 · параллельный деплой — низкая

`remote-deploy.sh` не защищён от одновременного запуска: повторный push во время сборки или ручной `ssh` дают два `docker compose up --build` подряд (двойная нагрузка на CPU общего сервера, гонка при пересоздании контейнеров). Исправление (чат 0): в начало — `exec 9>/run/gorky-deploy.lock; flock -n 9 || { echo "деплой уже идёт"; exit 0; }`. Тест `test_remote_deploy_is_serialized`.

### SEC-09 · `connect-src` — низкая

`connect-src 'self' wss:` разрешает WebSocket к **любому** хосту: при XSS это канал утечки. Исправление (чат 0): `connect-src 'self' wss://{host}` — Caddy подставит `{host}`. Голое `'self'` старые Safari не применяют к `wss:`, поэтому хост лучше указать явно. Тест `test_csp_connect_src_same_origin_only`.

## v2 · Проверка исправлений (`main@a2fa8cc`)

### Что принято

| Исправление | Как проверено |
|---|---|
| `POST/GET/DELETE /api/session`, HMAC-SHA256, `compare_digest`, срок, только `DEMO_USERS` | тесты: 401 без cookie, подделка подписи, cookie от другого секрета, просрочка, выход, флаги `HttpOnly; Secure; SameSite=Strict` |
| prod не стартует без `SESSION_SECRET`, с паролем БД `app`/`postgres`/пустым, со слабыми ICS-токенами | тесты `test_prod_*` |
| `SessionStore` по `(user_id, session_id)`, LRU 10 000, TTL 24 ч | ревью + `test_session_state_is_not_shared_between_users` |
| 429 + `Retry-After` на `/api/chat`, `/api/undo`, `/api/tts` | `test_chat_rate_limited`, `test_tts_rate_limited` |
| ICS: управляющие символы, RRULE через белый список и `rrulestr`, перенос по 75 октетов | `test_ics_*` (тест на `\r` исправлен — см. ниже) |
| compose shared: `cpus`, память, `pids_limit`, `no-new-privileges`, `cap_drop` у api, ротация логов; override — `127.0.0.1` | `test_infra.py` |
| Caddy: CSP Report-Only с `frame-ancestors 'none'`, `X-Frame-Options`, `-Server`; nginx `limit_req`/`limit_conn` | `test_infra.py`, ревью |
| deploy.yml: `environment: production`, `fingerprint`, ssh-action по SHA; forced command; CI-джоб security | `test_deploy_job_protected`, `test_remote_deploy_ignores_arguments` |

**Тесты, исправленные по замечаниям чата 0:**
- `test_ics_escapes_carriage_return` — чат 0 прав, тест был неверным. Он искал подстроку `BEGIN:VEVENT`, а она безвредно стояла внутри экранированного `SUMMARY:a\nEND:VEVENT\nBEGIN:VEVENT…` (одна строка). Теперь тест разворачивает перенесённые строки и считает только строки, которые **начинаются** с `BEGIN:`/`END:VEVENT`, плюс проверяет точный `SUMMARY` и отсутствие голых `\r`. Добавлен `test_ics_lines_folded_to_75_octets`.
- `test_freeze_now_rejected_in_prod` — смысл сохранён, тест усилен: без входа — 401 (заголовок не обходит авторизацию); после входа с `X-Freeze-Now` — 400 и `detail` про `X-Freeze-Now`; контроль — тот же запрос без заголовка даёт 200. Значит, 400 вызывает именно заголовок, а не вход или лимит.

### Что осталось или появилось

**SEC-01 (WS) — высокая.** `/ws/voice` при `APP_ENV=prod` без cookie принимает `?user_id=2` и отвечает записями пользователя 2 (проверено локально). Пока чат 4 не подключит `verify_cookie` и проверку `Origin`, на стенде SEC-01 фактически открыт: через WebSocket читается и меняется всё то же, что раньше через REST.

**Переходный режим.** Фронт (`http.ts`) ещё шлёт `X-User-Id` и `?user_id=`. С `ALLOW_HEADER_AUTH=false` стенд после деплоя main перестанет работать (все запросы 401), с `true` — SEC-01 на стенде открыт и для REST. Жюри до перехода фронта на `/api/session` (чат 5) говорить «закрыто» нельзя.

**SEC-03 · обход лимита на сессию.** Ключ лимита — `session_id` из тела запроса: 40 запросов с разными `session_id` → 40 × 200. Работает только общий лимит 600/ч, а его один клиент выбирает примерно за 2 минуты (nginx пропускает 5 r/s с одного IP) — после этого чат на час закрыт для всех, включая жюри. Исправление: ключ — `sid` из cookie; `POST /api/session` тоже ограничить (иначе новые cookie без счёта); в nginx для `/api/chat` — отдельная зона примерно 30 r/m на IP; общий часовой лимит поднять или считать только вызовы LLM (это бюджет чата 1). Тест `test_chat_limit_not_bypassed_by_rotating_session_id`.

**SEC-14 · 500 на мусорной cookie — низкая.** `hmac.compare_digest(str, str)` бросает `TypeError`, если в строке не-ASCII: cookie `pomnyu_session=abc.éé` → 500. Утечки нет, но это шум в логах и лёгкий способ получать 500. Исправление: сравнивать байты (`mac.encode("utf-8", "surrogateescape")`) или ловить `TypeError`/`UnicodeError` → `None`. Тест `test_garbage_cookie_gives_401_not_500`.

**SEC-15 · `harden-deploy-user.sh` — высокая до запуска.** Строка

```bash
grep -vF "$(awk '{print $2}' "$key")" /root/.ssh/authorized_keys > …new || true; mv …new /root/.ssh/authorized_keys
```

при пустом шаблоне удаляет **все** строки (`grep -vF ""` совпадает с каждой). Проверил на копиях файлов:

| `gh_actions.pub` | Результат текущего скрипта |
|---|---|
| пустой файл (создали через `touch`) | у root **0 ключей** |
| вставлено тело ключа без `ssh-ed25519` | у root **0 ключей** |
| две строки | вторая попадает в `authorized_keys` deploy **без** `restrict,command=` → shell под deploy |
| файла нет | скрипт молча пропускает шаг и пишет «Готово»: ключ остаётся у root, а у deploy ключа нет — деплой ломается после смены `VPS_USER` |
| ключ Actions = личный ключ владельца | владелец теряет вход под root |

На общем сервере это потеря доступа к соседнему рабочему приложению. Проверенная замена блока с ключом теперь встроена чатом 0 в сам скрипт (v3). Она отказывается работать с пустым, многострочным и «не ключом», не трогает root, если ключ Actions у него единственный, делает резервную копию и сверяет число оставшихся ключей. Прогнал её на тех же 5 случаях: root ни разу не остался без ключа.

Мелочи в том же скрипте: sudoers пишется сразу в `/etc/sudoers.d` и только потом проверяется `visudo` — битый файл ломает `sudo` всем, поэтому писать во временный, проверять и только потом ставить. Добавить `""` после команды в sudoers, чтобы запретить аргументы. `/opt/gorky_code` и `.env` сделать `root:root`, `.env` — `0600`.

**SEC-16 · smoke.** `deploy/smoke.sh` шлёт `/api/chat` без cookie → в prod 401, `curl -f` падает, джоб деплоя красный (при `ALLOW_HEADER_AUTH=false`). Исправление: `curl -c jar -b jar -X POST $URL/api/session -d '{"user_id":1}'`, затем чат с `-b jar`.

### Порядок выката на стенд (иначе стенд ляжет)

1. **Пароль БД.** Prod не стартует с паролем `app`. Смена `POSTGRES_PASSWORD` в `.env` **не меняет** пароль в существующем томе `pgdata` (он задаётся только при первой инициализации). Сначала `docker compose … exec db psql -U app -c "ALTER USER app PASSWORD '<новый>'"`, затем тот же пароль в `.env`. Пароль — `openssl rand -hex 24` (без `@:/`, он подставляется в URL).
2. `.env`: `SESSION_SECRET=$(openssl rand -hex 32)`; `ALLOW_HEADER_AUTH=true` — до выхода web/v4; `chmod 600 .env`.
3. Деплой main (вручную, ещё под root): `make shared-up`, затем `curl $H/health`.
4. Ключ Actions: **отдельная** пара `ssh-keygen -t ed25519 -f gha -N ''`; `gha.pub` → `/root/.ssh/gh_actions.pub`, приватный → секрет `VPS_SSH_KEY` в Environment `production`. Секреты `VPS_*` удалить из уровня репозитория, в Environment включить required reviewer и разрешить только ветку `main`.
5. Исправленный `harden-deploy-user.sh` — **с открытой второй root-сессией**; в новом окне проверить вход под root, только потом закрывать старую.
6. `VPS_USER=deploy`, `VPS_FINGERPRINT` → запуск Deploy вручную. Если упадёт на fingerprint: Go-клиент мог договориться о другом типе ключа хоста, тогда взять отпечаток ECDSA (`ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub`).
7. После web/v4 (чат 5) и WS (чат 4): `ALLOW_HEADER_AUTH=false`, перезапуск api, проверка из раздела «Проверить на стенде».

---

## v1 · Исходные находки (22.10)

## Методика и границы

- Код: ревью `api/`, `ws_voice.py`, `container.py`, `config.py`, `storage/`, `nlu/llm/`, `dialog/`, compose, Dockerfile, Caddy/nginx, CI.
- Локальный стенд: настоящие NLU + диалог + PostgreSQL 16 / pgvector, `APP_ENV=prod`, данные CSV. Все «атакующие» проверки (бомба, перебор, инъекции) — только здесь.
- Инструменты: gitleaks 8.28.0 по всем 14 веткам (21 коммит) и рабочему дереву + ручной поиск ключей Яндекса/GigaChat/приватных ключей; pip-audit (основные зависимости); npm audit; ffmpeg 6.1.
- **Стенд я не трогал:** из моей среды он недоступен. Проверки по одному неразрушающему запросу — в разделе [«Проверить на стенде»](#проверить-на-стенде); их нужно выполнить владельцу сервера.
- Не проверено: образы Docker (Trivy), `torch` и зависимости GigaAM (pip-audit по ним не запускался), архивы обмена кодом на рабочем столе.

---

## SEC-01 · Пользователя назначает клиент · Высокая

**Где.** `api/deps.py:15` — `get_user_id(x_user_id: Header = 1)`; `api/ws_voice.py:247-259` — `?user_id=` или `X-User-Id`; `frontend/src/api/http.ts:15,64`.

**Воспроизвести (локально, `APP_ENV=prod`):**

```bash
# создать запись от имени пользователя 2
curl -X POST localhost:8080/api/chat -H 'X-User-Id: 2' -H 'Content-Type: application/json' \
  -d '{"text":"Напомни завтра в 10 секретный звонок врачу","session_id":"a"}'
# прочитать её «чужим» клиентом
curl 'localhost:8080/api/items?from=2026-10-22T00:00:00%2B03:00' -H 'X-User-Id: 2'
# без заголовка — пользователь 1 (все 1000 записей CSV)
curl localhost:8080/api/items
# WebSocket: ws://localhost:8080/ws/voice?user_id=2 — ответ с записями пользователя 2
```

Результат: 200, запись «Секретный звонок врачу» видна; WS отвечает «Ближайшая — «Секретный звонок врачу»». Кроме того, `X-User-Id: 99999999999` даёт 500 (переполнение int4), отрицательные id принимаются.

**Что уже хорошо (проверено тестами).** Каждый запрос к БД фильтруется по `user_id`; чужой `session_id` не продолжает чужое уточнение (`test_session_state_is_not_shared_between_users`). Проблема только в том, откуда берётся `user_id`.

**Исправление — минимальное безопасное для прототипа:**

1. `POST /api/session {"user_id": 1|2|3}` — демо-вход. Сервер выдаёт cookie `pomnyu_session` = `base64url(json{uid, sid, exp}).HMAC-SHA256(SESSION_SECRET)`; флаги `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`. `GET /api/session` — кто я; `DELETE` — выход. Разрешены только id из `DEMO_USERS` (по умолчанию `1,2,3`).
2. `get_user_id`: при `APP_ENV=prod` — только из проверенной cookie, иначе 401; `X-User-Id` работает лишь вне prod (тесты, eval по HTTP).
3. Старт в prod без `SESSION_SECRET` (≥ 32 байт) — ошибка.
4. WebSocket: cookie приходит с рукопожатием сама; проверить подпись и `Origin` ∈ {свой хост, `CORS_ORIGINS`}, иначе закрыть до `accept` (SEC-10). Параметр `user_id` игнорировать в prod.
5. `SessionStore`: ключ — `(user_id, session_id)`, чтобы чужой пользователь не сбрасывал чужое состояние.
6. Фронт: экран входа вызывает `/api/session`, заголовок `X-User-Id` и `?user_id=` убрать, на 401 — экран входа.

Почему не JWT-библиотека: подпись HMAC из stdlib — 30 строк, ноль зависимостей, тот же уровень защиты для одного сервиса. ICS остаётся на токенах в URL — календари не умеют cookie.

**Честный ответ жюри.** «Настоящего входа в прототипе нет: экран входа — выбор демо-пользователя. Но личность выдаёт сервер: подписанная HttpOnly-cookie, клиент её не подделает, каждый запрос к БД фильтруется по пользователю из токена, LLM идентификаторов не видит. Для продукта меняем одну функцию — выдачу токена — на вход через Яндекс ID / Telegram / SMS».

**Остаточный риск.** Демо-пользователи публичны по замыслу: кто угодно может войти как пользователь 1 и удалить демо-данные во время защиты. Защита: команда `reset-demo` (чат 3) и, по желанию, «гостевая песочница» — новый пользователь с копией CSV на каждый вход.

---

## SEC-02 · Аудио-«бомба» · Высокая

**Где.** `voice/audio.py:decode_to_pcm16k` — `ffmpeg -i файл … pipe:1` без `-t` и без `-f`; ограничение длительности (`max_utterance_s=20`) применяется в `stt.py` уже **после** полного декодирования. Лимит WS — 2 МБ сжатых данных, а не секунд.

**Воспроизвести (локально):**

```bash
ffmpeg -f lavfi -i anullsrc=r=8000:cl=mono -t 9800 -c:a flac s2.flac   # 1,8 МБ
# отправить в /ws/voice с {"type":"start","mime":"audio/webm;codecs=opus"} → {"type":"end"}
```

| Вход | PCM после ffmpeg | Время | Пик памяти процесса |
|---|---|---|---|
| 1,8 МБ FLAC тишины, объявлен как webm | **313 МБ** (2 ч 43 мин) | 8,2 с CPU | 670 МБ |
| 2,1 МБ Opus 6 кбит/с | 115 МБ (1 ч) | 9,4 с | 275 МБ |
| тот же FLAC с `-t 21` | 0,67 МБ | мгновенно | — |

`mime` клиента игнорируется: ffmpeg определяет формат по содержимому. При `API_MEM_LIMIT=2500m` и моделях в памяти (≈2 ГБ) хватает одного-двух параллельных запросов, чтобы API убило OOM; всё это время CPU общего сервера занят. Промежуточные транскрипты каждые 0,8 с заново декодируют весь буфер.

Попутно: автоопределение формата открывает демультиплексоры вроде HLS/concat. ffmpeg 6.1 блокирует m3u8 с чужим расширением (проверил), но версия ffmpeg в образе зависит от базы `python:3.12-slim` — проверить на стенде.

**Исправление (чат 4):**

- `-t {max_utterance_s + 1}` **перед** `-i` — ffmpeg перестаёт читать вход после лимита.
- Формат входа задавать явно по белому списку mime: `webm→-f webm`, `ogg→-f ogg`, `mp4→-f mp4`, `wav→-f wav`; прочие mime — отказ. Плюс `-protocol_whitelist file,pipe`.
- Глобальный `asyncio.Semaphore(2)` на «декодирование + распознавание» (настраивается `VOICE_MAX_CONCURRENCY`); при переполнении — ответ «Сервер занят, повторите».
- Промежуточные транскрипты — декодировать только хвост или не чаще раза в 2 с; на 2 vCPU выключены (`VOICE_PARTIALS=0`).

**Критерий «починено»:** `test_decode_caps_duration`, `test_decode_rejects_container_not_matching_mime` — XPASS; `test_decode_normal_utterance_still_works` — зелёный.

---

## SEC-03 · Нет лимитов частоты и параллелизма · Высокая

**Где.** Нигде нет: ни в FastAPI, ни в Caddy, ни в nginx (`deploy/shared/nginx-voice.conf`).

**Почему это деньги и стабильность:**
- `/api/chat`: каждая фраза, которую правила не распознали с уверенностью ≥ 0,85, уходит в LLM. Подобрать такие фразы легко — любой бессмысленный текст. При `LLM_PROVIDER=gigachat|yandex` это прямые расходы.
- `/api/tts`: до 2000 символов за вызов без привязки к пользователю. SpeechKit — платный, Silero — CPU общего сервера.
- WebSocket: число соединений не ограничено; каждое держит буфер до 2 МБ и может запускать STT.

**Исправление.** Один процесс uvicorn (`--workers 1`), поэтому достаточно лимитера в памяти:

| Что | Где | Лимит (стартовый) |
|---|---|---|
| `/api/chat`, `/api/undo` | чат 0, зависимость FastAPI | 20/мин на сессию, 600/час на весь сервис |
| `/api/tts` | чат 0 + чат 4 | 30/мин на сессию; `Semaphore(2)` на синтез |
| WebSocket | чат 4 | ≤ 2 соединения на сессию, ≤ 20 всего; простой > 60 с — закрыть |
| Вызовы LLM | чат 1, внутри `HybridNLU` | ≤ 60/мин и ≤ 2000/сутки на сервис; сверх — только правила |
| На входе по IP | чат 0, `nginx-voice.conf` | `limit_req` 5 r/s burst 20; `limit_conn` 10 на IP для `/ws/` |

Ответ при превышении — 429 с `Retry-After`; в WS — `{"type":"error","message":"Слишком часто, подождите немного"}`. Ключ лимита — пользователь и сессия из cookie (после SEC-01). По IP внутри API считать нельзя: за Caddy все клиенты приходят с одного адреса шлюза Docker, поэтому слой по IP — на внешнем nginx, где виден настоящий адрес. Директива `limit_req_zone` в файле сайта `voice.conf` работает только для этого сайта и Планиметрику не затрагивает.

**Критерий:** `test_chat_rate_limited`, `test_tts_rate_limited` — XPASS.

---

## SEC-04 · Ключ деплоя с правами root · Высокая

**Где.** `.github/workflows/deploy.yml`: `appleboy/ssh-action@v1.2.0` с `VPS_USER` (по словам команды — `root`) и `VPS_SSH_KEY`.

**Риск.**
- Секрет доступен любой ветке репозитория. Любой участник с правом записи может запушить ветку с workflow `on: push`, который использует `secrets.VPS_SSH_KEY`, — и получить root на сервере, где лежат данные и ключи **Планиметрики**.
- Утечка ключа (скомпрометированный action, лог, ноутбук участника) = root на чужом приложении.
- Нет проверки ключа хоста (`fingerprint`) — возможна подмена сервера; action закреплён тегом, а не SHA.
- Даже существующий `deploy`-пользователь из `setup-vps.sh` состоит в группе `docker`, а это эквивалент root.

**Исправление (по шагам, от простого к надёжному):**

1. **Сразу:** создать новый ключ ed25519 только для деплоя, старый удалить из `/root/.ssh/authorized_keys`; проверить, нет ли там других лишних ключей.
2. **Forced command.** Пользователь `deploy` **без** группы `docker`. В его `authorized_keys`:
   ```
   restrict,command="sudo -n /usr/local/sbin/gorky-deploy" ssh-ed25519 AAAA… gha-deploy
   ```
   В `/etc/sudoers.d/gorky-deploy`: `deploy ALL=(root) NOPASSWD: /usr/local/sbin/gorky-deploy` (без аргументов). Скрипт `gorky-deploy` (владелец root, 0755) делает только одно: `git -C /opt/gorky_code fetch origin main && git reset --hard origin/main && docker compose -f docker-compose.yml -f docker-compose.shared.yml up -d --build && docker image prune -f`. Аргументы и `SSH_ORIGINAL_COMMAND` игнорирует. Итог: ключ не даёт shell, не даёт проброс портов, может только «обновить стенд».
3. **GitHub:** секреты `VPS_*` перенести в Environment `production` с обязательным подтверждением владельцем; деплой-джоб — `environment: production`. Защитить `main`: только через PR с одобрением. Тогда ветка-однодневка не прочитает ключ.
4. В action добавить `fingerprint: ${{ secrets.VPS_FINGERPRINT }}` (`ssh-keyscan host | ssh-keygen -lf -`), закрепить action по SHA.

**Остаточный риск.** Кто контролирует `main`, тот контролирует `docker-compose.yml`, а через него — volume `/` и root. Это закрывает только защита ветки (п. 3). Ещё надёжнее — pull-модель: systemd-таймер на сервере сам проверяет `origin/main` и деплоит; входящего ключа нет вовсе.

**Критерий:** `test_deploy_job_protected`, `test_third_party_actions_pinned_by_sha` — XPASS; на сервере `ssh -i key deploy@host id` выполняет деплой, а не `id`.

---

## SEC-05 · Контейнеры · Средняя

- `backend/Dockerfile` (чат 4): нет `USER` — uvicorn, ffmpeg и модели работают от root. Уязвимость в ffmpeg или torch = root внутри контейнера.
- `docker-compose.shared.yml` (чат 0): ограничена только память `api` и `db`; нет `cpus` (STT займёт все ядра общего сервера), нет `pids_limit`, у `web` и `caddy` нет лимита памяти.
- Логи Docker: драйвер `json-file` по умолчанию без ротации; при логе на каждую реплику диск общего сервера постепенно заполняется.

**Исправление:** в Dockerfile `useradd -r -u 10001 app`, `chown` каталогов `/app/.tts-cache`, `/models`, `USER app`. В shared-compose для всех сервисов: `cpus` (api — 1.5, остальные 0.5), `pids_limit: 256`, память `web`/`caddy` 128m, `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]` для api и web, `logging: {driver: json-file, options: {max-size: 10m, max-file: "3"}}`.

**Критерий:** `test_api_image_runs_as_non_root`, `test_shared_mode_limits_cpu_and_pids` — XPASS; на стенде `docker compose exec api id -u` ≠ 0.

---

## SEC-06 · Postgres наружу · Средняя

**Где.** `docker-compose.override.yml` публикует `5432:5432` (на всех интерфейсах). Он подхватывается автоматически при любом `docker compose up` без `-f`, в том числе `make up`. Docker пишет свои правила iptables раньше ufw, поэтому файрвол из `setup-vps.sh` порт **не закроет**. Пароль по умолчанию — `app`.

Режимы `prod` и `shared` порт не публикуют (проверено тестами), но достаточно одного ручного `make up` на сервере.

**Исправление:** в override — `127.0.0.1:5432:5432`; при `APP_ENV=prod` и `STORAGE_IMPL=postgres` отказаться стартовать, если пароль в `DATABASE_URL` — `app` или пустой.

**Критерий:** `test_override_binds_db_to_localhost`, `test_prod_refuses_default_db_password` — XPASS; снаружи `nc -vz voice.planimetrika.online 5432` — отказ.

---

## SEC-07 · ICS-токены · Низкая

Токен — строка из `ICS_TOKENS`, сейчас единственный `demo → 1`, опубликован в README. Для демо это нормально: данные пользователя 1 — открытый CSV организаторов. Угадать другие токены нельзя: их нет, неизвестный токен → 404 (`test_ics_unknown_token_404`).

**На будущее (чат 3 + чат 0):** токен на пользователя — `secrets.token_urlsafe(16)` (128 бит) в `users.ics_token`, ротация по запросу; в prod проверять, что все токены, кроме `demo`, не короче 22 символов.

---

## SEC-08 · ICS: экранирование · Низкая

`api/system.py:_ics_escape` экранирует `\n`, `;`, `,`, но не `\r` и управляющие символы; `RRULE` пишется как есть; длинные строки не сворачиваются (RFC 5545, 75 октетов).

Сейчас **не эксплуатируется**: NLU схлопывает пробельные символы в заголовке, а RRULE строит код из текста. Проверил: `"позвонить\rEND:VEVENT\rBEGIN:VEVENT"` превращается в заголовок с пробелами, лишнего события в фиде нет. Но заголовок может прийти и другим путём (импорт, будущий API), поэтому это защита в глубину.

**Исправление (чат 0):** вырезать `\r` и символы `< 0x20` кроме `\n`; проверять RRULE через `dateutil.rrule.rrulestr` и регулярку `^[A-Z0-9=;,:+-]+$`; сворачивать строки.

**Критерий:** `test_ics_escapes_carriage_return`, `test_ics_rejects_malformed_rrule` — XPASS.

---

## SEC-09 · Заголовки · Низкая

Caddy ставит HSTS, `nosniff`, `Referrer-Policy`, `Permissions-Policy: microphone=(self)` — хорошо. Нет `Content-Security-Policy` (с `frame-ancestors`), заголовок `Server: Caddy` не скрыт.

**Исправление (чат 0, совместимость — чат 5):** сначала `Content-Security-Policy-Report-Only`, после проверки фронта — боевой:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;
media-src 'self' blob:; connect-src 'self' wss://voice.planimetrika.online; worker-src 'self';
manifest-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'
```

и `-Server` в блоке `header`. `/docs` в prod — решение команды: для жюри Swagger полезен, утечки он не создаёт.

---

## SEC-10 · WebSocket без проверки Origin · Низкая → Средняя

Сейчас браузер не отправляет никаких учётных данных, поэтому подделка соединения с чужого сайта (CSWSH) ничего не даёт сверх SEC-01. После перехода на cookie браузер будет прикладывать её к рукопожатию с любого сайта — проверка `Origin` становится обязательной. Входит в исправление SEC-01 (тест `test_ws_rejects_foreign_origin`).

## SEC-11 · Состояние сессий в памяти · Низкая

`container.SessionStore` — словарь без предела и TTL: каждый новый `session_id` остаётся в памяти навсегда. `session_id` в REST ограничен 100 символами, в WS — не ограничен. **Исправление:** LRU на 10 000 сессий с TTL 24 ч (чат 0); в WS — `^[A-Za-z0-9_-]{1,100}$`, иначе закрыть (чат 4).

## SEC-12 · Цепочка поставки · Низкая

`appleboy/ssh-action` по тегу; образы `caddy:2-alpine`, `pgvector:pg16`, `nginx:1.27-alpine` (ветка 1.27 уже не получает исправлений) — плавающие теги; Python-зависимости `>=` без lock-файла. **Исправление (чат 0):** SHA для сторонних actions, актуальная stable-ветка nginx, Dependabot; джоб `security` в CI: `gitleaks git`, `pip-audit`, `npm audit --omit=dev`, `trivy image` по собранным образам.

## SEC-13 · Персональные данные в логах · Инфо

`api/chat.py:41` пишет в лог полный текст реплики и id пользователя. Для прототипа это осознанный инструмент отладки («не понял» → `grep turn`). Для продукта — маскировать или хранить не дольше 7 дней; ротация — в SEC-05.

## Попутно: нет пользователей 2 и 3 в PostgreSQL (функциональная)

README обещает пустых пользователей 2 и 3, но миграция и `import-csv --user 1` создают только пользователя 1. На чистой базе `X-User-Id: 2` + «Напомни…» → `ForeignKeyViolation`, ответ «Что-то пошло не так». **Чат 3:** в entrypoint или `import-csv` создавать всех из `DEMO_USERS` (`ensure_user`). Проверить на стенде: войти как пользователь 2 и создать напоминание.

---

## Проверено — уязвимостей нет

| Что | Как проверял | Итог |
|---|---|---|
| SQL-инъекции | Все запросы — ORM с параметрами; единственный `text()` — статичный `setval`. Пейлоады `' OR 1=1 --`, `'); DROP TABLE items; --`, `pg_sleep`, синтаксис tsquery `a:* & !b \| (c` через `/api/chat` на настоящем PostgreSQL | Ответ 200, база цела, ошибок tsquery нет (`test_search_payloads_are_data`) |
| Prompt injection → удаление чужих или всех записей | В схеме функций LLM нет `user_id` и `item_id`; лишние поля отбрасываются pydantic; `user_id` берётся из состояния сервера; удаление — одна запись и только после «да» | LLM, которую «уговорили», ничего не может (`test_injected_ids_are_dropped`, `test_delete_all_is_not_possible_by_phrase`) |
| XSS через заголовок | Заголовок `<img src=x onerror=alert(1)>` сохраняется как данные; фронт выводит текстом (React), `dangerouslySetInnerHTML`/`innerHTML`/`eval` в `frontend/src` нет; API отдаёт JSON с `nosniff` | Не эксплуатируется (`test_frontend_has_no_raw_html_sinks`) |
| `X-Freeze-Now` в prod | Локально при `APP_ENV=prod` → 400; `docker-compose.shared.yml` и `prod.yml` ставят `APP_ENV=prod`, `environment:` важнее `.env` | Закрыто (`test_freeze_now_rejected_in_prod`); подтвердить одним запросом на стенде |
| Секреты в git | gitleaks по всем 14 веткам и дереву; ручной поиск ключей Яндекса (`AQVN…`, `t1.…`), Basic-ключа GigaChat, приватных ключей; `.env`/`.pem` в истории | Ничего не найдено; `.env` в `.gitignore` и `.dockerignore` |
| Зависимости | pip-audit (FastAPI, SQLAlchemy, psycopg, httpx и др.), npm audit (prod и dev) | 0 известных уязвимостей на 22.10.2026 |
| Порты в режимах prod/shared | compose: Postgres и API не публикуются, Caddy в shared — только `127.0.0.1:8080` | Закрыто (`test_shared_mode_listens_on_localhost_only`) |
| CORS | Список origin из env, без `allow_credentials` | Корректно; после SEC-01 cookie — same-origin, CORS не расширять |

---

## Проверить на стенде

Каждая строка — один неразрушающий запрос. Ожидаемый результат — справа.

С любого компьютера:

```bash
H=https://voice.planimetrika.online
# 1. X-Freeze-Now запрещён в prod → 400
curl -s -o /dev/null -w '%{http_code}\n' -X POST $H/api/chat -H 'Content-Type: application/json' \
  -H 'X-Freeze-Now: 2030-01-01T10:00:00+03:00' -d '{"text":"привет","session_id":"sec-check"}'
# 2. Заголовки ответа (HSTS, nosniff, CSP, Server)
curl -sI $H/ | grep -iE 'strict-transport|content-security|x-frame|x-content-type|permissions|server'
# 3. Подмена пользователя (только чтение) → сейчас 200 и записи пользователя 2; после SEC-01 → 401
curl -s -o /dev/null -w '%{http_code}\n' $H/api/items -H 'X-User-Id: 2'
# 4. Конфигурация провайдеров (LLM/STT/TTS)
curl -s $H/health
# 5. Порты снаружи — по одному соединению: ожидается refused/timeout
nc -vz -w 3 voice.planimetrika.online 5432; nc -vz -w 3 voice.planimetrika.online 8080; nc -vz -w 3 voice.planimetrika.online 8000
```

На сервере (только чтение, секреты не печатаются):

```bash
cd /opt/gorky_code
docker ps --format '{{.Names}}\t{{.Ports}}'                    # нет 0.0.0.0:5432
sudo ss -ltnp | grep -E ':(5432|8080|8000)\b'                   # только 127.0.0.1
C="docker compose -f docker-compose.yml -f docker-compose.shared.yml"
$C exec api id -u                                               # сейчас 0 (SEC-05)
$C exec api ffmpeg -version | head -1                            # версия ffmpeg (SEC-02)
$C exec api printenv APP_ENV                                     # prod
grep -q '^POSTGRES_PASSWORD=app$' .env && echo "ПАРОЛЬ ПО УМОЛЧАНИЮ" || echo "пароль сменён"
sudo wc -l /root/.ssh/authorized_keys                            # сколько ключей у root (SEC-04)
docker inspect -f '{{.Name}} mem={{.HostConfig.Memory}} cpu={{.HostConfig.NanoCpus}} log={{.HostConfig.LogConfig.Config}}' $($C ps -q)
```

Результаты пришлите в чат 8 — обновлю отчёт.
