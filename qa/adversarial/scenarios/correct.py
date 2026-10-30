"""Поправить: перенос, удаление, «готово», повтор, «её» из контекста. «Сейчас» — пт 23.10.2026 15:00.
Цели — будущие записи CSV пользователя 1 (target: корни + где запись сейчас); новые дата и время,
если перенос «только даты» или сдвиг, build.py считает от времени цели.
Удаление — с подтверждением, поэтому ожидается clarify (или deleted, если подтверждение уже было)."""
from scenarios._h import *  # noqa: F403

DEL = ["clarify", "deleted"]


def tg(stems, date=None, **new):
    t = {"stems": stems}
    if date:
        t["date"] = date
    t.update(new)
    return t


def U(axis, turns, target=None, action="updated", **kw):
    kw.setdefault("intent", UPDATE)
    kw.setdefault("must_not", ["create"])
    return S(axis, turns, target=target, action=action, **kw)


def C(axis, turns, target=None, action="completed", **kw):
    kw.setdefault("intent", COMPLETE)
    kw.setdefault("must_not", ["create", "delete"])
    return S(axis, turns, target=target, action=action, **kw)


def D(axis, turns, target=None, action=None, **kw):
    kw.setdefault("intent", DELETE)
    kw.setdefault("must_not", ["create", "complete", "undo"])
    return S(axis, turns, target=target, action=action or DEL, **kw)


ORB = ["созвон", "орбит"]          # чт 29.10 15:30
MAR = ["встреча", "мари"]          # сб 24.10 20:45
SMETA = ["смет", "мобил"]          # сб 24.10 10:45
ALFA = ["созвон", "альф"]          # вс 01.11 15:15
REP = ["отчёт", "согласован"]      # чт 29.10 15:30
ANNA = ["позвонить", "анн"]        # вт 03.11 16:30
AKT_AN = ["акт", "аналит"]         # пн 26.10 20:30
MAKS = ["итоги", "максим"]         # пт 30.10 20:30
NAT = ["итоги", "натал"]           # пн 02.11 09:30
PRES = ["материалы", "презентац"]  # вс 25.10 13:15
MOB = ["материалы", "мобил"]       # ср 04.11 11:45
START = ["старт"]                  # чт 05.11 20:00
PROT = ["протокол", "елен"]        # сб 24.10 14:15

H_IVAN = "Напомни завтра в десять позвонить Ивану"
H_OLEG = "Поставь встречу с Олегом завтра в 15:00"

SCENARIOS = [
    # ---------- порядок слов
    U("word_order", "созвон с Орбитой на шестнадцать тридцать перенеси", tg(ORB, new_time="16:30")),
    U("word_order", "на воскресенье встречу с Марией сдвинь", tg(MAR, new_date="2026-10-25")),
    C("word_order", "смету по мобильному отметь проверил", tg(SMETA)),
    D("word_order", "первого ноября созвон с Альфой удали", tg(ALFA, "2026-11-01")),
    U("word_order", "отчёт на согласование перенеси на пятницу", tg(REP), action=["updated", "clarify"],
      time="15:30", note="v2: сегодня пятница и 15:30 ещё не прошло — по политике дня недели это сегодня, "
                         "по смыслу — 30.10; дату не проверяем"),
    U("word_order", "Анне позвонить в среду перенеси", tg(ANNA), action=["updated", "clarify"], time="16:30",
      note="звонок во вт 03.11: «в среду» = 28.10 или 04.11 — дату не проверяем"),
    C("word_order", "готово акт по аналитике", tg(AKT_AN)),
    U("word_order", "встречу с Марией в субботу на семь вечера перенеси", tg(MAR, "2026-10-24", new_time="19:00")),
    D("word_order", "итоги Максиму отправить удали", tg(MAKS)),
    U("word_order", "на час позже сдвинь созвон с Альфой", tg(ALFA, shift_min=60)),

    # ---------- разговорное время
    U("colloquial_time", "перенеси встречу с Марией на полвосьмого вечера", tg(MAR, new_time="19:30")),
    U("colloquial_time", "созвон с Орбитой перекинь на после обеда", tg(ORB), action=["updated", "clarify"],
      date="2026-10-29", note="«после обеда» — время не проверяем"),
    U("colloquial_time", "сдвинь звонок Анне на пару дней", tg(ANNA, shift_min=2 * 24 * 60),
      action=["updated", "clarify"]),
    U("colloquial_time", "перенеси смету на после праздников", tg(SMETA), action=["clarify", "updated"],
      note="дату не предсказать — лучше спросить"),
    U("colloquial_time", "перенеси созвон с Альфой на полчаса пораньше", tg(ALFA, shift_min=-30)),
    U("colloquial_time", "встречу с Марией давай на без четверти восемь вечера", tg(MAR), intent=[UPDATE, CREATE],
      action=["updated", "created", "clarify", "conflict"], time="19:45", must_not=["delete"],
      note="v2: без глагола переноса это и правка встречи 24.10, и новая встреча сегодня в 19:45"),
    U("colloquial_time", "перенеси отчёт на согласование на следующую пятницу", tg(REP, new_date="2026-10-30"),
      note="сегодня пятница: «следующая пятница» = 30.10 (политика «следующая среда» в среду)"),
    U("colloquial_time", "перенеси материалы по презентации на понедельник с утра",
      tg(PRES, new_date="2026-10-26", new_time="09:00")),
    U("colloquial_time", "сдвинь проверку сроков по хакатону на конец недели", tg(["сроки", "хакатон"]),
      action=["clarify", "updated"], note="две проверки сроков (30.10 и 07.11) и размытая дата"),
    U("colloquial_time", "отложи звонок Анне на недельку", tg(ANNA, shift_min=7 * 24 * 60),
      action=["updated", "clarify"]),

    # ---------- обрезки ASR
    U("asr_cut", "реноси созвон с орбитой на шестнадцать тридцать", tg(ORB, new_time="16:30"),
      action=["updated", "clarify"], note="обрезано «пе-»"),
    S("asr_cut", "али напоминание про смету", action=["clarify", "none"], must_not=["complete", "create", "update"],
      note="«удали» без «уд-»: удалить только после подтверждения"),
    C("asr_cut", "меть смету выполненной", tg(SMETA), action=["completed", "clarify"]),
    U("asr_cut", "двинь встречу с марией на час позже", tg(MAR, shift_min=60), action=["updated", "clarify"]),
    S("asr_cut", "дали созвон с альфой", action=["clarify", "none"], must_not=["complete", "create", "update"],
      note="как L19, но с описанием цели: ничего не менять без подтверждения"),
    C("asr_cut", "отово смета", tg(SMETA), action=["completed", "clarify"]),
    S("asr_cut", "мени последнее", action=["undone", "clarify", "none"], must_not=["create", "delete"]),
    U("asr_cut", "перенеси созвон с орбитой на шестнадцать тридцать", tg(ORB, new_time="16:30")),
    U("asr_cut", "кинь встречу с марией на воскресенье", tg(MAR), intent=[UPDATE, CREATE], action=["updated", "clarify"],
      date="2026-10-25", note="v2: «перекинь» или «закинь» без начала — перенос или новая встреча без времени"),
    C("asr_cut", "отметь что позвонил анне", tg(ANNA)),

    # ---------- числа словами
    U("numbers_words", "перенеси созвон с Орбитой на семнадцать ноль ноль", tg(ORB, new_time="17:00")),
    U("numbers_words", "перенеси встречу с Марией на двадцать пятое на восемнадцать тридцать",
      tg(MAR, new_date="2026-10-25", new_time="18:30")),
    U("numbers_words", "перенеси звонок Анне на четвёртое ноября в десять",
      tg(ANNA, new_date="2026-11-04", new_time="10:00")),
    U("numbers_words", "сдвинь созвон с Альфой на два часа позже", tg(ALFA, shift_min=120)),
    U("numbers_words", "перенеси подготовку к встрече со Стартом на шестое в девятнадцать",
      tg(START, new_date="2026-11-06", new_time="19:00")),
    D("numbers_words", "удали задачу на первое ноября подготовить акт", tg(["подготовить", "акт"], "2026-11-01")),
    U("numbers_words", "перенеси материалы по мобильному приложению с четвёртого на пятое",
      tg(MOB, "2026-11-04", new_date="2026-11-05")),
    U("numbers_words", "перенеси итоги Наталье на девять сорок пять", tg(NAT, new_time="09:45")),
    U("numbers_words", "сдвинь отчёт на согласование на сорок пять минут раньше", tg(REP, shift_min=-45)),
    U("numbers_words", "перенеси созвон с Орбитой на тридцатое на одиннадцать тридцать",
      tg(ORB, new_date="2026-10-30", new_time="11:30")),

    # ---------- паразиты, сленг, мат
    U("fillers_slang", "короче перекинь созвон с Орбитой на четыре", tg(ORB, new_time="16:00")),
    C("fillers_slang", "бля смету проверил отметь", tg(SMETA)),
    D("fillers_slang", "ну это самое убери нахрен звонок Анне", tg(ANNA)),
    U("fillers_slang", "слышь двинь встречу с Марией на часик попозже", tg(MAR, shift_min=60)),
    C("fillers_slang", "ёпт готово с актом по аналитике", tg(AKT_AN)),
    D("fillers_slang", "кароч этот созвон с Альфой отменяется", tg(ALFA),
      note="«отменяется» = удалить запись, а не undo"),
    U("fillers_slang", "э-э ну типа перенеси Старт на шестое", tg(START, new_date="2026-11-06"),
      action=["updated", "clarify"]),
    D("fillers_slang", "хрен с ним удали протокол Елене", tg(PROT)),
    C("fillers_slang", "всё сделал с итогами для Максима", tg(MAKS)),
    U("fillers_slang", "чё-то не успеваю двинь отчёт на понедельник", tg(REP, new_date="2026-10-26")),

    # ---------- самопоправки
    U("self_correction", "перенеси встречу с Марией на воскресенье ой нет на понедельник",
      tg(MAR, new_date="2026-10-26")),
    D("self_correction", "удали созвон с Альфой то есть с Орбитой", tg(ORB)),
    U("self_correction", "перенеси звонок Анне на десять нет на одиннадцать", tg(ANNA, new_time="11:00")),
    C("self_correction", "отметь смету выполненной а нет подожди акт", tg(["акт"]),
      note="«акт» — два кандидата (26.10 и 01.11)"),
    U("self_correction", "сдвинь созвон с Орбитой на час позже хотя нет на два", tg(ORB, shift_min=120)),
    U("self_correction", "перенеси отчёт на согласование на пятницу точнее на четверг следующей недели",
      tg(REP), action=["updated", "clarify"],
      note="«четверг следующей недели» = 29.10 (день отчёта) или 05.11 — дату не проверяем"),
    S("self_correction", "удали встречу с Марией ой не удаляй", action=["none", "clarify"],
      must_not=["delete", "create", "complete"]),
    U("self_correction", "перенеси материалы по презентации на завтра нет на сегодня на шесть",
      tg(PRES, new_date="2026-10-23", new_time="18:00")),
    S("self_correction", "готово то есть не готово ещё смета", action=["none", "clarify"],
      must_not=["complete", "create"]),

    # ---------- две команды
    U("two_commands", "перенеси созвон с Орбитой на пятницу и удали звонок Анне", action=["updated", "clarify"],
      must_not=["create"]),
    C("two_commands", "отметь смету и акт по аналитике выполненными", action=["completed", "clarify"]),
    S("two_commands", "удали встречу с Марией и поставь новую на воскресенье в двенадцать",
      action=["clarify", "deleted", "created"], must_not=["complete"]),
    U("two_commands", "перенеси созвон с Альфой на понедельник а звонок Анне на вторник",
      action=["updated", "clarify"]),
    C("two_commands", "смету проверил и протокол Елене отправил", action=["completed", "clarify"]),
    U("two_commands", "перенеси отчёт на согласование и созвон с Орбитой на пятницу",
      action=["updated", "clarify"], date="2026-10-30", note="обе записи в чт 29.10 15:30"),
    S("two_commands", "отмени последнее и запиши заново на завтра", action=["undone", "clarify"],
      must_not=["delete"]),

    # ---------- контекст (многоходовые)
    U("context", [H_IVAN, "ой нет на одиннадцать"], date="2026-10-24", time="11:00", title="иван"),
    U("context", [H_IVAN, "а лучше в воскресенье"], date="2026-10-25", time="10:00", title="иван"),
    C("context", ["Найди встречу с Марией в субботу", "отметь её выполненной"], action=["completed", "clarify"],
      title="мари"),
    D("context", ["Найди созвон с Орбитой", "удали его"], title="орбит", note="«его» — мужской род"),
    S("context", ["Найди созвон с Орбитой", "удали его", "да"], intent=["confirm", DELETE], action="deleted",
      title_contains=["орбит"]),
    S("context", ["Найди созвон с Орбитой", "удали его", "нет"], intent=["reject"], action=["none"],
      must_not=["delete"]),
    C("context", ["Поставь задачу купить продукты завтра в 10", "всё купил"], title="продукт"),
    U("context", [H_IVAN, "пусть повторяется каждую неделю"], action=["updated", "clarify"], title="иван",
      note="повтор: rrule FREQ=WEEKLY"),
    U("context", [H_OLEG, "Поставь встречу с Анной в понедельник в 12", "перенеси первую на воскресенье"],
      date="2026-10-25", time="15:00", title="олег", action=["updated", "clarify"]),
    U("context", ["Что у меня в субботу?", "перенеси встречу оттуда на воскресенье"], tg(MAR, new_date="2026-10-25"),
      action=["updated", "clarify"], note="в субботу одна встреча — с Марией"),
    D("context", ["Поставь задачу сдать отчёт в пятницу в 8:00", "а её удали"], title="отчёт"),
    U("context", ["Поставь встречу с Иваном завтра в 10", "Что у меня в понедельник?",
                  "перенеси встречу с Иваном туда же на одиннадцать"], date="2026-10-26", time="11:00",
      title="иван"),
    U("context", [H_OLEG, "сдвинь её на полчаса"], action=["updated", "clarify"], title="олег",
      note="«на полчаса» — раньше или позже? позже (15:30) или уточнить"),
    C("context", [H_IVAN, "позвонил уже"], title="иван", action=["completed", "clarify"]),
    U("context", [H_OLEG, "давай её в понедельник каждую неделю"], action=["updated", "clarify"], title="олег",
      note="повтор + перенос"),

    # ---------- прошлое и будущее
    C("tense", "отметь старую задачу про смету выполненной", action=["completed", "clarify"], title="смет"),
    D("tense", "удали ту встречу с Марией что была", action=["clarify", "deleted"], title="мари",
      note="прошедшая встреча — подтвердить, какую"),
    U("tense", "задача про акт была на понедельник перенеси на вторник", tg(AKT_AN, "2026-10-26",
                                                                          new_date="2026-10-27")),
    U("tense", "звонок Анне который был на третье перенеси на четвёртое", tg(ANNA, "2026-11-03",
                                                                          new_date="2026-11-04")),
    C("tense", "я уже позвонил Анне", tg(ANNA), action=["completed", "clarify"]),
    C("tense", "смета уже проверена", tg(SMETA), action=["completed", "clarify"]),
    C("tense", "вчерашнюю задачу отметь сделанной", action=["completed", "clarify"], title="максим",
      note="22.10 одна запись — вопросы для Максима"),
    U("tense", "та встреча с Марией что будет завтра перенеси на десять вечера", tg(MAR, "2026-10-24",
                                                                                  new_time="22:00")),
    D("tense", "отменилась встреча с Марией в субботу", tg(MAR, "2026-10-24"), intent=[DELETE, UPDATE],
      action=["clarify", "deleted", "updated"], must_not=["create", "complete", "undo"],
      note="v2: удалить или отметить отменённой (status cancelled), но не создать и не undo"),
    U("tense", "прошлую планёрку повтори на следующей неделе", action=["clarify", "updated", "created"],
      intent=None, must_not=["delete"], note="повтор прошедшего события — неоднозначно"),

    # ---------- вежливые и косвенные
    U("polite_indirect", "не могли бы вы перенести созвон с Орбитой на шестнадцать тридцать",
      tg(ORB, new_time="16:30")),
    U("polite_indirect", "было бы удобнее встретиться с Марией в воскресенье", tg(MAR), intent=[UPDATE, CREATE],
      action=["updated", "clarify"], date="2026-10-25",
      note="v2 (чат 0): без контекста это и создание, и перенос субботней встречи; у создания нет времени → "
           "спросить; создать встречу без времени нельзя"),
    U("polite_indirect", "можно звонок Анне на завтра", tg(ANNA), intent=[UPDATE, CREATE], action=["updated", "clarify", "created"],
      date="2026-10-24", must_not=["delete"],
      note="v2: перенос звонка 03.11 или новое напоминание на завтра"),
    C("polite_indirect", "кажется смету я уже проверил", tg(SMETA), action=["completed", "clarify"]),
    D("polite_indirect", "давай наверное уберём созвон с Альфой", tg(ALFA)),
    U("polite_indirect", "если можно сдвинь отчёт на согласование на час позже", tg(REP, shift_min=60)),
    U("polite_indirect", "мне не очень удобно в субботу с Марией давай в воскресенье",
      tg(MAR, "2026-10-24", new_date="2026-10-25"), action=["updated", "clarify"]),
    C("polite_indirect", "пожалуйста отметь что итоги Наталье отправлены", tg(NAT)),
    U("polite_indirect", "а нельзя ли перенести материалы по презентации на понедельник",
      tg(PRES, new_date="2026-10-26")),
    D("polite_indirect", "будьте любезны удалите подготовку акта", tg(["подготовить", "акт"])),
    U("polite_indirect", "сделай пожалуйста так чтобы созвон с Альфой повторялся каждое воскресенье", tg(ALFA),
      intent=[UPDATE, CREATE], action=["updated", "clarify"], note="повтор: rrule FREQ=WEEKLY;BYDAY=SU"),

    # ---------- бессмыслица
    S("nonsense", "перенеси", action=["clarify", "none"], must_not=NOT_CHANGE),
    S("nonsense", "удали", action=["clarify", "none"], must_not=NOT_CHANGE),
    S("nonsense", "перенеси на вчера", action=["clarify", "none"], must_not=NOT_CHANGE),
    S("nonsense", "перенеси встречу с Марией на тридцать второе", action=["clarify", "none"], must_not=NOT_CHANGE,
      note="несуществующая дата"),
    S("nonsense", "удали всё нафиг", action=["clarify", "none"], must_not=NOT_CHANGE,
      note="массовое удаление — только с подтверждением"),
    S("nonsense", "готово", action=["clarify", "none"], must_not=NOT_CHANGE),
    S("nonsense", "перенеси луну на завтра", action=["clarify", "none"], must_not=NOT_CHANGE),
    S("nonsense", "отметь меня", action=["clarify", "none"], must_not=NOT_CHANGE),
    S("nonsense", "перенеси встречу с Гендальфом на завтра", action=["clarify", "none"], must_not=NOT_CHANGE,
      note="такой записи нет"),
    S("nonsense", "сделай как было", action=["undone", "clarify", "none"], must_not=["create", "delete"]),

    # ---------- stand_live: реальные реплики тестировщиков со стенда (gorky_logs/turns/2026-10-23.log),
    # по одной на тип ошибки (QA-xx из qa/live/2026-10-23.jsonl); «сейчас» — момент реплики (МСК)
    U("stand_live", ["Поставь встречу с Иваном на завтра.", "На 13:00.",
                     "Можешь перенести, пожалуйста, встречу с Иваном на 12:00?"],
      date="2026-10-24", time="12:00", title="иван", must_not=["create", "search"], freeze_now="2026-10-23T21:05:00+03:00",
      note="QA-26 вежливый перенос «можешь перенести, пожалуйста» → поиск"),
    U("stand_live", ["Поставь задачу проверить почту сегодня в 21:15",
                     "сегодня в 21.15 задачу перенеси на два часа позже",
                     "перенеси эту же задачу еще на час позже"],
      date="2026-10-24", time="00:15", title="почт", must_not=["create", "delete"], freeze_now="2026-10-23T21:14:00+03:00",
      note="QA-35 «эту же задачу» → «Не нашёл запись «эту же задачу еще»»; первая реплика достроена"),
    D("stand_live", ["поставь задачу собраться командой завтра", "Поставь задачу купить продукты завтра в 10",
                     "удали все задачу собраться командо"],
      action="clarify", title="собрат", max_items=1, must_not=["delete", "create", "complete"],
      freeze_now="2026-10-23T21:16:00+03:00",
      note="QA-36 «удали все задачу собраться командо» (распознавание) → «Удалить все 3 задачи?»; "
           "спросить надо про одну"),
]
