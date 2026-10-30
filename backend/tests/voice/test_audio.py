import asyncio

import pytest

from app.voice.audio import AudioError, decode_to_pcm16k, encode_ogg_opus

from .conftest import make_audio, needs_ffmpeg

pytestmark = needs_ffmpeg


@pytest.mark.parametrize(
    "fmt,mime",
    [
        ("webm", "audio/webm;codecs=opus"),
        ("ogg", "audio/ogg"),
        ("wav", "audio/wav"),
        ("mp4", "audio/mp4"),
    ],
)
def test_decode_formats(fmt, mime):
    pcm = asyncio.run(decode_to_pcm16k(make_audio(fmt, 2.0), mime))
    assert pcm.sample_rate == 16000
    assert 1.9 < pcm.duration_s < 2.15


def test_decode_truncated_buffer_for_partials():
    full = make_audio("webm", 3.0)
    pcm = asyncio.run(decode_to_pcm16k(full[: len(full) // 2], "audio/webm", partial=True))
    assert 0.5 < pcm.duration_s < 2.5


def test_decode_garbage_raises():
    with pytest.raises(AudioError):
        asyncio.run(decode_to_pcm16k(b"not audio at all" * 10, "audio/webm"))


def test_encode_ogg_opus_roundtrip():
    ogg = asyncio.run(encode_ogg_opus(b"\x00\x00" * 48000, 48000))
    assert ogg[:4] == b"OggS"
    pcm = asyncio.run(decode_to_pcm16k(ogg, "audio/ogg"))
    assert 0.9 < pcm.duration_s < 1.1
