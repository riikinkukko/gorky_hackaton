import asyncio

import httpx
import pytest

from app.config import Settings
from app.voice.audio import Pcm
from app.voice.config import reload_settings
from app.voice.factory import build_stt, build_tts
from app.voice.stt import ConstSTT, SpeechKitSTT, STTError
from app.voice.tts import BaseTTS, CachedTTS, NullTTS, SpeechKitTTS

from .conftest import make_audio, needs_ffmpeg


def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return reload_settings()


def test_factory_takes_providers_from_app_settings():
    app_settings = Settings(_env_file=None, stt_provider="speechkit", tts_provider="browser")
    assert build_stt(app_settings).name == "speechkit"
    assert isinstance(build_tts(app_settings), NullTTS)
    assert build_tts(Settings(_env_file=None, tts_provider="silero")).name == "silero"
    with pytest.raises(ValueError):
        build_stt(Settings(_env_file=None, stt_provider="alexa"))


def test_voice_env_also_read_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("YANDEX_API_KEY='from-file'\nSPEECHKIT_VOICE=filipp\n")
    s = reload_settings()
    assert s.yandex_api_key == "from-file" and s.speechkit_voice == "filipp"
    monkeypatch.setenv("SPEECHKIT_VOICE", "jane")  # окружение важнее файла
    assert reload_settings().speechkit_voice == "jane"


def test_partials_auto(monkeypatch):
    assert _settings(monkeypatch, STT_PROVIDER="gigaam").partials_enabled
    assert _settings(monkeypatch, STT_PROVIDER="const").partials_enabled
    assert not _settings(monkeypatch, STT_PROVIDER="speechkit").partials_enabled
    assert _settings(monkeypatch, STT_PROVIDER="speechkit", VOICE_PARTIALS="on").partials_enabled


def test_speechkit_stt_request(monkeypatch):
    s = _settings(monkeypatch, YANDEX_API_KEY="k", YANDEX_FOLDER_ID="f")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["params"] = dict(req.url.params)
        seen["len"] = len(req.content)
        return httpx.Response(200, json={"result": "Что у меня сегодня"})

    stt = SpeechKitSTT(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    text = asyncio.run(stt.transcribe_pcm(Pcm(b"\x01\x00" * 16000)))
    assert text == "Что у меня сегодня"
    assert seen["auth"] == "Api-Key k"
    assert seen["params"]["format"] == "lpcm" and seen["params"]["sampleRateHertz"] == "16000"
    assert seen["params"]["folderId"] == "f"
    assert seen["len"] == 32000


def test_speechkit_stt_retries_5xx_then_fails_on_4xx(monkeypatch):
    s = _settings(monkeypatch, YANDEX_API_KEY="k")
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(401, text="bad key")

    stt = SpeechKitSTT(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(STTError, match="401"):
        asyncio.run(stt.transcribe_pcm(Pcm(b"\x01\x00" * 16000)))
    assert len(calls) == 2


def test_speechkit_tts_request(monkeypatch):
    s = _settings(monkeypatch, YANDEX_API_KEY="k", SPEECHKIT_VOICE="filipp")

    def handler(req):
        form = dict(x.split("=") for x in req.content.decode().split("&"))
        assert form["format"] == "oggopus" and form["voice"] == "filipp"
        return httpx.Response(200, content=b"OggS-fake")

    tts = SpeechKitTTS(s, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    audio, mime = asyncio.run(tts.synthesize("Готово."))
    assert audio == b"OggS-fake" and mime == "audio/ogg"


def test_short_audio_is_empty(monkeypatch):
    stt = ConstSTT(reload_settings(), text="должно не вызваться")
    assert asyncio.run(stt.transcribe_pcm(Pcm(b"\x00\x00" * 1000))) == ""


class CountingTTS(BaseTTS):
    name = "count"
    voice = "v"

    def __init__(self):
        super().__init__(reload_settings())
        self.calls = 0

    async def _synthesize(self, text):
        self.calls += 1
        await asyncio.sleep(0.01)
        return f"audio:{text}".encode()


def test_cache_lru_dedup_and_disk(tmp_path):
    inner = CountingTTS()
    cache = CachedTTS(inner, max_items=2, disk_dir=str(tmp_path))

    async def scenario():
        await asyncio.gather(
            *(cache.synthesize("Готово.") for _ in range(5))
        )  # один синтез на 5 запросов
        await cache.synthesize("готово.")  # регистр и пробелы не важны
        await cache.synthesize("A")
        await cache.synthesize("B")  # вытесняет «Готово.» из памяти, но не с диска
        await cache.synthesize("Готово.")

    asyncio.run(scenario())
    assert inner.calls == 3
    assert len(list(tmp_path.glob("*.ogg"))) == 3


@needs_ffmpeg
def test_fake_stt_full_path():
    stt = ConstSTT(reload_settings(), text="привет")
    assert asyncio.run(stt.transcribe(make_audio("webm", 1.0), "audio/webm")) == "привет"
    assert stt.last_timing["audio_s"] > 0.9


def test_null_tts_returns_empty_audio():
    assert asyncio.run(NullTTS().synthesize("Готово.")) == (b"", "")
