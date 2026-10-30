# app/nlu — понимание запросов

Реплика пользователя → `Command` (контракт). Даты считает код, а не LLM.

```python
from app.nlu import build_nlu, RuleDateResolver

nlu = build_nlu()  # LLM_PROVIDER=gigachat | yandex | rules
cmd = await nlu.parse("Перенеси встречу с Марией в субботу на 19:00", state, now)
spec = RuleDateResolver().resolve("в пятницу в 11", now)  # TimeSpec
```

## Как разбирается реплика

0. **Нормализация транскрипта** (`asr.py`), до всего остального. Удаляет заминки («э-э», «ну короче»), приводит время («15 00», «пятнадцать ноль ноль», «в 3 30», «15.00» → «15:00», «3:30») и сущности, искажённые распознаванием: «с и ваном» → «с Иваном», «в лектором» → «Вектором», «с арбитой» → «с Орбитой», «созвон маяком» → «созвон с Маяком». Сопоставление идёт по фонетическому ключу для русского (оглушение, аканье, иканье) с порогом Левенштейна 0/1/2 по длине слова. Словарные слова («лектор», «север» вне контекста) не трогаются. Словарь — люди, организации и темы из `synonyms.yml`, плюс сущности пользователя из его записей: `nlu.attach_repository(repo)`, кэш 5 минут. Исходный текст, результат и список исправлений пишутся в лог `app.nlu.asr`: `asr session=… original=… → text=… fixes=…`. В `Command.raw_text` попадает нормализованный текст. Выключение — `NLU_ASR_NORMALIZE=false`. Реальные ошибки распознавания добавляются в `REAL_ASR` в `tests/nlu/test_asr_noise.py`.
1. **Ответ на уточнение** (`context.py`). Если `state.awaiting` задан, «в три», «давай на 16:30», «вторую», «отмена» дополняют `state.pending`, а не становятся новой командой. Полноценная новая команда («Что у меня сегодня?») перебивает ожидание.
2. **Правила** (`router.py`). При уверенности ≥ `NLU_RULES_THRESHOLD` (0,85) ответ отдаётся сразу, без LLM (~1 мс).
3. **LLM** (`llm/`). GigaChat или YandexGPT, function calling: `create_item`, `list_items`, `search_items`, `update_item`, `complete_item`, `delete_item`, `smalltalk`. Модель возвращает `when_text`, дату считает `RuleDateResolver`. Ответ проверяется Pydantic; при ошибке — один ретрай с текстом ошибки.
4. **Фолбэк** — лучшая догадка правилами (`source="fallback"`), если LLM нет, он упал, превысил `NLU_LLM_TIMEOUT` или дважды ответил невалидно.

## Соглашения для чатов 2 и 3

| Что | Как |
|---|---|
| Периоды | `end` — **исключающая** граница: «сегодня» = `[00:00, 00:00 завтра)`, как у встреч `[starts_at, ends_at)` |
| Дата без времени | `has_time=False`, `start` = полночь этого дня. Встрече — уточнить время |
| Дедлайн | «до конца недели» → `start` = вс 23:59, `has_time=True`; «до пятницы» → пт 23:59, `has_time=False` |
| «на следующей неделе» при создании | `is_period=True, ambiguous=True` → уточнить день |
| Невалидная дата («31 ноября») | `start=None, ambiguous=True` |
| Цель update/complete/delete | `slots.target` (`pronoun` / `description` / `item_id`) **плюс** фильтры поиска цели в тех же `slots`: `when` (где запись сейчас, период), `participants`, `organization`, `type`, `query` |
| Перенос без даты («на 16:30») | `new_when` со временем на сегодня/завтра; дату записи сохраняет диалог. Проверка: `has_explicit_date(new_when.text, now)` |
| Перенос без даты («на завтра») | `new_when.has_time=False` → время записи сохраняет диалог |
| Сдвиг («на час позже») | `new_when.start=None`, `parse_shift(new_when.text)` → минуты (+60 / −30) |
| Тип в поиске | `meeting` / `reminder` только если сказано явно. «Задача» в речи значит «любая запись», поэтому `type=task` в поиске не ставится. «Подготовиться к встрече» — не встреча |
| `query` | леммы темы с раскрытыми синонимами: «мобилка» → «мобильное приложение», «деньги» → «бюджет смета», «тэ зэ» → «техническое задание». Имена и компании — только в `participants` / `organization` |
| «отмена» | при `awaiting` → `REJECT`; без сценария → `UNDO` |
| «нет, в 11» | `UPDATE`, `target.pronoun=True` |

## Переменные окружения

`LLM_PROVIDER`, `GIGACHAT_CREDENTIALS`, `GIGACHAT_SCOPE` (GIGACHAT_API_PERS), `GIGACHAT_MODEL` (GigaChat-2), `GIGACHAT_VERIFY_SSL` (true | false | путь к CA Минцифры), `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL` (yandexgpt-lite), `NLU_LLM_TIMEOUT` (6), `NLU_RULES_THRESHOLD` (0.85).

Зависимости: `pydantic>=2`, `httpx`, `PyYAML`, `pymorphy3`, `pymorphy3-dicts-ru`. Для тестов: `pytest`.

## Проверка

```bash
cd backend
python -m pytest tests/nlu -q                                     # 220 тестов, из них 104 на даты
python -m app.nlu.eval_nlu tests/nlu/fixtures/golden_eval.jsonl   # таблица метрик, -v — подробно
```

Словари (синонимы, организации, люди, типы) — в `synonyms.yml`, правятся без кода.
