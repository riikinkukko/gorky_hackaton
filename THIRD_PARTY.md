# Сторонние компоненты

Лицензии моделей, сервисов и библиотек, которые входят в образ или вызываются продуктом. Код проекта — MIT.

## Голосовой контур

*Ведёт чат 4. Проверено 21.10.2026 по файлам LICENSE в репозиториях и карточкам моделей.*

### Модели

| Компонент | Версия | Где используется | Лицензия | Коммерческое использование |
|---|---|---|---|---|
| **GigaAM-v3** (SberDevices), веса `v3_e2e_rnnt` / `v3_ctc` / `v3_rnnt` | git `7447938d` (Python-пакет 0.2.0) | STT, по умолчанию | MIT (код и веса) | ✅ разрешено |
| **Silero TTS `v5_6_ru`** (голоса aidar, baya, kseniya, xenia, eugene) | models.yml, коммит `e3822983` | TTS, по умолчанию | **CC BY-NC-SA 4.0** (файл `LICENSE`; в README указано «CC-NC-BY») | ❌ **только некоммерческое**. Для пилота и продажи — `TTS_PROVIDER=speechkit` или коммерческая лицензия Silero |
| Silero TTS `v5_cis_base` (русские голоса с префиксом `ru_`) | — | не используется, запасной вариант | MIT (`LICENSE_CIS`) | ✅, но нет автоударений: нужен отдельный расстановщик |
| Whisper small (OpenAI), конвертация CTranslate2 `Systran/faster-whisper-small` | — | только бенчмарк (`WITH_WHISPER=1`) | MIT | ✅ |

### Облачные сервисы

| Сервис | Где | Условия |
|---|---|---|
| Yandex SpeechKit (API v1 `stt:recognize`, `tts:synthesize`) | `STT_PROVIDER=speechkit`, `TTS_PROVIDER=speechkit` | Платный сервис по условиям Yandex Cloud; аудио уходит в облако Яндекса (учесть в политике ПДн) |

### Библиотеки и программы

| Компонент | Лицензия | Примечание |
|---|---|---|
| PyTorch, torchaudio | BSD-3-Clause | CPU-сборка |
| FFmpeg (пакет Debian) | GPL-2.0+ (сборка Debian с `--enable-gpl`), библиотеки LGPL-2.1+ | Вызывается как отдельная программа через `subprocess`, не линкуется |
| hydra-core | MIT | зависимость GigaAM |
| omegaconf | BSD-3-Clause | зависимость GigaAM |
| onnx | Apache-2.0 | зависимость GigaAM |
| onnxruntime | MIT | зависимость GigaAM и faster-whisper |
| sentencepiece | Apache-2.0 | токенизатор GigaAM-e2e |
| soundfile | BSD-3-Clause | зависимость GigaAM |
| numpy | BSD-3-Clause | |
| httpx | BSD-3-Clause | клиент SpeechKit |
| faster-whisper | MIT | только бенчмарк |
| CTranslate2 | MIT | только бенчмарк |
| PyAV (`av`) | BSD-3-Clause (внутри FFmpeg — LGPL) | только бенчмарк |
| JSZip (CDN, страница `app/voice/bench/recorder.html`) | MIT или GPL-3.0 (на выбор) | инструмент команды, в продукт не входит |

### Что сказать жюри про лицензии

На демо используется Silero `v5_6_ru` с некоммерческой лицензией, это допустимо для конкурсного прототипа. В коммерческой версии TTS переключается одной переменной на SpeechKit (или покупается лицензия Silero). Распознавание (GigaAM, MIT) остаётся локальным в любом варианте, так что голос пользователя не покидает сервер.

## Остальные компоненты

FastAPI, SQLAlchemy, pymorphy3, эмбеддинги поиска, LLM-провайдеры и фронтенд добавляют чаты 0–3 и 5 по мере подключения: строка таблицы на каждую зависимость с лицензией и условиями коммерческого использования.
