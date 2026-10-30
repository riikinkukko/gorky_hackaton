"""CLI слоя данных.

python -m app.cli import-csv data/voice_assistant_events_1000.csv --user 1 [--replace]
python -m app.cli reembed [--user demo]
python -m app.cli search "Когда у меня была встреча с Иваном?" --user demo [--now ...]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.contracts import Slots
from app.storage.config import DEFAULT_TZ
from app.storage.db import get_sessionmaker
from app.storage.embeddings import embedding_text, get_embedder
from app.storage.enrich import csv_item
from app.storage.models import ItemRow
from app.storage.ranking import make_fields
from app.storage.repository import SqlItemRepository, fill_search_fields


def read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig снимает BOM: иначе первая колонка называется «﻿id»
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_rows(records: list[dict[str, str]], user_id: int) -> tuple[list[ItemRow], Counter]:
    rows: list[ItemRow] = []
    stats: Counter = Counter()
    for rec in records:
        item, e = csv_item(rec, user_id, DEFAULT_TZ)
        rows.append(
            ItemRow(
                id=item.id,
                user_id=user_id,
                type=item.type.value,
                title=item.title,
                title_original=item.title_original,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                due_at=item.due_at,
                has_time=item.has_time,
                status=item.status.value,
                person=item.person,
                organization=item.organization,
                topic=item.topic,
                object=item.object,
                source=item.source,
                source_ref=f"csv:{rec['id']}",
            )
        )
        stats[item.type.value] += 1
        if e.text_issue:
            stats[f"fixed:{e.text_issue}"] += 1
    return rows, stats


def _embed_batch(rows: list[ItemRow]) -> None:
    emb = get_embedder()
    for r in rows:
        f = make_fields(r.title, r.topic, r.object, r.person, r.organization)
        r.person_norm = list(f.person_norm)
        r.organization_norm = list(f.organization_norm)
        r.search_text = f.search_text
    texts = [embedding_text(r.title, r.topic, r.object, r.person, r.organization) for r in rows]
    for r, v in zip(rows, emb.embed(texts), strict=True):
        r.embedding = v


async def import_csv(
    path: Path, user: str, replace: bool, keep_ids: bool, sm: async_sessionmaker | None = None
) -> Counter:
    sm = sm or get_sessionmaker()
    repo = SqlItemRepository(sm)
    user_id = await repo.ensure_user(user)
    records = read_csv(path)
    if not replace:
        # Быстрый путь для повторного старта API: уже загруженные строки не обогащаем заново.
        async with sm() as s:
            have = set(
                (
                    await s.scalars(
                        select(ItemRow.source_ref).where(
                            ItemRow.user_id == user_id, ItemRow.source_ref.is_not(None)
                        )
                    )
                ).all()
            )
        missing = [r for r in records if f"csv:{r['id']}" not in have]
        if not missing:
            return Counter(skipped_existing=len(records), inserted=0, user_id=user_id)
        records = missing
    rows, stats = build_rows(records, user_id)
    _embed_batch(rows)
    async with sm() as s, s.begin():
        if replace:
            await s.execute(
                delete(ItemRow).where(ItemRow.user_id == user_id, ItemRow.source == "import")
            )
        existing = set(
            (
                await s.scalars(
                    select(ItemRow.source_ref).where(
                        ItemRow.user_id == user_id, ItemRow.source_ref.is_not(None)
                    )
                )
            ).all()
        )
        rows = [r for r in rows if r.source_ref not in existing]
        stats["skipped_existing"] = len(existing & {f"csv:{r['id']}" for r in read_csv(path)})
        taken = (
            set(
                (
                    await s.scalars(select(ItemRow.id).where(ItemRow.id.in_([r.id for r in rows])))
                ).all()
            )
            if keep_ids
            else set()
        )
        if not keep_ids or taken:
            # id из CSV заняты (второй пользователь) — даём БД выдать новые; связь через source_ref
            for r in rows:
                r.id = None  # type: ignore[assignment]
            stats["ids_reassigned"] = len(rows)
        s.add_all(rows)
        await s.flush()
        await s.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('items','id'), "
                "GREATEST((SELECT max(id) FROM items), 1))"
            )
        )
    stats["inserted"] = len(rows)
    stats["user_id"] = user_id
    return stats


async def reembed(user: str | None) -> int:
    sm = get_sessionmaker()
    emb = get_embedder()
    uid = await _user_id(user) if user else None
    async with sm() as s, s.begin():
        q = select(ItemRow) if uid is None else select(ItemRow).where(ItemRow.user_id == uid)
        rows = (await s.scalars(q)).all()
        for r in rows:
            fill_search_fields(r, emb)
    return len(rows)


async def _user_id(name: str) -> int:
    return await SqlItemRepository().ensure_user(name)


async def search_cmd(query: str, user: str, now: datetime, tense: str) -> None:
    from app.storage.search import HybridSearchEngine

    uid = await _user_id(user)
    res = await HybridSearchEngine().search(uid, Slots(query=query, tense=tense), now)
    for item, score in res:
        at = item.starts_at or item.due_at
        print(f"{score:6.3f}  #{item.id:<5} {at:%Y-%m-%d %H:%M}  [{item.type}] {item.title}")
    if not res:
        print("ничего не найдено")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import-csv", help="импорт CSV организаторов")
    p.add_argument("path", type=Path)
    p.add_argument("--user", default="1", help="id пользователя (1 — демо) или имя")
    p.add_argument("--replace", action="store_true", help="удалить прежний импорт пользователя")
    p.add_argument("--new-ids", action="store_true", help="не сохранять id из CSV")
    p = sub.add_parser("reembed", help="пересчитать поисковые поля и эмбеддинги")
    p.add_argument("--user", help="id или имя; по умолчанию все пользователи")
    p = sub.add_parser("search", help="отладка поиска")
    p.add_argument("query")
    p.add_argument("--user", default="1")
    p.add_argument("--now", default=None)
    p.add_argument("--tense", default="any", choices=["past", "future", "any"])
    a = ap.parse_args(argv)

    if a.cmd == "import-csv":
        stats = asyncio.run(import_csv(a.path, a.user, a.replace, not a.new_ids))
        print(json.dumps(dict(stats), ensure_ascii=False, indent=1))
    elif a.cmd == "reembed":
        print(f"обновлено записей: {asyncio.run(reembed(a.user))}")
    elif a.cmd == "search":
        now = datetime.fromisoformat(a.now) if a.now else datetime.now(DEFAULT_TZ)
        asyncio.run(search_cmd(a.query, a.user, now, a.tense))
    return 0


if __name__ == "__main__":
    sys.exit(main())
