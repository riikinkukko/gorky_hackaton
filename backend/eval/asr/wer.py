"""WER / CER с выравниванием по словам — для qa/run_voice.py и отчёта.

Нормализация: ``app.voice.text_norm.normalize_for_wer`` (чат 4) плюс одна поправка:
«15 00» / «15.00» → «15:00» до нормализации. GigaAM e2e иногда пишет время через пробел,
а ``normalize_for_wer`` превращает «15 00» в «пятнадцать ноль», а «15:00» — в «пятнадцать
ноль ноль». Без поправки формат времени считался бы ошибкой распознавания.

Выравнивание нужно для класса D: если все ошибки — пропуски слов в начале или в конце
эталона, а середина распознана верно, то скорее обрезана запись, а не ошиблась модель.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.voice.text_norm import normalize_for_wer

_TIME_SPACE = re.compile(r"\b([01]?\d|2[0-3])[ .]([0-5]\d)\b")


def norm(text: str) -> str:
    return normalize_for_wer(_TIME_SPACE.sub(r"\1:\2", text or ""))


@dataclass
class Alignment:
    ref: list[str]
    hyp: list[str]
    ops: list[tuple[str, str | None, str | None]] = field(default_factory=list)  # (op, ref, hyp)

    @property
    def errors(self) -> int:
        return sum(op != "ok" for op, _, _ in self.ops)

    @property
    def wer(self) -> float:
        return self.errors / max(len(self.ref), 1)

    def edge_deletions(self) -> tuple[int, int]:
        """Сколько слов эталона потеряно подряд в начале и в конце (только удаления)."""
        head = 0
        for op, _, _ in self.ops:
            if op != "del":
                break
            head += 1
        tail = 0
        for op, _, _ in reversed(self.ops):
            if op != "del":
                break
            tail += 1
        return head, tail

    def only_edge_errors(self) -> bool:
        """Все ошибки — удаления на краях (признак обрезанной записи)."""
        head, tail = self.edge_deletions()
        return self.errors > 0 and head + tail == self.errors

    def diff(self) -> str:
        out = []
        for op, r, h in self.ops:
            if op == "ok":
                out.append(r or "")
            elif op == "sub":
                out.append(f"[{r}→{h}]")
            elif op == "del":
                out.append(f"[-{r}]")
            else:
                out.append(f"[+{h}]")
        return " ".join(out)


def align(ref_text: str, hyp_text: str) -> Alignment:
    r, h = norm(ref_text).split(), norm(hyp_text).split()
    n, m = len(r), len(h)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(
                d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (r[i - 1] != h[j - 1])
            )
    ops: list[tuple[str, str | None, str | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (r[i - 1] != h[j - 1]):
            ops.append(("ok" if r[i - 1] == h[j - 1] else "sub", r[i - 1], h[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append(("del", r[i - 1], None))
            i -= 1
        else:
            ops.append(("ins", None, h[j - 1]))
            j -= 1
    ops.reverse()
    return Alignment(r, h, ops)


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def cer(ref_text: str, hyp_text: str) -> float:
    r, h = norm(ref_text).replace(" ", ""), norm(hyp_text).replace(" ", "")
    return _lev(r, h) / max(len(r), 1)


def corpus(pairs: list[tuple[str, str]]) -> tuple[float, float]:
    """Корпусные WER и CER: сумма ошибок / сумма слов (символов) эталона."""
    we = wn = ce = cn = 0
    for ref, hyp in pairs:
        a = align(ref, hyp)
        we += a.errors
        wn += len(a.ref)
        r, h = norm(ref).replace(" ", ""), norm(hyp).replace(" ", "")
        ce += _lev(r, h)
        cn += len(r)
    return we / max(wn, 1), ce / max(cn, 1)


def entity_hits(entities: list[str], hyp_text: str) -> tuple[int, int]:
    """Сколько сущностей (имена, организации) узнаваемы в транскрипте по корню из 4 букв —
    так же, как ``keyword_recall`` в бенчмарке чата 4: «Иваном» засчитывается за «Иван»."""
    words = norm(hyp_text).split()
    hit = 0
    for e in entities:
        stem = norm(e)[:4]
        hit += any(w.startswith(stem) for w in words)
    return hit, len(entities)
