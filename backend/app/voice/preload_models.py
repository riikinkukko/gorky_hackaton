"""Скачать веса моделей на этапе сборки образа, чтобы контейнер стартовал без сети.

python -m app.voice.preload_models            # по STT_PROVIDER/TTS_PROVIDER
python -m app.voice.preload_models --all      # gigaam + silero + whisper
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gigaam(model: str, root: str, quantize: str = "none") -> None:
    """Скачать checkpoint (и токенизатор), проверить md5; для int8 — квантовать и удалить fp32.

    gigaam.load_model не используем: он держит в памяти две копии весов (пик ≈ 2,3 ГБ),
    а образ часто собирают прямо на маленьком сервере.
    """
    import gigaam as g

    from app.voice import gigaam_loader as gl

    t = time.perf_counter()
    name, ckpt, tok = gl.checkpoint_paths(model, root)
    expected = g._MODEL_HASHES.get(name)
    if expected and _md5(ckpt) != expected:
        os.remove(ckpt)
        raise SystemExit(f"gigaam {name}: md5 не совпал, файл удалён — запустите сборку ещё раз")
    gl.load_fp32_lowmem(ckpt, tok, name)  # проверяем, что checkpoint читается
    print(f"gigaam {name}: {time.perf_counter() - t:.0f}s → {root}")
    if quantize != "int8":
        return
    path = gl.int8_path(root, name)
    gl.save_int8(gl.quantize_int8(gl.load_fp32_lowmem(ckpt, tok, name)), path)
    gl.load_int8(path)
    os.remove(ckpt)  # fp32 в образе больше не нужен: −850 МБ
    print(f"gigaam {name} int8 → {path} ({os.path.getsize(path) / 2**20:.0f} МБ)")


def silero(model: str, root: str) -> None:
    import torch

    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{model}.pt")
    if not os.path.isfile(path):
        torch.hub.download_url_to_file(f"https://models.silero.ai/models/tts/ru/{model}.pt", path)
    torch.package.PackageImporter(path).load_pickle(
        "tts_models", "model"
    )  # проверяем, что файл целый
    print(f"silero {model} → {path}")


def whisper(model: str, root: str) -> None:
    from faster_whisper import download_model

    print(f"whisper {model} → {download_model(model, cache_dir=root)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    env = os.environ.get
    stt, tts = env("STT_PROVIDER", "gigaam"), env("TTS_PROVIDER", "silero").lower()
    if args.all or stt == "gigaam":
        gigaam(
            env("GIGAAM_MODEL", "v3_e2e_rnnt"),
            env("GIGAAM_CACHE_DIR", "/models/gigaam"),
            env("GIGAAM_QUANTIZE", "none").lower(),
        )
    if args.all or tts == "silero":
        silero(env("SILERO_MODEL", "v5_6_ru"), env("SILERO_CACHE_DIR", "/models/silero"))
    if args.all or stt == "whisper":
        whisper(env("WHISPER_MODEL", "small"), env("WHISPER_CACHE_DIR", "/models/whisper"))


if __name__ == "__main__":
    main()
