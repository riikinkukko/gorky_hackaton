"""Паузы и звуки-заминки в распознанной речи: «Э-э… покажи встречи», «Напомни … мне».

Убираются ТОЛЬКО незначащие звуки (э-э, эм, мм, хм) и знаки паузы (…, ...).
Слова-паразиты («ну», «короче», «типа») не трогаем — их нормализует NLU чата 1,
и они бывают значимыми («ну да» — согласие).
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

from app.contracts import TimeSpec

_PAUSE = re.compile(r"\s*(?:…|\.{2,})\s*")
# «э», «э-э» — заминка где угодно. «эм», «мм», «хм», «а-а» — только в начале фразы или между
# паузами («напомни, хм, завтра»): иначе это буквы аббревиатуры («си ар эм, пожалуйста» = CRM).
_HESITATION = re.compile(r"(?<![\w-])э+(?:-э+)*(?![\w-])[,.!?;:]*", re.IGNORECASE)
_HESITATION_SOFT = re.compile(
    r"(?:(?<=,\s)|(?<=,)|(?<=…\s)|(?<=…))(?:эм+|м{2,}|хм+|а-а+)(?:,|…|\.{2,})", re.IGNORECASE
)
_LEADING_SOFT = re.compile(r"^\s*(?:эм+|м{2,}|хм+|а-а+)(?![\w-])[,.!?;:…]*", re.IGNORECASE)
_SPACES = re.compile(r"\s{2,}")
_EDGE = re.compile(r"^[\s,;:—–-]+|[\s,;:—–-]+$")


def strip_hesitations(text: str) -> str:
    """«Э-э… покажи все встречи в ноябре» → «покажи все встречи в ноябре».

    Если после очистки ничего не осталось — возвращаем исходный текст (пусть решит NLU).
    """
    t = _LEADING_SOFT.sub(" ", text)
    t = _HESITATION_SOFT.sub(" ", t)
    t = _HESITATION.sub(" ", t)
    t = _PAUSE.sub(" ", t)
    t = _SPACES.sub(" ", t)
    t = _EDGE.sub("", t)
    return t or text


# служебное начало и конец названия: «Нам сдать тестовое, напомни пж» → «Сдать тестовое»
_TITLE_LEAD = re.compile(
    r"^(?:(?:мне|нам|задачу|задача|нужно|надо|необходимо|будет|бы|пожалуйста|пж|плиз)\s+)+",
    re.IGNORECASE,
)
_TITLE_TAIL = re.compile(
    r"[\s,]*(?:(?:напомни(?:те)?|запиши|поставь|добавь)(?:\s+(?:мне|нам|об\s+этом|это))?)?"
    r"(?:[\s,]+(?:пж|пжл|пожалуйста|плиз|спасибо))*[\s,.!?]*$",
    re.IGNORECASE,
)
_POLITE = re.compile(r"(?<![\w-])(?:пж|пжл|пожалуйста|плиз)(?![\w-])", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Название записи без пауз, заминок и служебных слов:
    «… мне позвонить Ивану» → «Позвонить Ивану»,
    «Нам сдать тестовое на хакатон , напомни пж» → «Сдать тестовое на хакатон»."""
    t = strip_hesitations(title)
    t = _POLITE.sub(" ", t)
    t = _TITLE_TAIL.sub("", t)
    t = _TITLE_LEAD.sub("", t.strip())
    t = re.sub(r"\s+([,.!?])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.;:—-")
    if re.match(r"^встречу\b", t, re.IGNORECASE):
        t = "Встреча" + t[len("встречу") :]
    return t[:1].upper() + t[1:] if t else title


# ---------- содержательность названия и «чистые» ответы со временем ----------

_EMPTY_WORDS = set(
    "что это как когда где куда зачем почему кто ну а и да нет так вот тут там потом ещё еще "
    "уже же ли бы то не мне нам меня мой моя мои пж пожалуйста плиз давай давайте ладно хорошо ок "
    "окей угу напомни напомнить запиши записать поставь поставить добавь добавить нужно надо "
    "сделай сделать задача задачу напоминание запись дело дела".split()
    # «Встреча» — нормальное название встречи по умолчанию, поэтому её здесь нет
)
_TIME_WORDS = set(
    "в во на к до с со по через после около утра утром дня днём днем вечера вечером ночи ночью "
    "сегодня завтра послезавтра вчера позавчера понедельник вторник среда среду четверг пятница "
    "пятницу суббота субботу воскресенье неделе неделю недели неделя месяц месяца месяце месяцу "
    "день дня числа число последний последнего последнюю последнее первый первого первое "
    "конец конце начале начало следующий следующей следующего следующую этот эту этой этого "
    "час часа часов часу минут минуты полчаса половина половине полдень полночь обед обеда "
    "раз ровно примерно часиков январь февраль март апрель май июнь июль август сентябрь "
    "октябрь ноябрь декабрь января февраля марта апреля мая июня июля августа сентября "
    "октября ноября декабря один два три четыре пять шесть семь восемь девять десять "
    "одиннадцать двенадцать".split()
)
_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in _WORD.findall(text)]


def meaningful(title: str | None) -> bool:
    """Есть ли в названии содержание: «Что», «Ну это», «В пятницу» — нет."""
    empty = {w.replace("ё", "е") for w in _EMPTY_WORDS | _TIME_WORDS}
    return any(w not in empty and not w.isdigit() and len(w) > 1 for w in _words(title or ""))


def only_time_words(text: str) -> bool:
    """Ответ на «Когда?» без содержания: «в последний день месяца», «завтра в 10»."""
    words = _words(strip_hesitations(text))
    allowed = {w.replace("ё", "е") for w in _TIME_WORDS} | {
        "ну", "давай", "наверное", "пж", "без", "четверти", "пятнадцати", "двадцати",
    }  # fmt: skip
    return bool(words) and all(
        w in allowed or w.isdigit() or _HALF.fullmatch(w) is not None for w in words
    )


# ---------- даты, которые пока не понимает DateResolver ----------

_MONTH_END = re.compile(r"(?:в\s+)?(?:последний\s+день|конец|конце)\s+(?:этого\s+)?месяца")
_NEXT_MONTH_START = re.compile(
    r"(?:в\s+)?(?:первый\s+день|первое\s+число|начало|начале)\s+следующего\s+месяца"
)


def relative_date(text: str, now: datetime) -> tuple[TimeSpec, str] | None:
    """«в последний день месяца» → (31 октября, найденная фраза). Запасной путь, пока
    DateResolver чата 1 отдаёт для таких фраз ambiguous без даты."""
    low = text.lower().replace("ё", "е")
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if m := _MONTH_END.search(low):
        last = calendar.monthrange(now.year, now.month)[1]
        return TimeSpec(text=m.group(0), start=day0.replace(day=last), has_date=True), m.group(0)
    if m := _NEXT_MONTH_START.search(low):
        nxt = (day0.replace(day=28) + timedelta(days=4)).replace(day=1)
        return TimeSpec(text=m.group(0), start=nxt, has_date=True), m.group(0)
    return None


def strip_phrase(title: str, phrase: str) -> str:
    """Убрать из названия найденную фразу даты (без учёта регистра и «ё»)."""
    low = title.lower().replace("ё", "е")
    i = low.find(phrase)
    if i < 0:
        return title
    return clean_title((title[:i] + " " + title[i + len(phrase) :]).strip())


# ---------- страховка названия перед созданием (не зависит от NLU) ----------

# служебное начало: «можно встречу…», «поставь туда…», «запишите пожалуйста…», «ёлки палки…»
_GUARD_LEAD = re.compile(
    r"^(?:(?:можно|давай(?:те)?|туда|сюда|это|ну|короче|типа|слушай|так|вот|а|и|ой|ммм?|э+|"
    r"блин|бля\w*|ёлки(?:\s+палки)?|елки(?:\s+палки)?|хрен\s+знает\s+зачем(?:\s+но)?|"
    r"чуть\s+не\s+забыл[аи]?|не\s+забудь(?:те)?|(?:надо\s+)?(?:бы\s+)?не\s+забыть|"
    r"(?:опять\s+)?забыл[аи]?|(?:на\s+)?этот\s+день|в\s+этот\s+день|"
    r"запиши(?:те)?|поставь(?:те)?|добавь(?:те)?|"
    r"сделай(?:те)?|создай(?:те)?|запланируй(?:те)?|ещё|еще|пожалуйста|плиз|пж)[\s,]+)+",
    re.IGNORECASE,
)
# хвост-паразит: «…ну ты понял», «…короче», «…в общем»
_GUARD_TAIL = re.compile(
    r"[\s,]+(?:ну\s+)?(?:ты\s+понял[аи]?|короче|в\s+общем|вообще|как\s+бы|типа)[\s,.!?]*$",
    re.IGNORECASE,
)
# «с этими как их Вектором» → «с Вектором»
_AS_THEM = re.compile(r"\s(?:эти(?:ми|х|м)?\s+)?как\s+(?:их|его|её|ее)(?=\s)", re.IGNORECASE)
_MAT = re.compile(
    r"(?<![а-яё])(?:бля\w*|хуй\w*|хуе\w*|пизд\w*|ебан\w*|ёбан\w*|сука|нахуй)(?![а-яё])",
    re.IGNORECASE,
)
_QUESTION_ONLY = re.compile(
    r"^(?:что|чё|че|чо|кие|какие|какая|какой|какое|когда|где|кто|сколько|зачем)$", re.IGNORECASE
)
# «Нет не», «Не надо» — отказ; «Не забыть оплатить» — нормальное дело (срезается ниже)
_SERVICE_START = re.compile(r"^(?:нет\b|не\s+(?:надо|нужно|стоит)\b|не$)", re.IGNORECASE)
_DANGLING = re.compile(r"\s(?:с|со|в|во|на|для|по|к|у|о|об|и|или|про)$", re.IGNORECASE)
# дата и время, которые NLU не вынул из названия: время в названии значит, что разбор неверен
_DATE_WORDS = re.compile(
    r"(?<![а-яё])(?:часов|часа|час|утра|вечера|ночи|дня|завтра|сегодня|послезавтра|вчера|через|"
    r"понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|"
    r"января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"первое|второе|третье|двадцатое|тридцатое|тридцать\s+первое|числа)(?![а-яё])"
    r"|(?<!\d)[0-2]?\d[:.][0-5]\d",
    re.IGNORECASE,
)
_EDGE_TIME = re.compile(
    r"^(?:(?:с|в|на|до|к)\s+)?[0-2]?\d[:.][0-5]\d(?:\s+(?:до|-|–)\s+[0-2]?\d[:.][0-5]\d)?\s+"
    r"|\s+(?:(?:с|в|на|до|к)\s+)?[0-2]?\d[:.][0-5]\d(?:\s+(?:до|-|–)\s+[0-2]?\d[:.][0-5]\d)?$",
    re.IGNORECASE,
)
_FILLER_ONLY = set(
    "один одна одну одно такую такой такое такая такие же ещё еще не об обо этом это то "
    "самое самый тоже".split()
)  # «одну такую же», «нет не», «об этом», «то же самое» — ссылка без контекста, не название


def guard_title(title: str | None) -> str | None:
    """Название, которое можно записать, или None — тогда не создаём, а переспрашиваем.

    Срезаем служебное начало и хвост («можно встречу с командой» → «Встреча с командой»).
    Отказываем, если после этого осталось пустое, слово-вопрос («что», «чё», «кие»),
    служебное «нет …», мат, дата или время, обрывок («встреча с») или повтор
    («встреча встреча встреча»).
    """
    if not title or not title.strip():
        return None
    # «13:30 тренировка по волейболу», «Встреча 14:40»: время с краю — остаток от разбора
    # времени, а не часть названия; срезаем (время в середине — по-прежнему отказ)
    t = _EDGE_TIME.sub("", _CREATE_TAIL.sub("", title.strip())).strip()
    t = clean_title(t) if t else t
    if not t:
        return None
    if _SERVICE_START.match(t.strip()):
        return None  # «Нет не», «Не надо» — это отказ, а не дело
    for _ in range(3):  # служебные слова бывают цепочкой: «ну это сделай …»
        t2 = _GUARD_LEAD.sub("", t.strip() + " ").strip()  # «Туда», «Можно» целиком — тоже
        if t2 == t:
            break
        t = t2
    t = _GUARD_TAIL.sub("", t)
    t = _AS_THEM.sub("", f" {t} ").strip()
    t = clean_title(t) if t else t
    low = t.lower().strip(" .,!?")
    words = _words(low)
    if not words or _QUESTION_ONLY.match(low) or _MAT.search(low) or _DATE_WORDS.search(low):
        return None
    if _DANGLING.search(f" {low}") or (len(words) > 1 and len(set(words)) == 1):
        return None
    if all(w in _FILLER_ONLY for w in words) or not meaningful(t):
        return None
    return t


# ---------- разговорное время и самоисправления (запасной путь, пока их не знает NLU) ----------

_ORD_GEN = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6,
    "седьмого": 7, "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11,
    "двенадцатого": 12,
}  # fmt: skip
_CARD = {
    "час": 1, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
    "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
}  # fmt: skip
_HALF = re.compile(r"(?<![а-я])пол\s?-?(" + "|".join(_ORD_GEN) + r")(?![а-я])")
_QUARTER_TO = re.compile(r"без\s+четверти\s+(" + "|".join(_CARD) + r"|\d{1,2})(?![а-я\d])")


def _pm(hour: int, low: str) -> int:
    """Политика golden: 1–7 без «утра» — после полудня."""
    if "утра" in low or "ночи" in low:
        return hour % 12
    return hour + 12 if 1 <= hour <= 7 else hour


def colloquial_time(text: str, now: datetime) -> TimeSpec | None:
    """«полчетвёртого» → 15:30, «без четверти двенадцать» → 11:45 (сегодняшняя дата,
    has_date=False — дату берёт тот, кто знает контекст)."""
    low = text.lower().replace("ё", "е")
    hour = minute = None
    if m := _HALF.search(low):
        hour, minute = _pm(_ORD_GEN[m.group(1)] - 1 or 12, low), 30
    elif m := _QUARTER_TO.search(low):
        n = int(m.group(1)) if m.group(1).isdigit() else _CARD[m.group(1)]
        hour, minute = _pm((n - 1) or 12, low), 45
    if hour is None or minute is None:
        return None
    start = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    return TimeSpec(text=m.group(0) if m else text, start=start, has_time=True)


# «в среду, ой нет, в четверг», «в три нет в четыре», «то есть», «хотя нет», «или нет давай»
CORRECTION = re.compile(
    r"(?<![а-я])(?:ой\s+нет|ой|а\s+нет|или\s+нет(?:\s+давай)?|хотя\s+нет|нет|то\s+есть|"
    r"в\s+смысле|точнее)(?![а-я])"
)


def after_correction(text: str) -> str | None:
    """Часть фразы после последнего самоисправления или None, если его нет."""
    low = text.lower().replace("ё", "е")
    parts = CORRECTION.split(low)
    if len(parts) < 2:
        return None
    tail = parts[-1].strip(" ,.")
    return tail or None


def cut_correction(title: str) -> str:
    """«Встреча с Иваном ой нет в четверг» → «Встреча с Иваном»; «Встреча с Олегом то есть
    с Игорем» → «Встреча с Игорем» (исправлено лицо, а не время)."""
    low = title.lower().replace("ё", "е")
    marks = list(CORRECTION.finditer(low))
    if not marks:
        return title
    first, m = marks[0], marks[-1]
    head, tail = title[: m.start()].strip(" ,"), title[m.end() :].strip(" ,")
    if not tail or only_time_words(tail):
        # исправлено время: «Созвон с Вектором ой в смысле в среду» → «Созвон с Вектором»
        return title[: first.start()].strip(" ,") or title
    # «с Олегом то есть с Игорем»: хвост начинается с предлога — заменяем последний оборот
    words = head.split()
    if tail.split()[0].lower() in ("с", "со", "для", "у", "к") and len(words) >= 2:
        for i in range(len(words) - 1, -1, -1):
            if words[i].lower() == tail.split()[0].lower():
                return " ".join([*words[:i], tail])
    return title  # «Проверить, нет ли ошибок» — не исправление, название не трогаем


# обрывок глагола создания в начале: «ставь встречу», «бавь встречу» (ASR съел «по-», «до-»)
_CREATE_TAIL = re.compile(
    r"^(?:ставь|тавь|бавь|обавь|апиши|апланируй|ланируй|оздай|здай)\s+", re.IGNORECASE
)
