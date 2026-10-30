"""Загрузка GigaAM с минимальной пиковой памятью и int8-квантование.

Зачем. ``gigaam.load_model`` читает checkpoint целиком (~850 МБ) и строит рядом модель
со случайными весами (ещё ~850 МБ), поэтому на старте пик ≈ 2,3 ГБ. Здесь:

* fp32: каркас энкодера и головы строится на устройстве ``meta`` (без памяти), веса
  подставляются из checkpoint, открытого через ``mmap``. Пик на загрузке ≈ размер весов.
* int8: линейные слои энкодера (≈ 87% весов) квантуются динамически (``quantize_dynamic``).
  Квантовать на старте нельзя — пик ≈ 2,7 ГБ. Поэтому квантуем при сборке образа
  (``preload_models``) и сохраняем готовый модуль; в рантайме он грузится за ~1 с.

Замеры (2 vCPU, фраза 5 с): fp32 ≈ 0,4 с, int8 ≈ 0,28 с; RAM процесса API с моделью —
см. README, раздел «Лёгкий профиль».
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

log = logging.getLogger(__name__)

INT8_SUFFIX = ".int8.pt"


def int8_path(cache_dir: str, model_name: str) -> str:
    return os.path.join(cache_dir, f"{model_name}{INT8_SUFFIX}")


def _full_name(model_name: str) -> str:
    short = {"ctc", "rnnt", "e2e_ctc", "e2e_rnnt", "ssl"}
    return f"v3_{model_name}" if model_name in short else model_name


def build_on_meta(cfg: Any) -> Any:
    """GigaAMASR без выделения памяти под веса энкодера и головы."""
    import hydra
    import torch
    from gigaam.model import GigaAMASR
    from torch import nn

    model = GigaAMASR.__new__(GigaAMASR)
    nn.Module.__init__(model)
    model.cfg = cfg
    # мел-фильтры считаются при создании — их на meta не построить
    model.preprocessor = hydra.utils.instantiate(cfg.preprocessor)
    with torch.device("meta"):
        model.encoder = hydra.utils.instantiate(cfg.encoder)
        model.head = hydra.utils.instantiate(cfg.head)
    model.decoding = hydra.utils.instantiate(cfg.decoding)
    return model


def to_float32(model: Any) -> Any:
    """Веса GigaAM v3 в checkpoint хранятся в float16. На CPU вход — float32, поэтому
    иначе: «Input type (float) and bias type (c10::Half) should be the same».
    Переводим только обычные параметры и буферы: упакованные int8-веса quantize_dynamic
    не являются параметрами и остаются как есть."""
    import torch

    for module in model.modules():
        for name, p in list(module.named_parameters(recurse=False)):
            if p.dtype == torch.float16:
                setattr(module, name, torch.nn.Parameter(p.detach().float(), requires_grad=False))
        for name, b in list(module.named_buffers(recurse=False)):
            if b is not None and b.dtype == torch.float16:
                module.register_buffer(name, b.float())
    return model


def checkpoint_paths(model_name: str, cache_dir: str) -> tuple[str, str, str | None]:
    """Скачивает (если ещё нет) checkpoint и токенизатор. → (полное имя, ckpt, tokenizer)."""
    import gigaam

    name, ckpt = gigaam._download_model(model_name, cache_dir)
    tokenizer = gigaam._download_tokenizer(name, cache_dir)
    return name, ckpt, tokenizer


def load_fp32_lowmem(ckpt_path: str, tokenizer_path: str | None, model_name: str) -> Any:
    import torch

    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False, mmap=True)
    cfg = ck["cfg"]
    if tokenizer_path is not None:
        cfg.decoding.model_path = tokenizer_path
    model = build_on_meta(cfg)
    model.load_state_dict(ck["state_dict"], assign=True)
    leftovers = [n for n, t in [*model.named_parameters(), *model.named_buffers()] if t.is_meta]
    if leftovers:
        raise RuntimeError(f"GigaAM: веса не загружены для {leftovers[:5]}")
    cfg.model_name = model_name
    return to_float32(model).eval()


def quantize_int8(model: Any) -> Any:
    """Динамическое int8 для nn.Linear энкодера. Голову (RNNT/CTC) не трогаем: она мала."""
    import warnings

    import torch

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # torch.ao.quantization помечен как переезжающий в torchao
        model.encoder = torch.ao.quantization.quantize_dynamic(
            model.encoder.eval(), {torch.nn.Linear}, dtype=torch.qint8
        )
    return model


def save_int8(model: Any, path: str) -> None:
    """Сохраняет квантованный модуль целиком. SentencePiece не сериализуем — его поднимем из cfg."""
    import torch

    decoding, model.decoding = model.decoding, None
    tmp = f"{path}.tmp"
    try:
        torch.save(model, tmp)
        os.replace(tmp, path)
    finally:
        model.decoding = decoding


def load_int8(path: str) -> Any:
    import hydra
    import torch

    model = torch.load(path, map_location="cpu", weights_only=False, mmap=True)
    model.decoding = hydra.utils.instantiate(model.cfg.decoding)
    # файл int8 мог быть собран из float16-весов до исправления — приводим при загрузке
    return to_float32(model).eval()


def load(model_name: str, cache_dir: str, quantize: str, threads: int) -> Any:
    """Точка входа для GigaAMSTT."""
    import torch

    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:  # уже задано (повторная загрузка в том же процессе)
        pass
    name = _full_name(model_name)
    t0 = time.perf_counter()
    if quantize == "int8":
        path = int8_path(cache_dir, name)
        if os.path.isfile(path):
            model = load_int8(path)
            log.info("GigaAM %s int8 loaded in %.1fs", name, time.perf_counter() - t0)
            return model
        log.warning(
            "Нет %s — квантую на старте (пик памяти ≈ 2,7 ГБ). На сервере с лимитом RAM "
            "соберите образ с GIGAAM_QUANTIZE=int8.",
            path,
        )
        _, ckpt, tok = checkpoint_paths(name, cache_dir)
        model = quantize_int8(load_fp32_lowmem(ckpt, tok, name))
        try:
            save_int8(model, path)
        except OSError as exc:
            log.warning("не удалось сохранить %s: %s", path, exc)
    else:
        _, ckpt, tok = checkpoint_paths(name, cache_dir)
        model = load_fp32_lowmem(ckpt, tok, name)
    log.info("GigaAM %s (%s) loaded in %.1fs", name, quantize, time.perf_counter() - t0)
    return model
