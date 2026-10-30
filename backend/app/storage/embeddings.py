"""Эмбеддеры для векторного поиска.

- `HashEmbedder` — детерминированный, без скачивания моделей: признаки = леммы с
  синонимами + символьные триграммы лемм, signed feature hashing в 1024 измерения.
  Ловит «мобилка ↔ мобильное приложение» (через синонимы) и опечатки (триграммы).
- `SentenceTransformerEmbedder` — настоящая модель (напр. deepvk/USER-bge-m3, dim 1024),
  включается переменной EMBEDDINGS_MODEL, если установлен sentence-transformers.
"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Protocol

from app.storage.config import EMBEDDINGS_MODEL
from app.storage.models import EMBEDDING_DIM
from app.storage.text import expand, lemmas


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...


def _h(feature: str) -> tuple[int, float]:
    d = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    v = int.from_bytes(d, "little")
    return v % EMBEDDING_DIM, (1.0 if (v >> 63) & 1 else -1.0)


class HashEmbedder:
    name = "hash-v1"

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIM
        toks = expand(lemmas(text))
        for t in toks:
            i, s = _h("w:" + t)
            vec[i] += s * 1.0
            padded = f"#{t}#"
            for k in range(len(padded) - 2):
                i, s = _h("c:" + padded[k : k + 3])
                vec[i] += s * 0.3
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]


class SentenceTransformerEmbedder:
    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer  # опциональная зависимость

        self.name = model
        self._m = SentenceTransformer(model, device="cpu")
        dim = self._m.get_sentence_embedding_dimension()
        if dim != EMBEDDING_DIM:
            raise ValueError(f"{model}: размерность {dim}, схема ждёт {EMBEDDING_DIM}")
        self._e5 = "e5" in model.lower()

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if self._e5:
            texts = [("query: " if query else "passage: ") + t for t in texts]
        vecs = self._m.encode(texts, normalize_embeddings=True, batch_size=64)
        return [list(map(float, v)) for v in vecs]


@lru_cache(maxsize=2)
def get_embedder(model: str = EMBEDDINGS_MODEL) -> Embedder:
    if model in ("", "hash"):
        return HashEmbedder()
    return SentenceTransformerEmbedder(model)


def embedding_text(
    title: str, topic: str | None, obj: str | None, persons: list[str], orgs: list[str]
) -> str:
    """Текст, который векторизуется для записи."""
    parts = [title, topic or "", obj or "", " ".join(persons), " ".join(orgs)]
    return " ".join(p for p in parts if p)
