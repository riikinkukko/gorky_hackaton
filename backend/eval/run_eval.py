"""Прогон эталонного набора golden_eval.jsonl и таблица метрик для README.

Формат набора и правила прогона — docs «golden_eval_format.md» (файлы проекта):
каждая строка в чистой среде, `history` проигрывается в той же сессии перед `query`,
даты сравниваются в Europe/Moscow с точностью до минуты.

Режимы (--target):
  container     по умолчанию: app.container.build_container(Settings()) — fake или real
                из .env. Postgres: строки изолированы клонами пользователя; memory: новый
                контейнер с CSV на каждую изменяющую строку.
  orchestrator  in-process через Orchestrator (чат 2): --orchestrator module:attr или
                EVAL_ORCHESTRATOR. attr — объект с .handle() или фабрика (sync/async),
                которая принимает user_id (или ничего).
  http          через POST /api/chat; пользователь — X-User-Id, «сейчас» — X-Freeze-Now
                (или сервер запущен с FREEZE_NOW из набора).
  search        только поиск: строки category=search без history, напрямую через
                HybridSearchEngine; слоты строит базовый разборщик (время глагола, год).
                Проверка слоя данных без NLU.
  auto          --orchestrator/EVAL_ORCHESTRATOR, если задан; иначе container, если есть
                app.container; иначе search.

Таблица всегда печатается в stdout (CI кладёт её в job summary); --out дублирует в файл.

Изоляция: CSV импортируется один раз в шаблонного пользователя; строки, которые могут
менять данные (create/update/…/history), получают свежую копию (INSERT … SELECT, ~50 мс),
строки только для чтения делят одну копию. Ожидаемые id — id из CSV, сопоставление через
items.source_ref = "csv:<id>". Все временные пользователи удаляются в конце.

    cd backend && python -m eval.run_eval --min-recall 0.9 >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text

from app.contracts import AssistantReply, DialogState, Slots, TimeSpec
from app.storage.config import DEFAULT_TZ
from app.storage.db import get_sessionmaker
from app.storage.models import ItemRow, UserRow
from eval.metrics import Metric, markdown_table, norm_title, recall_at_k, reciprocal_rank

INTENT_MAP = {
    "create": "create_item",
    "list": "list_items",
    "search": "search_items",
    "update": "update_item",
    "complete": "complete_item",
    "delete": "delete_item",
    "confirm": "confirm",
    "reject": "reject",
    "undo": "undo",
    "smalltalk": "smalltalk",
    "unknown": "unknown",
}
READ_ONLY = {"list", "search", "smalltalk"}
CRITERION_QIDS = {f"q{i}" for i in range(19, 31)}  # критерий чата 3: Recall@5 ≥ 90%

# ---------- базовый разборщик для режима search ----------

_PAST = re.compile(
    r"\b(был[аио]?|ставил[аи]?|звонил[аи]?|обсуждал[аи]?|готовил(ся|ась|и)?|"
    r"делал[аи]?|отправлял[аи]?|последн\w+|когда-то|раньше|прошл\w+)\b",
    re.I,
)
_FUTURE = re.compile(r"\b(будет|будут|предстоит|запланирован\w*|следующ\w+|ближайш\w+)\b", re.I)


def baseline_slots(query: str, now: datetime) -> Slots:
    """Минимальный разбор: грамматическое время и «в этом/прошлом году». Не замена NLU."""
    tense = "past" if _PAST.search(query) else "future" if _FUTURE.search(query) else "any"
    when = None
    if m := re.search(r"\bв (этом|прошлом) году\b", query, re.I):
        y = now.year - (m[1].lower() == "прошлом")
        when = TimeSpec(
            text=m[0],
            start=datetime(y, 1, 1, tzinfo=DEFAULT_TZ),
            end=datetime(y + 1, 1, 1, tzinfo=DEFAULT_TZ),
            is_period=True,
        )
    return Slots(query=query, tense=tense, when=when)  # type: ignore[arg-type]


# ---------- данные ----------


def find_csv(arg: str | None) -> Path:
    here = Path(__file__).resolve().parent
    for p in [
        arg,
        here.parent.parent / "data/voice_assistant_events_1000.csv",
        Path("data/voice_assistant_events_1000.csv"),
    ]:
        if p and Path(p).exists():
            return Path(p)
    raise SystemExit("не найден CSV: передайте --csv")


class Sandbox:
    """Шаблонный пользователь с импортом CSV и дешёвые клоны для изоляции строк."""

    isolated = True

    def __init__(self) -> None:
        self.template_id: int | None = None
        self.template_note = ""
        self.users: list[int] = []

    async def setup(self, csv_path: Path) -> None:
        """Шаблон — засеянный пользователь (make seed / старт API), если его импорт не тронут;
        иначе — свежий импорт во временного пользователя."""
        from app.cli import import_csv, read_csv

        seed_uid = int(os.getenv("EVAL_SEED_USER_ID", os.getenv("SEED_USER_ID", "1")))
        n_csv = len(read_csv(csv_path))
        async with get_sessionmaker()() as s:
            pristine = await s.scalar(
                select(func.count()).where(
                    ItemRow.user_id == seed_uid,
                    ItemRow.source == "import",
                    ItemRow.source_ref.like("csv:%"),
                    ItemRow.deleted_at.is_(None),
                    ItemRow.updated_at == ItemRow.created_at,
                )
            )
        if pristine == n_csv:
            self.template_id = seed_uid  # не удаляется в teardown
            self.template_note = f"шаблон — пользователь {seed_uid} (без повторного импорта)"
            return
        stats = await import_csv(
            csv_path, f"eval-tpl-{uuid.uuid4().hex[:8]}", replace=False, keep_ids=False
        )
        self.template_id = int(stats["user_id"])
        self.users.append(self.template_id)
        self.template_note = "шаблон — свежий импорт CSV"

    async def clone(self) -> tuple[int, dict[int, int]]:
        cols = [
            c.name
            for c in ItemRow.__table__.columns
            if c.name not in ("id", "user_id", "search_tsv", "sort_at")
        ]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        async with get_sessionmaker()() as s, s.begin():
            u = UserRow(name=f"eval-{uuid.uuid4().hex[:10]}")
            s.add(u)
            await s.flush()
            await s.execute(
                text(
                    f"INSERT INTO items (user_id, {col_sql}) SELECT :uid, {col_sql} "
                    f"FROM items WHERE user_id = :tpl AND source = 'import' AND deleted_at IS NULL"
                ),
                {"uid": u.id, "tpl": self.template_id},
            )
            rows = (
                await s.execute(
                    select(ItemRow.id, ItemRow.source_ref).where(
                        ItemRow.user_id == u.id, ItemRow.source_ref.like("csv:%")
                    )
                )
            ).all()
            uid = u.id
        self.users.append(uid)
        return uid, {i: int(ref[4:]) for i, ref in rows}

    async def teardown(self) -> None:
        async with get_sessionmaker()() as s, s.begin():
            await s.execute(delete(ItemRow).where(ItemRow.user_id.in_(self.users)))
            await s.execute(delete(UserRow).where(UserRow.id.in_(self.users)))


# ---------- исполнители ----------


class Runner:
    needs_dialog = True

    async def turn(self, text_: str, state: DialogState, now: datetime) -> AssistantReply:
        raise NotImplementedError


class OrchestratorRunner(Runner):
    def __init__(self, spec: str) -> None:
        mod, _, attr = spec.partition(":")
        self._obj = getattr(importlib.import_module(mod), attr or "orchestrator")
        self._by_user: dict[int, Any] = {}

    async def _get(self, user_id: int) -> Any:
        if hasattr(self._obj, "handle") and not inspect.isclass(self._obj):
            return self._obj  # готовый экземпляр
        if user_id not in self._by_user:
            o = self._obj(user_id) if inspect.signature(self._obj).parameters else self._obj()
            self._by_user[user_id] = await o if inspect.isawaitable(o) else o
        return self._by_user[user_id]

    async def turn(self, text_: str, state: DialogState, now: datetime) -> AssistantReply:
        return await (await self._get(state.user_id)).handle(text_, state, now)


class ContainerRunner(Runner):
    """По умолчанию: app.container.build_container(Settings()) — fake или real из .env.

    STORAGE_IMPL=memory: если SEED_CSV не задан, подставляется CSV организаторов; каждая
    изменяющая строка получает новый контейнер (чистое хранилище в памяти).
    STORAGE_IMPL=postgres: один контейнер, изоляция — клонами пользователя (Sandbox).
    """

    def __init__(self, csv_path: Path) -> None:
        from app.config import Settings

        settings = Settings()
        if settings.storage_impl != "postgres" and not settings.seed_csv:
            settings = settings.model_copy(update={"seed_csv": str(csv_path)})
        self.settings = settings
        self.container: Any = None
        self.rebuild()

    @property
    def postgres(self) -> bool:
        return self.settings.storage_impl == "postgres"

    @property
    def label(self) -> str:
        impl = getattr(self.container, "impl", {}) or {}
        return "container " + ", ".join(f"{k}={v}" for k, v in impl.items())

    def rebuild(self) -> None:
        from app.container import build_container

        self.container = build_container(self.settings)

    async def turn(self, text_: str, state: DialogState, now: datetime) -> AssistantReply:
        reply: AssistantReply = await self.container.orchestrator.handle(text_, state, now)
        return reply


class MemorySandbox:
    """Хранилище в памяти: «чистая среда» = новый контейнер с CSV; id = id из CSV."""

    share_reads = False  # контейнер пересоздаётся на каждую строку — это дёшево

    def __init__(self, runner: ContainerRunner) -> None:
        self.runner = runner
        self.user_id = runner.settings.seed_user_id
        self._fresh = True

    async def setup(self, csv_path: Path) -> None:
        return None

    async def clone(self) -> tuple[int, dict[int, int]]:
        if not self._fresh:
            self.runner.rebuild()
        self._fresh = False
        return self.user_id, {i: i for i in range(1, 1001)}

    async def teardown(self) -> None:
        return None


class HttpRunner(Runner):
    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/")

    async def turn(self, text_: str, state: DialogState, now: datetime) -> AssistantReply:
        body = json.dumps({"text": text_, "session_id": state.session_id}).encode()
        req = urllib.request.Request(
            f"{self._url}/api/chat",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-User-Id": str(state.user_id),
                "X-Freeze-Now": now.isoformat(),
            },
        )
        raw = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=30).read())
        return AssistantReply.model_validate_json(raw)


class SearchRunner(Runner):
    needs_dialog = False

    def __init__(self) -> None:
        from app.storage.search import HybridSearchEngine

        self._eng = HybridSearchEngine()

    async def turn(self, text_: str, state: DialogState, now: datetime) -> AssistantReply:
        res = await self._eng.search(state.user_id, baseline_slots(text_, now), now)
        return AssistantReply(
            text="", action="found", items=[i for i, _ in res], debug={"mode": "search-only"}
        )


# ---------- оценка одной строки ----------

M = {  # ключ → (название, цель, формат)
    "success": ("Сценарий выполнен целиком (task success)", "≥ 90%", "pct"),
    "intent": ("Точность интента", "≥ 95%", "pct"),
    "date": ("Точность даты и времени", "≥ 98%", "pct"),
    "slots": ("Точность слотов (тип, название, длительность, повтор)", "≥ 90%", "pct"),
    "list": ("Списки за период: точный набор", "≥ 95%", "pct"),
    "recall5": ("Recall@5 поиска (все поисковые)", "≥ 90%", "pct"),
    "recall5_crit": ("Recall@5 поиска, q19–q30 (критерий)", "≥ 90%", "pct"),
    "mrr": ("MRR поиска", "≥ 0,8", "float"),
    "top1": ("«Когда…»: верная запись на 1-м месте", "≥ 90%", "pct"),
    "empty": ("Честное «не нашёл»", "100%", "pct"),
    "clarify": ("Уточнение: спросил, когда нужно, и не спросил, когда не нужно", "100%", "pct"),
    "conflict": ("Конфликты: найдены и нет ложных", "100%", "pct"),
    "edit": ("Изменение / выполнено / удаление: верная запись", "≥ 90%", "pct"),
    "action": ("Верное действие (confirm, undo, smalltalk)", "100%", "pct"),
}


@dataclass
class Report:
    metrics: dict[str, Metric] = field(default_factory=dict)
    latency: list[float] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    by_tag: dict[str, list[bool]] = field(default_factory=lambda: defaultdict(list))

    def add(self, key: str, v: float | bool) -> None:
        name, goal, fmt = M[key]
        self.metrics.setdefault(key, Metric(name, goal, fmt=fmt)).add(v)


def _at(item: Any) -> datetime | None:
    return item.starts_at or item.due_at


def _minute(dt: datetime | None) -> str | None:
    return dt.astimezone(DEFAULT_TZ).strftime("%Y-%m-%dT%H:%M") if dt else None


def _iso(s: str) -> str:
    return s.replace(" ", "T")[:16]


def _rrule_norm(r: str | None) -> set[str]:
    return {p.strip().upper() for p in (r or "").removeprefix("RRULE:").split(";") if p.strip()}


def _intent(reply: AssistantReply) -> str | None:
    v = (reply.debug or {}).get("intent")
    return getattr(v, "value", v)


def score(row: dict, reply: AssistantReply, ranked: list[int], rep: Report) -> dict:
    """Проверки строки. ranked — id результатов, переведённые в id CSV (новые записи → -id)."""
    exp, cat, qid = row["expected"], row["category"], row["qid"]
    checks: dict[str, bool] = {}

    def check(key: str, ok: bool) -> None:
        checks[key] = bool(ok)
        rep.add(key, ok)

    if (want := INTENT_MAP.get(exp.get("intent", ""))) and (got := _intent(reply)) is not None:
        check("intent", got == want)

    item = reply.items[0] if reply.items else None
    created = reply.action == "created"

    # --- уточнения ---
    if "missing" in exp:
        need = bool(exp["missing"])
        check("clarify", (reply.action == "clarify") == need and (need or created))
    if exp.get("created") is False:
        check("clarify", not created)
    if cat == "list" and exp.get("ambiguous"):
        check("clarify", reply.action == "clarify")

    # --- создание: дата, слоты, конфликты ---
    creates = exp.get("intent") == "create" and not exp.get("missing")
    if creates and ("conflicts_with_ids" in exp) and exp["conflicts_with_ids"]:
        check(
            "conflict", reply.action == "conflict" and set(exp["conflicts_with_ids"]) <= set(ranked)
        )
    elif creates and "conflicts_with_ids" in exp:
        check("conflict", reply.action != "conflict")
    if creates and not exp.get("conflicts_with_ids") and exp.get("created") is not False:
        it = item if created else None
        if "datetime" in exp and exp["datetime"] is None:
            check("date", it is not None and _at(it) is None)
        elif "datetime" in exp or "due" in exp:
            want = _iso(exp.get("datetime") or exp["due"])
            check("date", it is not None and _minute(_at(it)) == want)
        elif "date" in exp:
            d = _at(it) if it else None
            local = d.astimezone(DEFAULT_TZ) if d else None
            check(
                "date",
                it is not None
                and local is not None
                and local.date().isoformat() == exp["date"]
                and (not it.has_time or local.strftime("%H:%M") == "23:59"),
            )
        if "type" in exp and "title" in exp:
            ok = False
            if it is not None:
                titles = {norm_title(t) for t in [exp["title"], *exp.get("title_accept", [])]}
                ok = it.type.value == exp["type"] and norm_title(it.title) in titles
                if ok and "duration_min" in exp:
                    st, en = it.starts_at, it.ends_at
                    ok = (
                        st is not None
                        and en is not None
                        and (en - st == timedelta(minutes=exp["duration_min"]))
                    )
                if ok and "recurrence" in exp:
                    ok = _rrule_norm(it.rrule) == _rrule_norm(exp["recurrence"])
            check("slots", ok)

    # --- списки ---
    if cat == "list" and not exp.get("ambiguous"):
        got_ids = set(ranked)
        alts = [set(exp["ids"])]
        if m := re.search(r"\[([\d, ]+)\]", exp.get("note", "")):
            alts.append({int(x) for x in m[1].split(",")})
        if not exp["ids"] and "nearest_id" in exp:
            alts.append({exp["nearest_id"]})  # пустой период → предложено ближайшее
        check("list", got_ids in alts)

    # --- поиск ---
    if cat == "search":
        rel = set(exp.get("all_ids") or exp.get("relevant_ids") or [])
        if "answer_id" in exp:
            rel.add(exp["answer_id"])
            check("top1", bool(ranked) and ranked[0] == exp["answer_id"])
        if rel:
            r5 = recall_at_k(ranked, rel, 5)
            rep.add("recall5", r5)
            if qid in CRITERION_QIDS:
                rep.add("recall5_crit", r5)
            rep.add("mrr", reciprocal_rank(ranked, rel))
            checks["recall5"] = r5 >= 0.999
        else:
            check("empty", not ranked)

    # --- изменение / выполнено / удаление ---
    if cat in ("update", "complete", "delete") and exp.get("intent") != "confirm":
        options = exp.get("accept") or [exp]
        oks = []
        for opt in options:
            if opt.get("action") == "clarify" and len(opt) == 1:
                oks.append(reply.action == "clarify")
                continue
            if "target_id" in opt:
                ok = bool(ranked) and ranked[0] == opt["target_id"]
            elif "target" in opt:  # запись, созданная в history: без source_ref
                ok = bool(ranked) and ranked[0] < 0
            else:
                continue
            if exp.get("requires_confirmation"):
                ok = ok and reply.action != "deleted"
            if ok and "new_datetime" in opt:
                ok = _minute(_at(reply.items[0])) == _iso(opt["new_datetime"])
            oks.append(ok)
        if oks:
            check("edit", any(oks))

    # --- действие ---
    if "action" in exp:
        ok = reply.action == exp["action"]
        if ok and "target_id" in exp:
            ok = bool(ranked) and ranked[0] == exp["target_id"]
        check("action", ok)

    success = all(checks.values()) if checks else None
    if success is not None:
        rep.add("success", success)
        for tag in row.get("tags", []):
            rep.by_tag[tag].append(success)
    return {
        "qid": qid,
        "category": cat,
        "query": row["query"],
        "action": reply.action,
        "intent": _intent(reply),
        "top": ranked[:5],
        "checks": checks,
        "success": success,
    }


# ---------- прогон ----------


async def run(golden: list[dict], runner: Runner, sb: Any, only_search: bool) -> Report:
    rep = Report()
    shared: tuple[int, dict[int, int]] | None = None
    for row in golden:
        if only_search and (row["category"] != "search" or row.get("history")):
            continue
        now = datetime.fromisoformat(row["now"])
        if (
            getattr(sb, "share_reads", True)
            and row["category"] in READ_ONLY
            and not row.get("history")
        ):
            shared = shared or await sb.clone()
            uid, to_csv = shared
        else:
            uid, to_csv = await sb.clone()
        state = DialogState(session_id=f"eval-{row['qid']}-{uuid.uuid4().hex[:6]}", user_id=uid)
        err = None
        try:
            for h in row.get("history", []):
                await runner.turn(h, state, now)
            t0 = time.perf_counter()
            reply = await runner.turn(row["query"], state, now)
            rep.latency.append((time.perf_counter() - t0) * 1000)
        except Exception as e:  # строка не должна ронять весь прогон
            reply, err = AssistantReply(text="", action="none"), f"{type(e).__name__}: {e}"
        ranked = [to_csv.get(i.id, -i.id) for i in reply.items]
        res = score(row, reply, ranked, rep)
        if err:
            res["error"] = err
        res["reply"] = reply.text
        rep.rows.append(res)
    return rep


ORDER = [
    "success",
    "intent",
    "date",
    "slots",
    "list",
    "recall5_crit",
    "recall5",
    "mrr",
    "top1",
    "empty",
    "clarify",
    "conflict",
    "edit",
    "action",
]
TAG_ORDER = [
    "tz_original",
    "paraphrase",
    "colloquial",
    "asr_noise",
    "typo",
    "synonym",
    "case_mismatch",
    "edge_date",
    "edge_time",
    "multi_turn",
    "context",
    "conflict",
    "recurrence",
    "empty_period",
    "not_found",
]


def render(rep: Report, target: str, now: str, n_total: int) -> str:
    metrics = [m for k in ORDER if (m := rep.metrics.get(k))]
    out = [
        f"Прогон `{target}` · «сейчас» = {now} · строк оценено: {len(rep.rows)} из {n_total} · "
        f"{datetime.now(DEFAULT_TZ):%Y-%m-%d %H:%M}",
        "",
        markdown_table(metrics, rep.latency, "≤ 1,5 с / ≤ 3 с"),
    ]
    tags = [t for t in TAG_ORDER if t in rep.by_tag] + sorted(
        t for t in rep.by_tag if t not in TAG_ORDER and t != "base"
    )
    if len(tags) > 1:
        out += ["", "| Срез (тег) | Успешно | N |", "|---|---|---|"]
        for t in tags:
            v = rep.by_tag[t]
            out.append(f"| `{t}` | {sum(v) / len(v) * 100:.0f}% | {len(v)} |")
    fails = [r for r in rep.rows if r["success"] is False or r.get("error")]
    if fails:
        out += ["", "<details><summary>Не прошли: " + str(len(fails)) + "</summary>", ""]
        for r in fails:
            bad = [k for k, v in r["checks"].items() if not v]
            out.append(
                f"- **{r['qid']}** «{r['query']}» — {', '.join(bad) or r.get('error')}"
                f" · action={r['action']} · top={r['top']}"
            )
        out += ["", "</details>"]
    return "\n".join(out)


def _importable(spec: str) -> bool:
    try:
        mod, _, attr = spec.partition(":")
        return hasattr(importlib.import_module(mod), attr or "orchestrator")
    except Exception:
        return False


async def amain(a: argparse.Namespace, golden: list[dict]) -> tuple[str, list[dict], int]:
    if a.only:
        golden = [g for g in golden if g["qid"] in set(a.only.split(","))]
    now = golden[0]["now"]
    os.environ.setdefault("FREEZE_NOW", now)
    spec = a.orchestrator or os.getenv("EVAL_ORCHESTRATOR")
    target = a.target
    if target == "auto":
        target = "orchestrator" if spec and _importable(spec) else "container"
    if target == "orchestrator" and not spec:
        raise SystemExit("нужен --orchestrator module:attr или EVAL_ORCHESTRATOR")

    runner: Runner
    sb: Any = Sandbox()
    label = target
    if target == "container":
        cr = ContainerRunner(find_csv(a.csv))
        runner, label = cr, cr.label
        if not cr.postgres:
            sb = MemorySandbox(cr)
    elif target == "orchestrator":
        runner = OrchestratorRunner(spec)  # type: ignore[arg-type]
    elif target == "http":
        runner = HttpRunner(a.url)
    else:
        runner = SearchRunner()

    await sb.setup(find_csv(a.csv))
    try:
        rep = await run(golden, runner, sb, only_search=not runner.needs_dialog)
    finally:
        await sb.teardown()
    target = label

    md = render(rep, target, now, len(golden))
    code = 0
    crit = rep.metrics.get("recall5_crit")
    if a.min_recall_e2e and crit and crit.value < a.min_recall_e2e:
        md += f"\n\n❌ Сквозной Recall@5 q19–q30 = {crit.value:.0%} < {a.min_recall_e2e:.0%}"
        code = 1
    if a.min_recall:
        gate = await search_gate(golden, a.csv) if runner.needs_dialog else crit
        from app.storage.text import lexicon

        if lexicon().source == "none":
            md += (
                "\n\n⚠️ Нет словаря синонимов app/nlu/synonyms.yml (чат 1): поиск работает "
                "без синонимов, Recall@5 ниже. Влейте ветку nlu/v1."
            )
        if gate is None:
            md += "\n\n⚠️ Порог поиска не проверен: PostgreSQL недоступен"
        else:
            ok = gate.value >= a.min_recall
            md += (
                f"\n\n{'✅' if ok else '❌'} Поиск (чат 3, слой данных без NLU, PostgreSQL): "
                f"Recall@5 q19–q30 = {gate.value:.0%} (порог {a.min_recall:.0%}, N={gate.n})"
            )
            code = code or (0 if ok else 1)
    return md, rep.rows, code


async def search_gate(golden: list[dict], csv_arg: str | None) -> Metric | None:
    """Recall@5 на q19–q30 через HybridSearchEngine и базовый разборщик — критерий чата 3.

    Не зависит от того, какой NLU / оркестратор подключён в .env.
    """
    rows = [r for r in golden if r["qid"] in CRITERION_QIDS]
    sb = Sandbox()
    try:
        await sb.setup(find_csv(csv_arg))
    except Exception:
        return None
    try:
        rep = await run(rows, SearchRunner(), sb, only_search=True)
    finally:
        await sb.teardown()
    return rep.metrics.get("recall5_crit")


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.run_eval")
    ap.add_argument("golden", nargs="?", default=str(Path(__file__).with_name("golden_eval.jsonl")))
    ap.add_argument(
        "--target", default="auto", choices=["auto", "container", "orchestrator", "http", "search"]
    )
    ap.add_argument("--orchestrator", help="module:attr, напр. app.dialog.orchestrator:build")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--csv")
    ap.add_argument("--only", help="через запятую: q19,q22")
    ap.add_argument("--out", help="сохранить markdown-отчёт")
    ap.add_argument("--details", help="построчные результаты (jsonl)")
    ap.add_argument(
        "--min-recall",
        type=float,
        default=0.0,
        help="порог Recall@5 на q19–q30 для слоя поиска (без NLU), напр. 0.9",
    )
    ap.add_argument(
        "--min-recall-e2e",
        type=float,
        default=0.0,
        help="порог сквозного Recall@5 q19–q30 (через NLU и оркестратор)",
    )
    a = ap.parse_args()
    golden = [
        json.loads(line)
        for line in Path(a.golden).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    md, rows, code = asyncio.run(amain(a, golden))
    print(md)  # stdout: CI кладёт таблицу в job summary
    if a.out:
        Path(a.out).write_text(md + "\n", encoding="utf-8")
    if a.details:
        Path(a.details).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + "\n",
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
