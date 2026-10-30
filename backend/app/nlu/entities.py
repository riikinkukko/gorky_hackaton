"""EntityNormalizer: леммы, люди, организации, тип записи, грамматическое время, синонимы.

«Иваном», «Ивану», «иван» → «Иван»; «мобилку» → «мобильное приложение»;
«ТЗ» → «техническое задание».
Работает и на строчном тексте без пунктуации (так отдаёт STT).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pymorphy3
import yaml

from app.contracts import ItemType
from app.nlu.dates import PAST_ADJ

_DEFAULT_SYNONYMS = Path(__file__).with_name("synonyms.yml")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")

# слова с неудачной леммой у pymorphy3 или несклоняемые
_LEMMA_OVERRIDES = {
    "демо": "демо",
    "тз": "тз",
    "кп": "кп",
    "срм": "срм",
    "црм": "црм",
    "crm": "crm",
    "деньгам": "деньги",
    "деньгами": "деньги",
    "деньги": "деньги",
    "денег": "деньги",
    "деньга": "деньги",
    "планерка": "планёрка",
    "планерку": "планёрка",
    "планерки": "планёрка",
    "планерок": "планёрка",
    "созвоны": "созвон",
    "созвонов": "созвон",
    "созвонам": "созвон",
}

# имена-омонимы обычных слов: считаем именем, только если написаны с заглавной
_NAME_HOMONYMS = {
    "роман",
    "вера",
    "надежда",
    "любовь",
    "лев",
    "роза",
    "лилия",
    "марина",
    "слава",
    "мила",
    "дина",
    "ангелина",
    "майя",
    "мая",
    "ия",
    "нина",
    "агата",
    "кира",
    "яна",
    "тимур",
}
_PREPS_BEFORE_ENTITY = {"с", "со", "у", "для", "от", "к", "ко", "и", "кому", "вместе"}

STOP_LEMMAS = {
    # служебные
    "я",
    "мы",
    "ты",
    "вы",
    "он",
    "она",
    "оно",
    "они",
    "меня",
    "мне",
    "нас",
    "нам",
    "мой",
    "наш",
    "свой",
    "у",
    "в",
    "во",
    "на",
    "по",
    "про",
    "о",
    "об",
    "с",
    "со",
    "для",
    "к",
    "ко",
    "от",
    "до",
    "за",
    "из",
    "и",
    "а",
    "но",
    "или",
    "ли",
    "же",
    "бы",
    "не",
    "ни",
    "ну",
    "вот",
    "это",
    "этот",
    "тот",
    "весь",
    "всё",
    "все",
    "там",
    "тут",
    "здесь",
    "ещё",
    "уже",
    "вообще",
    "пожалуйста",
    "кое-что",
    "что-то",
    "какой-то",
    "какой-нибудь",
    "что-нибудь",
    "любой",
    "который",
    "так",
    "как",
    "чтобы",
    "то",
    # вопросы
    "что",
    "чем",
    "чего",
    "какой",
    "когда",
    "где",
    "кто",
    "сколько",
    "зачем",
    "почему",
    "когда-то",
    "когда-нибудь",
    # глаголы-команды и связки
    "найти",
    "найди",
    "поискать",
    "искать",
    "показать",
    "покажи",
    "напомнить",
    "быть",
    "есть",
    "мочь",
    "должный",
    "должен",
    "надо",
    "нужно",
    "нужный",
    "хотеть",
    "ставить",
    "поставить",
    "сказать",
    "иметь",
    "иметься",
    "посмотреть",
    "вывести",
    "открыть",
    "дать",
    "подсказать",
    "рассказать",
    "напомни",
    "касаться",
    "связанный",
    "насчёт",
    "тема",
    "запись",
    "последний",
    "раз",
    "прошлый",
    "следующий",
    "ближайший",
    # время запроса, а не тема: «старую встречу», «прошедшие», «будущие» → tense
    "старый",
    "прошедший",
    "прошедшее",
    "давний",
    "давно",
    "давным-давно",
    "ранний",
    "прежний",
    "минувший",
    "будущий",
    "предстоящий",
    "грядущий",
    "её",
    "его",
    "их",
    "ее",
    "запланировать",
    "запланированный",
    "планировать",
    "план",
    "связать",
    "старое",
    "предстоящее",
    "давнишний",
    "предыдущий",
    "былой",
    "новый",
    "относиться",
    "всякий",
    "разный",
    "получиться",
}
_GOVERNING_PREPS = {"к", "ко", "перед", "после", "до", "для", "про", "о", "об", "насчёт", "насчет"}
GENERIC_TYPE_LEMMAS = {
    "задача",
    "задачка",
    "дело",
    "таск",
    "задание",
    "поручение",
    "встреча",
    "напоминание",
    "напоминалка",
    "напоминалочка",
    "запись",
    "событие",
    "мероприятие",
}

_PAST_RE = re.compile(
    r"(?<![а-яё])(был|была|было|были|раньше|прошл\w*|когда-то|последний раз|в последний раз|"
    r"\w+(?:ил|ал|ял|ел|ыл)(?:а|и|ся|ась|ись)?)(?![а-яё])"
)
# «старую встречу», «прошедшие», «давнюю», «ранние» — прошлое (формы — в dates.PAST_ADJ)
_PAST_ADJ_RE = re.compile(r"(?<![а-яё])(?:" + PAST_ADJ + r")(?![а-яё])")
_FUTURE_RE = re.compile(
    r"(?<![а-яё])(будет|будут|предстоит|предстоят|ближайш\w*|следующ\w*|запланирован\w*|"
    r"будущ\w*|предстоящ\w*|грядущ\w*|"
    r"намечен\w*|скоро|впереди|когда у меня (?:встреча|созвон))(?![а-яё])"
)
# слова на -ил/-ал, которые не глаголы прошедшего времени
_PAST_FALSE = {
    "материал",
    "сигнал",
    "канал",
    "финал",
    "капитал",
    "персонал",
    "файл",
    "пил",
    "мил",
    "стил",
    "крокодил",
    "профил",
    "фитил",
    "автомобил",
    "мобил",
    "почтил",
    "идеал",
    "журнал",
    "минимал",
}


# «старую встречу», «прошедшие созвоны» — фильтр на прошлое; «предстоящую» — на будущее
_OLD_ADJ = re.compile(
    r"(?<![а-яё])(?:стар|прошедш|давн|давнишн|предыдущ|был)"
    r"(?:ая|ую|ой|ые|ых|ый|ое|ого|ым|ыми|ие|их|ий|ее|его|им|ими|юю|яя|ей|ую)"
    r"(?![а-яё])"
)
_NEW_ADJ = re.compile(
    r"(?<![а-яё])(?:предстоящ|будущ|грядущ)(?:ая|ую|ей|ие|их|ий|ее|его|им|ими)(?![а-яё])"
)


@dataclass(frozen=True)
class Lemma:
    orig: str
    lemma: str  # нормальная форма, нижний регистр, с ё как у pymorphy
    key: str  # для сравнения: без ё
    start: int
    end: int
    is_name: bool
    capitalized: bool


def _key(s: str) -> str:
    return s.lower().replace("ё", "е")


class EntityNormalizer:
    def __init__(self, synonyms_path: Path | str | None = None) -> None:
        self.morph = pymorphy3.MorphAnalyzer()
        self._lemma_cache: dict[str, str] = {}
        self._name_cache: dict[str, str | None] = {}
        data = yaml.safe_load(Path(synonyms_path or _DEFAULT_SYNONYMS).read_text(encoding="utf-8"))
        self.organizations: dict[str, str] = {}  # key леммы → каноническое имя
        for org in data.get("organizations", []):
            self.organizations[_key(self._lemma_word(org))] = org
        self.known_people: dict[str, str] = {_key(p): p for p in data.get("people", [])}
        self.type_words: dict[str, ItemType] = {}
        for t, words in (data.get("types") or {}).items():
            for w in words:
                self.type_words[_key(self._lemma_word(w))] = ItemType(t)
        # фразовые синонимы: последовательность ключей лемм → каноническая фраза
        self.phrases: list[tuple[tuple[str, ...], str]] = []
        for canon, variants in (data.get("phrases") or {}).items():
            for v in [canon, *variants]:
                seq = tuple(_key(self._lemma_word(w)) for w in _WORD_RE.findall(v))
                if seq:
                    self.phrases.append((seq, canon))
        self.phrases.sort(key=lambda x: -len(x[0]))
        self.expand: dict[str, list[str]] = {
            _key(self._lemma_word(k)): v for k, v in (data.get("expand") or {}).items()
        }
        self.verbs: dict[str, str] = {}
        for canon, variants in (data.get("verbs") or {}).items():
            for v in [canon, *variants]:
                self.verbs[_key(self._lemma_word(v))] = canon

    # ----- леммы -----

    def _lemma_word(self, word: str) -> str:
        cached = self._lemma_cache.get(word)
        if cached is not None:
            return cached
        res = self._lemma_word_raw(word)
        if len(self._lemma_cache) < 50000:
            self._lemma_cache[word] = res
        return res

    def _lemma_word_raw(self, word: str) -> str:
        w = word.lower()
        if w in _LEMMA_OVERRIDES:
            return _LEMMA_OVERRIDES[w]
        if re.fullmatch(r"[a-z]+", w):
            return w
        return self.morph.parse(w)[0].normal_form

    def _name_lemma(self, word: str) -> str | None:
        """Лемма имени, если слово может быть именем."""
        if word in self._name_cache:
            return self._name_cache[word]
        res = self._name_lemma_raw(word)
        if len(self._name_cache) < 50000:
            self._name_cache[word] = res
        return res

    def _name_lemma_raw(self, word: str) -> str | None:
        w = word.lower()
        names = [p for p in self.morph.parse(w) if "Name" in p.tag and p.score >= 0.01]
        if not names:
            return None
        # склоняемый разбор лучше несклоняемого: «Ани» → «Аня», а не «Ани»
        inflected = [p for p in names if "Fixd" not in p.tag]
        if inflected and (
            inflected[0].score >= 0.05 or names[0].score < 0.05 or "Fixd" in names[0].tag
        ):
            return inflected[0].normal_form
        return names[0].normal_form if names[0].score >= 0.05 else None

    def _is_name(self, w: str, i: int) -> str | None:
        # «с Альфа» (именительный после «с»): сначала сверка с организациями — по форме и
        # по лемме, и только потом морфология имён (иначе «Альфа» → человек «Альф»)
        if _key(w) in self._org_forms() or _key(self._lemma_word(w)) in self.organizations:
            return None
        name = self._name_lemma(w)
        if name is None:
            return None
        k = _key(name)
        cap = w[0].isupper()
        if k in self.known_people:
            return name
        if k in _NAME_HOMONYMS:
            return name if (cap and i > 0) else None
        known = self.morph.word_is_known(w.lower())
        top_is_name = "Name" in self.morph.parse(w.lower())[0].tag
        # для незнакомых слов pymorphy «угадывает» имя («хакатону») — такому не верим
        if known and (top_is_name or (cap and i > 0)):
            return name
        return None

    def lemmas(self, text: str) -> list[Lemma]:
        out: list[Lemma] = []
        words = list(_WORD_RE.finditer(text))
        i = 0
        while i < len(words):
            m = words[i]
            w = m.group(0)
            # STT рвёт имена: «с и ваном» → «иваном»
            if len(w) <= 2 and i + 1 < len(words):
                joined = w + words[i + 1].group(0)
                jn = self._is_name(joined, i)
                if (
                    jn
                    and _key(jn) in self.known_people
                    and not self._is_name(words[i + 1].group(0), i + 1)
                ):
                    out.append(
                        Lemma(
                            joined,
                            jn,
                            _key(jn),
                            m.start(),
                            words[i + 1].end(),
                            True,
                            joined[:1].isupper(),
                        )
                    )
                    i += 2
                    continue
            name = self._is_name(w, i)
            lemma = name or self._lemma_word(w)
            out.append(
                Lemma(w, lemma, _key(lemma), m.start(), m.end(), name is not None, w[:1].isupper())
            )
            i += 1
        return out

    def lemmatize(self, text: str) -> str:
        return " ".join(lm.lemma for lm in self.lemmas(text))

    def infinitive(self, word: str) -> str:
        """«проверил» → «проверить»."""
        return self._lemma_word(word)

    def _org_forms(self) -> dict[str, str]:
        """Все падежные формы организаций → ключ леммы («орбитой» → «орбита»)."""
        if not hasattr(self, "_org_forms_cache"):
            forms: dict[str, str] = {}
            for key in self.organizations:
                for f in self.morph.parse(key)[0].lexeme:
                    forms.setdefault(_key(f.word), key)
                forms[key] = key
            self._org_forms_cache = forms
        return self._org_forms_cache

    def _topic_keys(self) -> list[str]:
        if not hasattr(self, "_topic_by_key"):
            self._topic_by_key: dict[str, str] = {}
            for seq, canon in self.phrases:
                if len(seq) == 1:
                    self._topic_by_key.setdefault(seq[0], canon)
        return list(self._topic_by_key)

    # ----- сущности -----

    def normalize_person(self, word: str) -> str:
        name = self._name_lemma(word) or self._lemma_word(word)
        return name[:1].upper() + name[1:]

    def people(self, text: str, lemmas: list[Lemma] | None = None) -> list[str]:
        out: list[str] = []
        for lm in lemmas or self.lemmas(text):
            if lm.is_name and lm.key not in self.organizations:
                canon = self.known_people.get(lm.key) or (lm.lemma[:1].upper() + lm.lemma[1:])
                if canon not in out:
                    out.append(canon)
        return out

    def organizations_in(self, text: str, lemmas: list[Lemma] | None = None) -> list[str]:
        ls = lemmas or self.lemmas(text)
        out: list[str] = []
        for i, lm in enumerate(ls):
            prev = ls[i - 1].orig.lower() if i > 0 else ""
            key = lm.key
            if key not in self.organizations:  # «Альфа» в именительном: лемма бывает «альф»
                key = self._org_forms().get(_key(lm.orig), key)
            if key not in self.organizations:
                if prev not in _PREPS_BEFORE_ENTITY or lm.is_name or len(key) < 4:
                    continue
                form = self._fuzzy(_key(lm.orig), self._org_forms().keys(), unknown_only=lm.orig)
                if not form:
                    continue
                key = self._org_forms()[form]
            if prev in _PREPS_BEFORE_ENTITY or (lm.capitalized and i > 0):
                org = self.organizations[key]
                if org not in out:
                    out.append(org)
        return out

    def _fuzzy(self, key: str, vocab: Iterable[str], unknown_only: str | None = None) -> str | None:
        """Ближайшее слово словаря на расстоянии ≤ 1 (≤ 2 для длинных).

        Ловит опечатки STT: «арбитой» → «орбита».
        """
        from .text import _lev

        if unknown_only is not None and self.morph.word_is_known(unknown_only.lower()):
            return None
        limit = 1 if len(key) < 8 else 2
        best, best_d = None, limit + 1
        for v in vocab:
            d = _lev(key, v, limit)
            if d < best_d:
                best, best_d = v, d
        return best if best_d <= limit else None

    def item_type(self, text: str, lemmas: list[Lemma] | None = None) -> ItemType | None:
        """Тип записи по слову-типу.

        «Подготовиться к встрече» — не встреча: слово под предлогом пропускаем.
        """
        ls = lemmas or self.lemmas(text)
        for i, lm in enumerate(ls):
            t = self.type_words.get(lm.key)
            if t is None:
                continue
            if i > 0 and ls[i - 1].orig.lower() in _GOVERNING_PREPS:
                continue
            return t
        return None

    def tense(self, text: str) -> Literal["past", "future", "any"]:
        low = text.lower().replace("ё", "е")
        if _OLD_ADJ.search(low) or _PAST_ADJ_RE.search(low):  # nlu/v5 + storage/v3
            return "past"
        if _NEW_ADJ.search(low):
            return "future"
        for m in _PAST_RE.finditer(low):
            w = m.group(1)
            if (
                " " in w
                or w in {"был", "была", "было", "были", "раньше", "когда-то"}
                or w.startswith("прошл")
            ):
                return "past"
            stem = re.sub(r"(?:а|и|ся|ась|ись)$", "", w)
            if stem in _PAST_FALSE or len(w) < 5:
                continue
            p = self.morph.parse(w)[0]
            if "VERB" in p.tag and "past" in p.tag:
                return "past"
        if _FUTURE_RE.search(low):
            return "future"
        return "any"

    # ----- запрос для поиска -----

    def expand_query(
        self,
        text: str,
        exclude: set[str] | None = None,
        lemmas: list[Lemma] | None = None,
        keep_generic_types: bool = False,
    ) -> str | None:
        """Содержательные слова запроса в каноническом виде, с раскрытыми синонимами.

        Имена и организации исключаются (они уходят в отдельные фильтры).
        """
        ls = [lm for lm in (lemmas or self.lemmas(text))]
        excl = {_key(e) for e in (exclude or set())}
        terms: list[str] = []

        def add(t: str) -> None:
            if t and t not in terms:
                terms.append(t)

        i = 0
        while i < len(ls):
            lm = ls[i]
            # фразы
            matched = False
            for seq, canon in self.phrases:
                n = len(seq)
                if tuple(x.key for x in ls[i : i + n]) == seq:
                    add(canon)
                    i += n
                    matched = True
                    break
            if matched:
                continue
            k = lm.key
            prev = ls[i - 1].orig.lower() if i > 0 else ""
            if (
                prev in _PREPS_BEFORE_ENTITY
                and len(k) >= 4
                and not lm.is_name
                and self._fuzzy(_key(lm.orig), self._org_forms().keys(), unknown_only=lm.orig)
            ):
                i += 1
                continue  # опечатка в названии компании — она уже в фильтре organization
            if (
                lm.is_name
                or k in self.organizations
                or k in excl
                or k in STOP_LEMMAS
                or _key(lm.orig) in STOP_LEMMAS
                or len(k) < 2
            ):
                i += 1
                continue
            if k in GENERIC_TYPE_LEMMAS and not keep_generic_types:
                i += 1
                continue
            if (
                not self.morph.word_is_known(lm.orig.lower())
                and len(k) >= 5
                and k not in self.verbs
            ):
                fz = self._fuzzy(k, self._topic_keys())
                if fz:
                    add(self._topic_by_key[fz])
                    i += 1
                    continue
            if k in self.verbs:
                add(self.verbs[k])
            elif k in self.expand:
                for e in self.expand[k]:
                    add(e)
            else:
                add(lm.lemma)
            i += 1
        return " ".join(terms) or None


def first(items: list[str]) -> str | None:
    return items[0] if items else None


@lru_cache(maxsize=1)
def default_normalizer() -> EntityNormalizer:
    return EntityNormalizer()
