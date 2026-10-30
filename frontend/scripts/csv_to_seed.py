"""CSV организаторов → seed.json для мок-API фронтенда.

Тип выводится по шаблону так же, как в разделе 2 документа 01.
Нормализация падежей — зона чата 3; здесь title = исходный текст.
"""
import csv, json, re, sys
from datetime import datetime, timedelta

src, dst = sys.argv[1], sys.argv[2]
MEETING = re.compile(r"^(Встреча|Созвон|Демо|Провести планёрку|Провести планерку)", re.I)
REMIND = re.compile(r"^Напомнить", re.I)
items = []
with open(src, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        t = r["text"].strip()
        typ = "meeting" if MEETING.match(t) else "reminder" if REMIND.match(t) else "task"
        start = datetime.fromisoformat(f"{r['date']}T{r['time']}:00")
        dur = 30 if t.lower().startswith("демо") else 60
        items.append({
            "id": int(r["id"]), "type": typ, "title": t,
            "starts_at": start.isoformat() + "+03:00" if typ == "meeting" else None,
            "ends_at": (start + timedelta(minutes=dur)).isoformat() + "+03:00" if typ == "meeting" else None,
            "due_at": start.isoformat() + "+03:00" if typ != "meeting" else None,
        })
json.dump(items, open(dst, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(len(items), "items →", dst)
