"""Форматирование дат по-русски: «чт, 22 октября, 10:00»."""

from __future__ import annotations

from datetime import datetime

from app.contracts import Item

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_GEN = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def human_dt(dt: datetime, with_time: bool = True, now: datetime | None = None) -> str:
    s = f"{WEEKDAYS[dt.weekday()]}, {dt.day} {MONTHS_GEN[dt.month - 1]}"
    if now is None or dt.year != now.year:
        s += f" {dt.year}"
    if with_time:
        s += f", {dt:%H:%M}"
    return s


def item_line(item: Item, now: datetime | None = None) -> str:
    when = item.starts_at or item.due_at
    return f"{item.title} — {human_dt(when, item.has_time, now)}" if when else item.title


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
