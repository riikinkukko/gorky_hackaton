"""Набор живых реплик со стенда: загрузка, прогон через /ws/voice, проверка ожиданий.

Набор — ``qa/live/*.jsonl``, по строке на реплику:

    id, cls (A/B/C/D/OK), owner, qa (номер находки), text — транскрипт как в логе стенда,
    history — реплики той же сессии до text, user (по умолчанию 2 — пустой демо-пользователь,
    как у тестировщиков), now (по умолчанию чт 22.10.2026 20:00 МСК — вечер теста),
    expect — что должно получиться, known — находка ещё не исправлена (тест xfail),
    reconstructed — фраза достроена по фрагменту из разбора, was / note — для людей.

Ключи ``expect`` (все необязательные):

    intent              строка или список допустимых
    forbid_intent       список недопустимых intent (обрезанная фраза не должна вести к созданию)
    action              список допустимых action
    forbid_action       список недопустимых action
    type, at            тип и время первой записи ответа ("YYYY-MM-DDTHH:MM", МСК)
    reply_has / reply_not  в тексте ответа есть хотя бы одна из подстрок / нет ни одной
    min_items, max_items   сколько записей в ответе (списки, «покажи все»)
    items_have          подстроки, каждая — в названии хотя бы одной записи ответа
    date                дата первой записи ("YYYY-MM-DD"), когда время не важно
    duration_min        длительность первой записи (ends_at − starts_at)
    title_has/title_not подстроки (без регистра), которые должны быть / не должны быть в названии
    target_has          подстроки названия записи, с которой сделано действие
    forbid_target_has   подстроки, которых в названии этой записи быть не должно
    forbid_at           суффиксы времени, которых не должно быть у созданных записей ("T14:00")
    if_created_*        min_items / at / date / type / reply_has — только если action == created
    if_updated_at       время — только если action == updated
    if_deleted_min_items, if_clarify_min_items  — сколько записей затронуто / названо в вопросе
    unchanged           записи пользователя после реплики те же, что до неё
    unchanged_without_confirm  то же, если ассистент переспросил (action == clarify)

Здесь же ``run_case`` и ``check`` — их вызывает и pytest, и qa/regress.py.

Хранилище — память с CSV (по умолчанию) или PostgreSQL, если задан QA_LIVE_DATABASE_URL
(отдельная база с «qa» в имени; записи пользователя реплики стираются перед ней).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = ROOT / "qa" / "live"
DEFAULT_NOW = "2026-10-22T20:00:00+03:00"
CSV = ROOT / "data" / "voice_assistant_events_1000.csv"
MSK = ZoneInfo("Europe/Moscow")
BACKEND = ROOT / "backend"

# PostgreSQL вместо памяти: QA_LIVE_DATABASE_URL=postgresql+psycopg://…/qa_live
# Перед каждой репликой записи её пользователя удаляются, поэтому база обязана быть
# отдельной: имя должно содержать «qa» (защита от прогона по базе стенда или разработки).
PG_URL = os.environ.get("QA_LIVE_DATABASE_URL") or None
_PG_READY = False


def backend_name() -> str:
    return "postgres" if PG_URL else "memory"


def _pg_dsn() -> str:
    assert PG_URL is not None
    if "qa" not in (urlparse(PG_URL).path or "").lower():
        raise RuntimeError(f"QA_LIVE_DATABASE_URL: имя базы должно содержать «qa»: {PG_URL}")
    return PG_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def _pg_prepare() -> None:
    """Один раз за процесс: миграции alembic до head."""
    global _PG_READY
    if _PG_READY:
        return
    _pg_dsn()
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "app/storage/alembic.ini", "upgrade", "head"],
        cwd=BACKEND, env={**os.environ, "DATABASE_URL": PG_URL or ""},
        check=True, capture_output=True,
    )  # fmt: skip
    _PG_READY = True


def _pg_wipe(user_id: int) -> None:
    import psycopg

    with psycopg.connect(_pg_dsn(), autocommit=True) as conn:
        conn.execute("DELETE FROM dialog_sessions WHERE user_id = %s", (user_id,))
        conn.execute("DELETE FROM items WHERE user_id = %s", (user_id,))  # actions — каскадом


def _pg_seed_csv() -> None:
    """Пользователь 1 на стенде — данные CSV организаторов: свежий импорт перед репликой."""
    subprocess.run(
        [sys.executable, "-m", "app.cli", "import-csv", str(CSV), "--user", "1"],
        cwd=BACKEND, env={**os.environ, "DATABASE_URL": PG_URL or "", "STORAGE_IMPL": "postgres"},
        check=True, capture_output=True,
    )  # fmt: skip


def _pg_snapshot(user_id: int) -> dict[int, tuple]:
    import psycopg

    def msk(v: datetime | None) -> datetime | None:
        return v.astimezone(MSK) if v else None

    with psycopg.connect(_pg_dsn()) as conn:
        rows = conn.execute(
            "SELECT id, title, type, status, starts_at, due_at, rrule FROM items "
            "WHERE user_id = %s AND deleted_at IS NULL",
            (user_id,),
        ).fetchall()
    return {r[0]: (r[1], r[2], r[3], msk(r[4]), msk(r[5]), r[6]) for r in rows}


def load_cases(directory: Path = LIVE_DIR) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                case["_set"] = path.stem
                cases.append(case)
    return cases


@dataclass
class Outcome:
    transcript: str
    reply: dict[str, Any]
    before: dict[int, tuple] = field(default_factory=dict)
    after: dict[int, tuple] = field(default_factory=dict)
    history_replies: list[str] = field(default_factory=list)

    @property
    def action(self) -> str:
        return str(self.reply.get("action"))

    @property
    def intent(self) -> str | None:
        return (self.reply.get("debug") or {}).get("intent")

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self.reply.get("items") or [])

    def summary(self) -> str:
        it = self.items[0] if self.items else None
        at = (it.get("starts_at") or it.get("due_at") or "")[:16] if it else ""
        title = f" «{it['title']}» {at}" if it else ""
        return f"{self.intent}/{self.action}{title} — {self.reply.get('text', '')[:90]!r}"


def make_client(now: str = DEFAULT_NOW) -> TestClient:
    # Лимиты частоты (SEC) защищают стенд; сессия живого теста проигрывается за доли секунды
    # и упёрлась бы в «Слишком часто» уже на 11-й реплике. Снимаем их только здесь
    # (голосовой лимит — переменной окружения на время реплики, см. run_case).
    settings = Settings(  # type: ignore[call-arg]
        rate_chat_per_min=100000,
        rate_service_per_hour=1000000,
        _env_file=None,
        app_env="test",
        freeze_now=datetime.fromisoformat(now),
        nlu_impl="real",
        dialog_impl="real",
        storage_impl="postgres" if PG_URL else "memory",
        database_url=PG_URL,
        stt_provider="fake",
        tts_provider="fake",
        llm_provider="rules",
        # в PostgreSQL CSV не грузим: реплики живых тестов идут от пустых пользователей 2 и 3
        seed_csv=None if PG_URL else str(CSV),
    )
    return TestClient(create_app(settings))


def _snapshot(client: TestClient, user_id: int) -> dict[int, tuple]:
    if PG_URL:
        return _pg_snapshot(user_id)
    repo = client.app.state.container.repo  # type: ignore[attr-defined]
    items = repo.all_items(user_id)
    return {
        i.id: (i.title, i.type.value, i.status.value, i.starts_at, i.due_at, i.rrule) for i in items
    }


def _voice(client: TestClient, text: str, session: str, user_id: int) -> tuple[str, dict]:
    with client.websocket_connect(f"/ws/voice?session_id={session}&user_id={user_id}") as ws:
        ws.send_json({"type": "start", "mime": "audio/webm;codecs=opus"})
        ws.send_bytes(b"text:" + text.encode())
        ws.send_json({"type": "end"})
        final, reply = "", None
        while reply is None:
            msg = ws.receive()
            if msg.get("text") is None:
                continue
            m = json.loads(msg["text"])
            if m["type"] == "transcript" and m.get("final"):
                final = m["text"]
            elif m["type"] == "reply":
                reply = m
    return final, reply


@contextmanager
def _no_voice_rate_limit() -> Iterator[None]:
    """VOICE_MAX_TURNS_PER_MIN берётся из окружения через кешированный get_settings() — поднимаем
    его только на время реплики, сбрасывая кеш на входе и выходе, иначе ломаются тесты лимитов
    в tests/security."""
    from app.voice.config import get_settings  # кеширует окружение (lru_cache)

    old = os.environ.get("VOICE_MAX_TURNS_PER_MIN")
    os.environ["VOICE_MAX_TURNS_PER_MIN"] = "100000"
    get_settings.cache_clear()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("VOICE_MAX_TURNS_PER_MIN", None)
        else:
            os.environ["VOICE_MAX_TURNS_PER_MIN"] = old
        get_settings.cache_clear()


def run_case(case: dict[str, Any]) -> Outcome:
    """Свежее приложение на каждую реплику: история одной реплики не влияет на другие."""
    user = int(case.get("user", 2))
    session = f"live-{case.get('_set', '')}-{case['id']}"
    if PG_URL:
        _pg_prepare()
        _pg_wipe(user)
        if user == 1:
            _pg_seed_csv()
    with _no_voice_rate_limit(), make_client(case.get("now", DEFAULT_NOW)) as client:
        hist = []
        for h in case.get("history", []):
            _, r = _voice(client, h, session, user)
            hist.append(f"{r.get('action')}: {r.get('text', '')[:60]}")
        before = _snapshot(client, user)
        transcript, reply = _voice(client, case["text"], session, user)
        after = _snapshot(client, user)
    return Outcome(transcript, reply, before, after, hist)


def _at(item: dict[str, Any]) -> str:
    return (item.get("starts_at") or item.get("due_at") or "")[:16]


def _has(title: str, parts: list[str]) -> bool:
    t = title.lower().replace("ё", "е")
    return all(p.lower().replace("ё", "е") in t for p in parts)


def _any(title: str, parts: list[str]) -> bool:
    t = title.lower().replace("ё", "е")
    return any(p.lower().replace("ё", "е") in t for p in parts)


def check(case: dict[str, Any], o: Outcome) -> list[str]:
    """Список нарушений ожиданий (пусто — реплика прошла)."""
    e: dict[str, Any] = case.get("expect", {})
    bad: list[str] = []
    first = o.items[0] if o.items else None
    title = (first or {}).get("title", "")

    if o.transcript != case["text"]:
        bad.append(f"транскрипт {o.transcript!r} ≠ {case['text']!r}")
    if "intent" in e:
        want = e["intent"] if isinstance(e["intent"], list) else [e["intent"]]
        if o.intent not in want:
            bad.append(f"intent {o.intent} ∉ {want}")
    if o.intent in e.get("forbid_intent", []):
        bad.append(f"intent {o.intent} запрещён")
    if "action" in e and o.action not in e["action"]:
        bad.append(f"action {o.action} ∉ {e['action']}")
    if o.action in e.get("forbid_action", []):
        bad.append(f"action {o.action} запрещён")
    if "type" in e and (first is None or first.get("type") != e["type"]):
        bad.append(f"тип {(first or {}).get('type')} ≠ {e['type']}")
    if "at" in e and (first is None or _at(first) != e["at"]):
        bad.append(f"время {_at(first) if first else None} ≠ {e['at']}")
    if e.get("reply_has") and not _any(str(o.reply.get("text", "")), e["reply_has"]):
        bad.append(f"в ответе нет ни одного из {e['reply_has']}")
    if e.get("title_has") and not _has(title, e["title_has"]):
        bad.append(f"в названии «{title}» нет {e['title_has']}")
    if e.get("title_not") and first is not None and _any(title, e["title_not"]):
        bad.append(f"в названии «{title}» есть {e['title_not']}")
    if e.get("target_has") and (first is None or not _has(title, e["target_has"])):
        bad.append(f"цель «{title}» не содержит {e['target_has']}")
    if e.get("forbid_target_has") and first is not None and _any(title, e["forbid_target_has"]):
        bad.append(f"цель «{title}» — не та запись")

    text = str(o.reply.get("text", ""))
    if e.get("reply_not") and _any(text, e["reply_not"]):
        bad.append(f"в ответе есть {e['reply_not']}")
    if "min_items" in e and len(o.items) < e["min_items"]:
        bad.append(f"в ответе {len(o.items)} записей, нужно ≥ {e['min_items']}")
    if "max_items" in e and len(o.items) > e["max_items"]:
        bad.append(f"в ответе {len(o.items)} записей, нужно ≤ {e['max_items']}")
    if e.get("items_have"):
        titles = [str(i.get("title", "")) for i in o.items]
        for part in e["items_have"]:
            if not any(_any(t, [part]) for t in titles):
                bad.append(f"среди записей ответа нет «{part}»")
    if "date" in e and (first is None or _at(first)[:10] != e["date"]):
        bad.append(f"дата {_at(first)[:10] if first else None} ≠ {e['date']}")
    if "duration_min" in e:
        st, en = (first or {}).get("starts_at"), (first or {}).get("ends_at")
        got = (
            int((datetime.fromisoformat(en) - datetime.fromisoformat(st)).total_seconds() // 60)
            if st and en
            else None
        )
        if got != e["duration_min"]:
            bad.append(f"длительность {got} мин ≠ {e['duration_min']}")

    created = [i for i in o.after if i not in o.before]
    for suffix in e.get("forbid_at", []):
        for iid in created:
            when = o.after[iid][3] or o.after[iid][4]
            if when is not None and when.isoformat()[10:16] == suffix:
                bad.append(f"создана запись на {suffix[1:]}")
    if o.action == "created":
        if "if_created_min_items" in e and len(created) < e["if_created_min_items"]:
            bad.append(f"создано {len(created)} записей, нужно ≥ {e['if_created_min_items']}")
        if "if_created_at" in e and (first is None or _at(first) != e["if_created_at"]):
            bad.append(f"время {_at(first) if first else None} ≠ {e['if_created_at']}")
        if "if_created_date" in e and (first is None or _at(first)[:10] != e["if_created_date"]):
            bad.append(f"дата {_at(first)[:10] if first else None} ≠ {e['if_created_date']}")
        if "if_created_type" in e and (first is None or first.get("type") != e["if_created_type"]):
            bad.append(f"тип {(first or {}).get('type')} ≠ {e['if_created_type']}")
        if e.get("if_created_reply_has") and not _any(text, e["if_created_reply_has"]):
            bad.append(f"создано без слов {e['if_created_reply_has']} в ответе")
    if o.action == "updated" and "if_updated_at" in e:
        if first is None or _at(first) != e["if_updated_at"]:
            bad.append(f"перенесено на {_at(first) if first else None} ≠ {e['if_updated_at']}")
    if o.action == "clarify" and "if_clarify_min_items" in e:
        if len(o.items) < e["if_clarify_min_items"]:
            bad.append(
                f"переспросил про {len(o.items)} запись(и), нужно ≥ {e['if_clarify_min_items']}"
            )
    if o.action == "deleted" and "if_deleted_min_items" in e:
        gone = [i for i in o.before if i not in o.after]
        if len(gone) < e["if_deleted_min_items"]:
            bad.append(f"удалено {len(gone)}, нужно ≥ {e['if_deleted_min_items']}")

    must_keep = e.get("unchanged") or (e.get("unchanged_without_confirm") and o.action == "clarify")
    if must_keep and o.before != o.after:
        changed = sorted(
            {i for i in o.before if o.after.get(i) != o.before[i]}
            | {i for i in o.after if i not in o.before}
        )
        what = [o.before.get(i, o.after.get(i, ("?",)))[0] for i in changed][:3]
        bad.append(f"изменены записи без подтверждения: {what}")
    return bad
