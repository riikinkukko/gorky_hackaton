"""Метрики eval: чистые функции, без БД."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from statistics import median


def recall_at_k(ranked: list[int], relevant: set[int], k: int = 5) -> float:
    """|top-k ∩ relevant| / min(k, |relevant|).

    Делитель min(k, |rel|): при 103 релевантных записях («про презентацию») идеальная выдача
    даёт 1.0, а не 5/103. Пустой relevant — метрика не определена (вызывающий учитывает отдельно).
    """
    if not relevant:
        raise ValueError("recall@k не определён для пустого relevant")
    return len(set(ranked[:k]) & relevant) / min(k, len(relevant))


def reciprocal_rank(ranked: list[int], relevant: set[int]) -> float:
    for i, x in enumerate(ranked, 1):
        if x in relevant:
            return 1.0 / i
    return 0.0


def percentile(values: list[float], p: float) -> float:
    """Перцентиль методом nearest-rank (p в 0..100)."""
    if not values:
        return math.nan
    s = sorted(values)
    k = max(1, math.ceil(p / 100 * len(s)))
    return s[k - 1]


def norm_title(s: str | None) -> str:
    s = (s or "").lower().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", s))


@dataclass
class Metric:
    name: str
    goal: str
    values: list[float] = field(default_factory=list)
    fmt: str = "pct"  # pct | float | ms

    def add(self, v: float | bool) -> None:
        self.values.append(float(v))

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def value(self) -> float:
        return sum(self.values) / len(self.values) if self.values else math.nan

    def render(self) -> str:
        if not self.values:
            return "—"
        v = self.value
        if self.fmt == "pct":
            return f"{v * 100:.0f}%"
        if self.fmt == "float":
            return f"{v:.2f}"
        return f"{v:.0f} мс"


def latency_cell(ms: list[float]) -> str:
    if not ms:
        return "—"
    return f"{percentile(ms, 50) / 1000:.2f} с / {percentile(ms, 95) / 1000:.2f} с"


def markdown_table(metrics: list[Metric], latency_ms: list[float], latency_goal: str) -> str:
    lines = ["| Метрика | Цель | Результат | N |", "|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m.name} | {m.goal} | {m.render()} | {m.n} |")
    lines.append(
        f"| Задержка p50 / p95 | {latency_goal} | {latency_cell(latency_ms)} | {len(latency_ms)} |"
    )
    return "\n".join(lines)


__all__ = [
    "Metric",
    "markdown_table",
    "median",
    "norm_title",
    "percentile",
    "recall_at_k",
    "reciprocal_rank",
]
