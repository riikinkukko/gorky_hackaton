# syntax=docker/dockerfile:1.7
# Образ API (владелец — чат 4). Контекст сборки — backend/ (так его собирает docker-compose).
#
#   docker compose build api                              # прод: GigaAM + Silero внутри образа
#   docker compose build --build-arg VOICE_MODELS=0 api   # без torch и весов (~250 МБ вместо ~2,5 ГБ);
#                                                         # годится для STT/TTS = fake | speechkit | browser
#   docker build --build-arg WITH_WHISPER=1 -t api:bench . # + faster-whisper small для бенчмарка
#
# Лёгкий профиль (сервер 4 ГБ, API ≤ 1,5 ГБ): GigaAM int8 без Silero —
#   GIGAAM_QUANTIZE=int8 TTS_MODELS=0 VOICE_TORCH_THREADS=2 в .env → docker compose build api
#   (compose пробрасывает их как build-args, см. профили в .env.example)
#
# Стадии: deps (python-пакеты) → models (веса, отдельный кэшируемый слой) → runtime.

ARG PYTHON=3.12

# ---------- 1. зависимости ----------
FROM python:${PYTHON}-slim AS deps
ARG VOICE_MODELS=1
ARG WITH_WHISPER=0
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
# голос: CPU-сборка torch (≈200 МБ вместо ≈2,5 ГБ CUDA-колёс с PyPI) и GigaAM из git
COPY app/voice/requirements-voice.txt app/voice/requirements-bench.txt ./
RUN if [ "$VOICE_MODELS" = "1" ]; then \
      pip install --prefix=/install torch==2.10.* torchaudio==2.10.* \
        --index-url https://download.pytorch.org/whl/cpu \
      && pip install --prefix=/install -r requirements-voice.txt \
      && if [ "$WITH_WHISPER" = "1" ]; then pip install --prefix=/install -r requirements-bench.txt; fi; \
    fi
# зависимости остальных чатов — из pyproject.toml (кэшируется, пока он не меняется)
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install --prefix=/install .

# ---------- 2. веса моделей ----------
FROM python:${PYTHON}-slim AS models
ARG VOICE_MODELS=1
ARG WITH_WHISPER=0
ARG TTS_MODELS=1
ARG GIGAAM_MODEL=v3_e2e_rnnt
ARG GIGAAM_QUANTIZE=none
ARG SILERO_MODEL=v5_6_ru
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=deps /install /usr/local
COPY app/__init__.py /src/app/__init__.py
COPY app/voice/preload_models.py app/voice/gigaam_loader.py /src/app/voice/
WORKDIR /src
ENV GIGAAM_MODEL=${GIGAAM_MODEL} SILERO_MODEL=${SILERO_MODEL} GIGAAM_QUANTIZE=${GIGAAM_QUANTIZE} \
    GIGAAM_CACHE_DIR=/models/gigaam SILERO_CACHE_DIR=/models/silero WHISPER_CACHE_DIR=/models/whisper
# int8: квантование здесь (пик ≈ 2,7 ГБ на машине сборки), fp32-checkpoint из образа удаляется
RUN mkdir -p /models \
    && if [ "$VOICE_MODELS" = "1" ]; then \
         touch app/voice/__init__.py \
         && TTS=$([ "$TTS_MODELS" = "1" ] && echo silero || echo none) \
         && STT_PROVIDER=gigaam TTS_PROVIDER=$TTS python -m app.voice.preload_models \
         && if [ "$WITH_WHISPER" = "1" ]; then \
              STT_PROVIDER=whisper TTS_PROVIDER=none python -m app.voice.preload_models; fi; \
       fi

# ---------- 3. рантайм ----------
FROM python:${PYTHON}-slim AS runtime
ARG GIGAAM_MODEL=v3_e2e_rnnt
ARG GIGAAM_QUANTIZE=none
ARG SILERO_MODEL=v5_6_ru
ARG VOICE_TORCH_THREADS=4
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# ffmpeg: webm/opus/mp4 → 16 кГц моно и PCM → ogg/opus
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --user-group --create-home --home-dir /home/app app
COPY --from=deps /install /usr/local
# --chown при копировании, а не chown -R потом: иначе веса (до 1 ГБ) продублируются отдельным слоем.
# /models пишется только если int8 не собран заранее (GigaAM квантует на старте и кладёт рядом).
COPY --from=models --chown=10001:10001 /models /models
WORKDIR /app
COPY . .
RUN pip install --no-deps . \
    && chmod 0755 /app/docker-entrypoint.sh \
    && install -d -o app -g app /app/.tts-cache
# MALLOC_ARENA_MAX: glibc по умолчанию заводит арену на поток, и RSS растёт без пользы
ENV GIGAAM_MODEL=${GIGAAM_MODEL} GIGAAM_QUANTIZE=${GIGAAM_QUANTIZE} SILERO_MODEL=${SILERO_MODEL} \
    GIGAAM_CACHE_DIR=/models/gigaam SILERO_CACHE_DIR=/models/silero WHISPER_CACHE_DIR=/models/whisper \
    TTS_CACHE_DIR=/app/.tts-cache MALLOC_ARENA_MAX=2 \
    VOICE_TORCH_THREADS=${VOICE_TORCH_THREADS} OMP_NUM_THREADS=${VOICE_TORCH_THREADS} \
    MKL_NUM_THREADS=${VOICE_TORCH_THREADS}

EXPOSE 8000
# start-period: lifespan грузит модели и прогревает кэш озвучки (int8 ≈ 2 с, fp32 + Silero ≈ 10–20 с)
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1
# SEC-05: не root. Код и библиотеки — только чтение; пишет приложение в /app/.tts-cache, /models,
# $HOME/.cache (torch/HF) и /dev/shm (временные файлы ffmpeg, в Docker он 1777).
USER app
# Строго один воркер: модели живут в памяти процесса, второй воркер удвоит RAM.
# Параллелизм внутри — asyncio + to_thread.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips=127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1", "--ws-max-size", "4194304"]
