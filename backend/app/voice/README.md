# Голосовой контур (`backend/app/voice/`)

Браузер → `/ws/voice` → ffmpeg (16 кГц моно) → **STT** → `Orchestrator.handle()` → **TTS** → ogg/opus обратно в браузер.

| Файл | Что внутри |
|---|---|
| `__init__.py` | фабрики `build_stt(settings)`, `build_tts(settings)` для `app/container.py`; `startup`, `provider_status` |
| `config.py` | переменные голосового контура (из окружения и `.env`); провайдер берётся из `app.config.Settings` |
| `audio.py` | декодирование webm/ogg/mp4/wav → PCM 16 кГц, кодирование PCM → ogg/opus |
| `stt.py` | `GigaAMSTT`, `SpeechKitSTT`, `WhisperSTT`, `ConstSTT` (для тестов) |
| `tts.py` | `SileroTTS`, `SpeechKitTTS`, `SilenceTTS` (тесты), `NullTTS` (browser), `CachedTTS` |
| `text_norm.py` | числа, даты, время, дни недели и аббревиатуры словами для Silero; нормализация для WER |
| `factory.py` | реализация фабрик и хуков |
| `preload_models.py` | скачивание весов при сборке образа |
| `requirements-voice.txt`, `requirements-bench.txt` | зависимости голоса (ставит `backend/Dockerfile`, в CI не нужны) |
| `bench/` | `recorder.html`, `phrases.jsonl` (40 фраз), `run_bench.py`, `make_synthetic.py` |

Вне папки: `app/api/ws_voice.py` (протокол WebSocket), `backend/Dockerfile`, корневой `THIRD_PARTY.md`.

## Как подключено

- `app/container.py` при `STT_PROVIDER≠fake` / `TTS_PROVIDER≠fake` вызывает `app.voice.build_stt(settings)` / `build_tts(settings)`. При `fake` работают заглушки `app/fakes/voice.py`, поэтому CI не нужны ни модели, ни torch.
- lifespan в `app/main.py` → `app.voice.startup(c.stt, c.tts)`: загрузка GigaAM и Silero, прогрев 10 частых фраз. Пока модели грузятся, сервер не принимает запросы, поэтому в `HEALTHCHECK` стоит `start-period=60s`.
- `/health` → `stt`, `tts`: `ok` или `down` (модель не загрузилась, нет ключа SpeechKit).
- `/api/tts` (в `app/api/chat.py`) берёт `c.tts`, кэш работает и там.

## Протокол `/ws/voice`

```
→ (необязательно) {"type":"start","mime":"audio/mp4"}     любой контейнер; по умолчанию audio/webm;codecs=opus
→ бинарные чанки записи
← {"type":"transcript","text":"Напомни мне завтра","final":false}   раз в ~2 с — только локальный STT
                                                                      и только если чанки идут по ходу записи
→ {"type":"end"}
← {"type":"transcript","text":"Напомни мне завтра в 10 утра позвонить Ивану.","final":true}
← {"type":"reply", ...AssistantReply, "audio_mime":"audio/ogg",
   "debug":{..., "voice":{"stt_provider","audio_s","decode_ms","stt_ms"}}}
← бинарный кадр с озвучкой (audio_mime); при TTS_PROVIDER=browser кадра нет и audio_mime = null
```

- Одно соединение обслуживает сколько угодно реплик. `{"type":"cancel"}` выбрасывает текущую запись.
- Ошибки: пустая или слишком короткая запись, битое аудио, сбой STT или диалога → `{"type":"error","message":…}`, затем всё равно `reply` с тем же текстом (`action: "none"`) и озвучкой. Так голосовой пользователь слышит, что случилось. Сокет не закрывается.
- Наш TTS заранее знает mime (`audio/ogg`), поэтому `reply` уходит сразу, а звук догоняет его. Для заглушки `FakeTTS` сначала синтез, потом `reply`, как было у чата 0.
- Запись длиннее 2 МБ финализируется автоматически; звук длиннее 20 с обрезается при декодировании.
- Пользователь (SEC-01): подписанная cookie `pomnyu_session` из `POST /api/session`. В `APP_ENV=prod` без неё сокет закрывается с кодом 4401. Исключение — переходный `ALLOW_HEADER_AUTH=true`: тогда, как и вне prod, принимаются `?user_id=` и `X-User-Id`, но в prod только для демо-пользователей. Контекст диалога общий с `/api/chat` через `run_turn` и `c.sessions`.

### Защита `/ws/voice` (docs/security/REPORT.md)

| Что | Как | Код закрытия / ответ |
|---|---|---|
| Чужой сайт (SEC-10, CSWSH) | `Origin` должен совпадать с хостом запроса или входить в `CORS_ORIGINS`. Сравнивается хост, а не схема: за nginx → Caddy схема приходит как `http`. Без `Origin` (не браузер) — пропускаем | 1008 до `accept` |
| `session_id` (SEC-11) | `^[A-Za-z0-9_-]{1,100}$` | 1008 до `accept` |
| Нет сессии (SEC-01) | см. выше | 4401 до `accept` |
| Сокетов много (SEC-03) | ≤ `VOICE_MAX_SOCKETS` (20) на процесс и ≤ `VOICE_MAX_SOCKETS_PER_SESSION` (2) на сессию | `error` и затем 1013 |
| Сокет молчит | `VOICE_IDLE_TIMEOUT_S` (60 с) без сообщений | 4408 |
| Частые реплики | ≤ `VOICE_MAX_TURNS_PER_MIN` (10) на сессию плюс общий бюджет `/api/chat` в час | `error` «Слишком часто, подождите немного.» и `reply` |
| Очередь к модели | decode+STT ≤ `VOICE_MAX_CONCURRENCY` (2) одновременно, синтез ≤ `VOICE_MAX_TTS_CONCURRENCY` (2); дольше `VOICE_QUEUE_TIMEOUT_S` (5 с) в очереди → отказ. Промежуточный транскрипт слот не ждёт — пропускается | `error` «Сервер занят…»; `/api/tts` — 503 |
| Аудио-«бомба» (SEC-02) | контейнер — строго по mime из белого списка (`-f webm/ogg/mp4/mp3/wav`), длительность режется на входе ffmpeg (`-t VOICE_MAX_UTTERANCE_S`) и stdout читается не больше лимита, только локальный файл (`-protocol_whitelist file,pipe`); промежуточный транскрипт — не чаще раза в 2 с и по последним 6 с | `error` «Не получилось распознать…» |

Замер SEC-02 после исправления:
- 1,8 МБ FLAC-тишины на 2,7 часа с mime `audio/webm` отклоняются за 50 мс. Раньше — 313 МБ PCM, 8 с CPU, 670 МБ RSS.
- Настоящий webm/opus на 45 минут (1,6 МБ) декодируется в 20 с PCM (`VOICE_MAX_UTTERANCE_S`) за 140 мс; stdout ffmpeg читается не больше `20 × 16000 × 2` байт, лишнее — процесс убивается.
- Промежуточные транскрипты сейчас фронт не получает: он шлёт запись одним куском после отпускания кнопки. Чтобы они появились, чату 5 нужен `MediaRecorder.start(250)` и отправка каждого `dataavailable` в сокет.

## Переменные окружения

| Переменная | По умолчанию | Значения |
|---|---|---|
| `STT_PROVIDER` | `fake` (в `.env.example`) | `gigaam` · `speechkit` · `whisper` · `fake` (заглушка каркаса) |
| `GIGAAM_MODEL` | `v3_e2e_rnnt` | `v3_e2e_rnnt` (пунктуация и цифры) · `v3_e2e_ctc` · `v3_rnnt` · `v3_ctc` |
| `TTS_PROVIDER` | `fake` (в `.env.example`) | `silero` · `speechkit` · `browser` (озвучивает браузер) · `fake` |
| `SILERO_MODEL`, `SILERO_SPEAKER` | `v5_6_ru`, `xenia` | голоса: aidar, baya, kseniya, xenia, eugene |
| `YANDEX_API_KEY` или `YANDEX_IAM_TOKEN`, `YANDEX_FOLDER_ID` | — | для SpeechKit |
| `SPEECHKIT_VOICE`, `SPEECHKIT_SPEED` | `alena`, `1.1` | |
| `VOICE_PARTIALS` | `auto` | `auto` — промежуточные транскрипты только у локального STT (SpeechKit берёт деньги за каждый запрос) · `on` · `off` |
| `VOICE_TORCH_THREADS` | `4` | потоков torch на модель |
| `TTS_CACHE_DIR` | — | папка для кэша озвучки на диске |
| `VOICE_MAX_UTTERANCE_S` | `20` | дольше — обрезается при декодировании |
| `VOICE_MAX_CONCURRENCY`, `VOICE_MAX_TTS_CONCURRENCY`, `VOICE_QUEUE_TIMEOUT_S` | `2`, `2`, `5` | очередь к моделям |
| `VOICE_MAX_SOCKETS`, `VOICE_MAX_SOCKETS_PER_SESSION`, `VOICE_IDLE_TIMEOUT_S`, `VOICE_MAX_TURNS_PER_MIN` | `20`, `2`, `60`, `10` | лимиты `/ws/voice` |
| `TTS_PREWARM` | `1` | прогревать частые фразы на старте |

Для фронта и CI без моделей: `STT_PROVIDER=fake TTS_PROVIDER=fake`. Ключи SpeechKit можно держать в `.env` — голос читает его так же, как `app.config`.

## Бенчмарк

1. Записать фразы: `cd backend/app/voice/bench && python -m http.server 8765`, открыть `http://localhost:8765/recorder.html`. Каждый участник вводит своё имя латиницей и читает 40 фраз (около 5 минут), затем жмёт «Скачать zip». Желательно записать второй набор с фоновым шумом под именем `<имя>-noise`.
2. Распаковать архивы в `backend/recordings/`.
3. Прогнать:

```bash
cd backend
docker build --build-arg WITH_WHISPER=1 -t assistant-api:bench .
docker run --rm -v "$PWD/recordings:/app/recordings" -e YANDEX_API_KEY assistant-api:bench \
  python -m app.voice.bench.run_bench --audio-dir recordings \
  --providers gigaam,gigaam:v3_rnnt,whisper:small,speechkit --out recordings/bench_report.md
```

На выходе `bench_report.md` (таблица для README) и `bench_report.csv` (по каждой фразе: эталон, распознанное, тайминги). Каждый провайдер работает в отдельном процессе, поэтому пиковая RAM измеряется честно. Смоук без моделей: `--providers const`. Чтобы проверить пайплайн с моделями без записей, можно взять синтетику: `python -m app.voice.bench.make_synthetic --tts silero`. Её WER в README не ставим, он оптимистичен.

Метрики: WER и CER считаются после нормализации (регистр, пунктуация, ё, «15:30» → «пятнадцать тридцать», «29 октября» → «двадцать девятого октября»). «Ключевые слова» — доля имён, компаний и дней из фразы, распознанных хотя бы с правильным корнем. Для NLU это важнее общего WER. «Оценка для 6 с» = медианный RTF × 6 + время декодирования.

### Результаты

*Нет записей команды → таблица заполняется после шага 3.*

| Модель | WER | Ключевые слова | p50 | оценка для 6 с | RAM пик |
|---|---|---|---|---|---|
| `gigaam` (v3_e2e_rnnt) | — | — | — | — | — |
| `gigaam:v3_rnnt` | — | — | — | — | — |
| `whisper:small` (int8, beam 1) | — | — | — | — | — |
| `speechkit` | — | — | — | — | — |

Ориентир из открытых замеров авторов GigaAM (средний WER по 10 русским наборам, не наши данные): v3_rnnt — 8,3%, v3_ctc — 9,1%, v3_e2e_rnnt — 11,2% (после постобработки), Whisper large-v3 — 21,0%. Whisper small обычно хуже large-v3, поэтому с ним цель WER ≤ 10% вряд ли будет достигнута.

## Профили и ресурсы

Замер 22.10.2026: процесс API целиком (настоящие NLU и диалог, `/ws/voice`, uvicorn, один воркер), 2 vCPU Intel Xeon 2,1 ГГц, `VOICE_TORCH_THREADS=2`, `TTS_PROVIDER=none`, фраза 5 с. Скрипт — `bench/ws_load.py`.

| Профиль | RAM в покое | RAM под нагрузкой (пик) | STT, фраза 5 с, p50 / p95 | Пик на старте |
|---|---|---|---|---|
| **int8** (`GIGAAM_QUANTIZE=int8`) | **≈ 1,04 ГБ** | **≈ 1,08 ГБ** (фраза 15 с) | **0,28 / 0,33 с** | ≈ 1,1 ГБ, загрузка 0,9 с |
| fp32 (по умолчанию) | ≈ 1,45 ГБ | ≈ 1,5 ГБ | 0,42 / 0,50 с | ≈ 1,45 ГБ (было 2,3 ГБ через `gigaam.load_model`) |
| SpeechKit, без моделей | ≈ 0,1 ГБ | ≈ 0,1 ГБ | сеть + ≈ 0,3–0,6 с у Яндекса | — |

Как читать цифры:

- **Это замер на архитектуре GigaAM-v3 со случайными весами.** Конфигурация совпадает с настоящей: 222,5 млн параметров, файл весов 849 МБ. Настоящие веса из этой среды скачать нельзя, а память и время энкодера от значений весов не зависят. Декодер RNNT для настоящей речи добавит примерно 0,6 мс на токен, то есть 10–20 мс на фразу. **WER int8 против fp32 не измерен:** это первый пункт `run_bench` на стенде (`gigaam` против `gigaam` с `GIGAAM_QUANTIZE=int8`).
- RAM пересчитана на CPU-сборку torch, которая стоит в образе. Здесь стояло колесо с CUDA из PyPI, его CUDA-библиотеки занимали 193 МБ RSS, они вычтены. Сырые цифры процесса: int8 — 1,23 ГБ в покое и 1,27 ГБ на пике.
- Под нагрузкой память почти не растёт: активации на фразу 5–15 с занимают 15–35 МБ, модель одна, запросы идут к ней по очереди. Два одновременных клиента: STT p50 0,70 с (включая ожидание очереди).
- Основные части int8: модель ~0,5 ГБ (из них файл 310 МБ отображается через mmap), torch ~0,4 ГБ, приложение ~0,1 ГБ.

**Лёгкий профиль для сервера 4 ГБ (лимит API 1,5 ГБ)** — переменные в `.env`, см. `.env.example`:

```
VOICE_MODELS=1 GIGAAM_QUANTIZE=int8 TTS_MODELS=0 STT_PROVIDER=gigaam TTS_PROVIDER=none
VOICE_TORCH_THREADS=2 VOICE_PARTIALS=0 API_MEM_LIMIT=1500m
```

- `GIGAAM_QUANTIZE=int8` и `TTS_MODELS=0` — аргументы сборки (compose пробрасывает их из `.env`).
  - Квантование делается в стадии `models` Dockerfile: пик ≈ 2 ГБ на машине, где собирается образ. fp32-checkpoint потом удаляется, образ на 850 МБ легче.
  - Квантовать на старте контейнера тоже можно, но пик тогда ≈ 2 ГБ, и под лимитом 1,5 ГБ контейнер упадёт по OOM.
- Запас до лимита ≈ 0,45 ГБ. Если сервер окажется медленнее и STT выйдет за 1 с, переходите на SpeechKit — это ещё и −1 ГБ RAM.
- `VOICE_PARTIALS=0`: промежуточные транскрипты на 2 vCPU отнимают процессор у финального распознавания. Фронт пока шлёт запись одним куском, так что ничего не теряется.
- `TTS_PROVIDER=none`: `reply` приходит с `audio_mime: null`, бинарного кадра нет, озвучивает браузер.

**Вариант со SpeechKit** (не помещается GigaAM или нужна экономия RAM): `VOICE_MODELS=0 STT_PROVIDER=speechkit TTS_PROVIDER=none YANDEX_API_KEY=… YANDEX_FOLDER_ID=…`. Ключ — у сервисного аккаунта с ролью `ai.speechkit-stt.user`. Стоимость — около 0,16 ₽ за фразу до 15 с, голос уходит в облако Яндекса.

## Тесты

`pytest tests/voice` — нормализация текста, декодирование webm/ogg/mp4/wav и обрезанного буфера, SpeechKit на моках (авторизация, ретраи), кэш TTS (LRU, диск, склейка параллельных запросов), фабрики от `app.config.Settings` и `.env`, и полный протокол `/ws/voice` поверх `create_app`:
- промежуточные транскрипты;
- общий контекст с `/api/chat`;
- Safari mp4, `start` и `cancel`;
- пустая и битая запись, падение диалога;
- `browser` TTS без аудио-кадра;
- прогрев в lifespan;
- `/health`.

Нужен только ffmpeg (с libopus). Без него тесты, которым нужно аудио, пропускаются: в CI стоит добавить `sudo apt-get install -y ffmpeg`. Модели не скачиваются.
