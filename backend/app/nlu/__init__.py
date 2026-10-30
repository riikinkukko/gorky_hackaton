"""NLU: понимание реплики → Command (контракт v1.2), даты → TimeSpec.

Фабрики для app/container.py (включаются NLU_IMPL=real):
    build_date_resolver(settings) -> DateResolver
    build_nlu(settings, dates)    -> NLU
По умолчанию работает только на правилах. LLM подключается явно: LLM_PROVIDER=gigachat | yandex.

Помощники для диалога:
    has_explicit_date(text, now) — была ли в фразе дата (дублирует TimeSpec.has_date)
    parse_shift(text)            — «на час позже» → +60 минут
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .asr import AsrNormalizer, AsrResult, KnownEntities, RepoEntitySource
from .dates import RuleDateResolver, has_explicit_date
from .entities import EntityNormalizer, default_normalizer
from .llm.base import LLMClient
from .router import RuleRouter, parse_shift
from .service import HybridNLU

log = logging.getLogger("app.nlu")

__all__ = [
    "AsrNormalizer",
    "AsrResult",
    "EntityNormalizer",
    "HybridNLU",
    "KnownEntities",
    "RepoEntitySource",
    "RuleDateResolver",
    "RuleRouter",
    "build_date_resolver",
    "build_nlu",
    "default_normalizer",
    "has_explicit_date",
    "parse_shift",
]


def _setting(settings: Any, name: str, default: str | None = None) -> str | None:
    """Атрибут settings (llm_provider / LLM_PROVIDER), иначе переменная окружения."""
    for attr in (name.lower(), name):
        val = getattr(settings, attr, None) if settings is not None else None
        if val is not None:
            secret = getattr(val, "get_secret_value", None)  # pydantic SecretStr
            return str(secret() if callable(secret) else val)
    return os.environ.get(name, default)


def _verify(value: str | None) -> bool | str:
    if value is None:
        return True
    if value.lower() in ("", "1", "true", "yes"):
        return True
    if value.lower() in ("0", "false", "no"):
        return False
    return value  # путь к CA-бандлу


def build_date_resolver(settings: Any = None) -> RuleDateResolver:
    """DateResolver из контракта. Настроек не требует: «сейчас» приходит в resolve(now=…)."""
    return RuleDateResolver()


def build_nlu(
    settings: Any = None, dates: RuleDateResolver | None = None, *, provider: str | None = None
) -> HybridNLU:
    """NLU из контракта. Без LLM_PROVIDER (или =rules) — только правила, без сетевых вызовов."""
    if dates is not None and not isinstance(dates, RuleDateResolver):
        log.warning("build_nlu: получен не RuleDateResolver — внутри используется свой")
        dates = None
    name = (provider or _setting(settings, "LLM_PROVIDER") or "rules").lower()
    timeout = float(_setting(settings, "NLU_LLM_TIMEOUT", "6") or 6)
    threshold = float(_setting(settings, "NLU_RULES_THRESHOLD", "0.85") or 0.85)
    llm: LLMClient | None = None
    try:
        if name == "gigachat":
            from .llm.gigachat import GigaChatClient

            llm = GigaChatClient(
                _setting(settings, "GIGACHAT_CREDENTIALS", "") or "",
                scope=_setting(settings, "GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
                or "GIGACHAT_API_PERS",
                model=_setting(settings, "GIGACHAT_MODEL", "GigaChat-2") or "GigaChat-2",
                verify=_verify(_setting(settings, "GIGACHAT_VERIFY_SSL")),
                timeout=timeout,
            )
        elif name == "yandex":
            from .llm.yandex import YandexGPTClient

            llm = YandexGPTClient(
                _setting(settings, "YANDEX_API_KEY", "") or "",
                _setting(settings, "YANDEX_FOLDER_ID", "") or "",
                model=_setting(settings, "YANDEX_MODEL", "yandexgpt-lite") or "yandexgpt-lite",
                timeout=timeout,
            )
    except ValueError as e:
        log.warning("LLM %s не настроен (%s) — работаем на правилах", name, e)
        llm = None
    asr = (_setting(settings, "NLU_ASR_NORMALIZE", "true") or "true").lower() not in (
        "0",
        "false",
        "no",
    )
    return HybridNLU(
        llm, resolver=dates, rules_threshold=threshold, llm_timeout=timeout, asr_normalize=asr
    )
