import pytest

from app.voice.text_norm import (
    normalize_for_tts,
    normalize_for_wer,
    number_to_words,
    ordinal_day,
    plural,
    split_for_tts,
    time_to_words,
)


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "ноль"),
        (1, "один"),
        (11, "одиннадцать"),
        (21, "двадцать один"),
        (100, "сто"),
        (103, "сто три"),
        (1000, "одна тысяча"),
        (2026, "две тысячи двадцать шесть"),
        (1_000_000, "один миллион"),
    ],
)
def test_number_to_words(n, expected):
    assert number_to_words(n) == expected


def test_feminine_and_plural():
    assert number_to_words(2, feminine=True) == "две"
    assert plural(1, "час", "часа", "часов") == "час"
    assert plural(3, "час", "часа", "часов") == "часа"
    assert plural(11, "час", "часа", "часов") == "часов"
    assert plural(22, "час", "часа", "часов") == "часа"


@pytest.mark.parametrize(
    "h,m,expected",
    [
        (10, 0, "десять часов"),
        (1, 0, "один час"),
        (15, 30, "пятнадцать тридцать"),
        (9, 5, "девять ноль пять"),
        (21, 1, "двадцать один ноль одна"),
    ],
)
def test_time(h, m, expected):
    assert time_to_words(h, m) == expected


def test_ordinals():
    assert ordinal_day(22) == "двадцать второе"
    assert ordinal_day(3, genitive=True) == "третьего"
    assert ordinal_day(31, genitive=True) == "тридцать первого"


@pytest.mark.parametrize(
    "src,expected",
    [
        (
            "Записал: позвонить Ивану — чт, 22 октября, 10:00.",
            "Записал: позвонить Ивану, четверг, двадцать второе октября, десять часов.",
        ),
        (
            "В 15:30 у вас созвон с Орбитой. Поставить на 16:30?",
            "В пятнадцать тридцать у вас созвон с Орбитой. Поставить на шестнадцать тридцать?",
        ),
        ("Перенёс на сб, 24 октября.", "Перенёс на субботу, двадцать четвёртое октября."),
        ("Нашёл 103 записи про ТЗ", "Нашёл сто три записи про тэ-зэ"),
        ("до 25 октября 2 встречи по CRM", "до двадцать пятого октября две встречи по си-ар-эм"),
        (
            "Последняя — 5 марта 2026 года",
            "Последняя, пятое марта две тысячи двадцать шестого года",
        ),
    ],
)
def test_normalize_for_tts(src, expected):
    assert normalize_for_tts(src) == expected


def test_tts_output_has_no_digits_or_latin():
    out = normalize_for_tts("Созвон с Альфой по CRM, пт, 23 октября, 9:00; ещё 3 задачи и demo")
    assert not any(ch.isdigit() for ch in out)
    assert not any("a" <= ch.lower() <= "z" for ch in out)


def test_split_for_tts():
    text = "Первое предложение. " * 80
    chunks = split_for_tts(text, limit=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "").strip()


@pytest.mark.parametrize(
    "hyp,ref",
    [
        (
            "Поставь созвон с Маяком 29 октября в 15:30.",
            "поставь созвон с маяком двадцать девятого октября в пятнадцать тридцать",
        ),
        (
            "Добавь встречу с командой завтра в 15:00",
            "добавь встречу с командой завтра в пятнадцать ноль ноль",
        ),
        ("Через 2 часа напомни проверить почту", "через два часа напомни проверить почту"),
        (
            "Послезавтра в 9 созвон с Альфой по CRM",
            "послезавтра в девять созвон с альфой по си ар эм",
        ),
        ("Найди планёрки по деньгам", "найди планерки по деньгам"),
    ],
)
def test_normalize_for_wer_matches_spoken_reference(hyp, ref):
    assert normalize_for_wer(hyp) == normalize_for_wer(ref)
