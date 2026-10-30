"""Короткие конструкторы сценариев. «Сейчас» — пт 23.10.2026 15:00 (МСК).

Календарь: сб 24, вс 25, пн 26, вт 27, ср 28, чт 29, пт 30, сб 31.10, вс 1.11.
Эта неделя 19–25.10, следующая 26.10–1.11, через одну 2–8.11.
"""
CREATE, LIST, SEARCH = "create_item", "list_items", "search_items"
UPDATE, COMPLETE, DELETE, UNDO = "update_item", "complete_item", "delete_item", "undo"
UNKNOWN_SMALL = ["unknown", "smalltalk"]
NOT_CHANGE = ["create", "update", "delete", "complete"]


def S(axis, turns, *, note=None, target=None, skip_csv_check=False, freeze_now=None, **expect):
    """expect: intent, action, type, date, time, period, duration_min, title_contains (str|list),
    title_not, tense, must_not."""
    if "title" in expect:
        t = expect.pop("title")
        expect["title_contains"] = [t] if isinstance(t, str) else t
    if isinstance(expect.get("title_not"), str):
        expect["title_not"] = [expect["title_not"]]
    expect = {k: v for k, v in expect.items() if v is not None}
    sc = {"axis": axis, "turns": turns, "expect": expect}
    if note:
        sc["note"] = note
    if target:
        sc["target"] = target
    if skip_csv_check:
        sc["skip_csv_check"] = True
    if freeze_now:
        sc["freeze_now"] = freeze_now
    return sc


def _creates(action) -> bool:
    return "created" in (action if isinstance(action, (list, tuple)) else [action])


def task(axis, turns, action="created", **kw):
    if _creates(action):
        kw.setdefault("intent", CREATE)
    kw.setdefault("type", "task")
    return S(axis, turns, action=action, **kw)


def remind(axis, turns, action="created", **kw):
    if _creates(action):
        kw.setdefault("intent", CREATE)
    kw.setdefault("type", "reminder")
    return S(axis, turns, action=action, **kw)


def meet(axis, turns, action="created", **kw):
    if _creates(action):
        kw.setdefault("intent", CREATE)
    kw.setdefault("type", "meeting")
    return S(axis, turns, action=action, **kw)


def lst(axis, turns, action="listed", **kw):
    kw.setdefault("intent", LIST)
    kw.setdefault("must_not", ["create"])
    return S(axis, turns, action=action, **kw)


def find(axis, turns, action="found", **kw):
    kw.setdefault("intent", SEARCH)
    kw.setdefault("must_not", ["create"])
    return S(axis, turns, action=action, **kw)


def junk(axis, turns, action=("none", "clarify"), must_not=("create",), **kw):
    """Бессмыслица / не по теме: переспрос или отказ, ничего не записывать."""
    return S(axis, turns, action=list(action), must_not=list(must_not), **kw)
