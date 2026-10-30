"""Загрузчик GigaAM: meta + mmap, int8-квантование, сохранение. Нужны torch и gigaam (образ с
VOICE_MODELS=1); в CI их нет — тесты пропускаются. Модель — крошечная копия архитектуры v3
со случайными весами, качество не проверяем, только механику и память."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("gigaam")

from omegaconf import OmegaConf  # noqa: E402

from app.voice import gigaam_loader as gl  # noqa: E402
from app.voice.audio import Pcm  # noqa: E402
from app.voice.config import reload_settings  # noqa: E402
from app.voice.stt import GigaAMSTT  # noqa: E402

NAME = "v3_e2e_rnnt"


def _tiny_cfg() -> object:
    vocab = [f"t{i}" for i in range(32)]
    return OmegaConf.create(
        {
            "model_name": NAME,
            "preprocessor": {
                "_target_": "gigaam.preprocess.FeatureExtractor",
                "sample_rate": 16000,
                "features": 64,
                "win_length": 320,
                "hop_length": 160,
                "n_fft": 320,
                "center": False,
            },
            "encoder": {
                "_target_": "gigaam.encoder.ConformerEncoder",
                "feat_in": 64,
                "n_layers": 2,
                "d_model": 64,
                "subsampling": "conv1d",
                "subs_kernel_size": 5,
                "subsampling_factor": 4,
                "ff_expansion_factor": 4,
                "self_attention_model": "rotary",
                "n_heads": 4,
                "conv_kernel_size": 5,
            },
            "head": {
                "_target_": "gigaam.decoder.RNNTHead",
                "decoder": {"pred_hidden": 32, "pred_rnn_layers": 1, "num_classes": 33},
                "joint": {
                    "enc_hidden": 64,
                    "pred_hidden": 32,
                    "joint_hidden": 32,
                    "num_classes": 33,
                },
            },
            "decoding": {"_target_": "gigaam.decoding.RNNTGreedyDecoding", "vocabulary": vocab},
        }
    )


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from gigaam.model import GigaAMASR

    torch.manual_seed(0)
    model = GigaAMASR(_tiny_cfg()).eval()
    ckpt = tmp_path / f"{NAME}.ckpt"
    torch.save({"cfg": model.cfg, "state_dict": model.state_dict()}, ckpt)
    monkeypatch.setattr(gl, "checkpoint_paths", lambda name, root: (name, str(ckpt), None))
    return tmp_path


def test_lowmem_fp32_matches_regular_load(cache: Path) -> None:
    from gigaam.model import GigaAMASR

    ck = torch.load(cache / f"{NAME}.ckpt", weights_only=False)
    ref = GigaAMASR(ck["cfg"]).eval()
    ref.load_state_dict(ck["state_dict"])
    low = gl.load_fp32_lowmem(str(cache / f"{NAME}.ckpt"), None, NAME)
    wav = torch.randn(1, 16000) * 0.1
    length = torch.tensor([16000])
    with torch.inference_mode():
        a, _ = ref.forward(wav, length)
        b, _ = low.forward(wav, length)
    assert torch.allclose(a, b)


def test_int8_roundtrip_and_close_to_fp32(cache: Path) -> None:
    fp32 = gl.load_fp32_lowmem(str(cache / f"{NAME}.ckpt"), None, NAME)
    q = gl.quantize_int8(gl.load_fp32_lowmem(str(cache / f"{NAME}.ckpt"), None, NAME))
    path = gl.int8_path(str(cache), NAME)
    gl.save_int8(q, path)
    assert q.decoding is not None  # save_int8 возвращает декодер на место
    loaded = gl.load_int8(path)
    wav, length = torch.randn(1, 32000) * 0.1, torch.tensor([32000])
    with torch.inference_mode():
        ref, _ = fp32.forward(wav, length)
        out, _ = loaded.forward(wav, length)
    rel = ((out - ref).norm() / ref.norm()).item()
    assert rel < 0.1, rel
    assert any("quantized" in type(m).__module__ for m in loaded.encoder.modules())


@pytest.mark.parametrize("prebuilt", [True, False])
def test_gigaam_stt_int8(cache: Path, monkeypatch: pytest.MonkeyPatch, prebuilt: bool) -> None:
    if prebuilt:
        gl.save_int8(
            gl.quantize_int8(gl.load_fp32_lowmem(str(cache / f"{NAME}.ckpt"), None, NAME)),
            gl.int8_path(str(cache), NAME),
        )
    monkeypatch.setenv("GIGAAM_QUANTIZE", "int8")
    monkeypatch.setenv("GIGAAM_CACHE_DIR", str(cache))
    monkeypatch.setenv("VOICE_TORCH_THREADS", "1")
    stt = GigaAMSTT(reload_settings())
    text = asyncio.run(stt.transcribe_pcm(Pcm(b"\x10\x00" * 16000)))
    assert isinstance(text, str)
    # без готового файла квантуем на старте и сохраняем — следующий старт быстрый
    assert os.path.isfile(gl.int8_path(str(cache), NAME))


def test_preload_int8_removes_fp32(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gigaam

    from app.voice import preload_models

    ckpt = cache / f"{NAME}.ckpt"
    monkeypatch.setitem(gigaam._MODEL_HASHES, NAME, preload_models._md5(str(ckpt)))
    preload_models.gigaam(NAME, str(cache), "int8")
    assert os.path.isfile(gl.int8_path(str(cache), NAME))
    assert not (cache / f"{NAME}.ckpt").exists()


def test_preload_rejects_corrupted_checkpoint(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gigaam

    from app.voice import preload_models

    monkeypatch.setitem(gigaam._MODEL_HASHES, NAME, "0" * 32)
    with pytest.raises(SystemExit, match="md5"):
        preload_models.gigaam(NAME, str(cache), "int8")
    assert not (cache / f"{NAME}.ckpt").exists()
