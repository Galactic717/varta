"""Питання → Query. Слоти — детерміновано (газетир, лексикон, регекси); намір — класифікатор.

Перед класифікацією місця й числа маскуються («що летить на <місце>»): модель вчиться формі питання,
а не назвам міст, і не може «вигадати» місто — місто береться лише з газетира.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..gazetteer import Gazetteer, Place, lemma_stems, norm, query_stems, WORD_RE
from .lexicon import detect_types, mask_types

ME_RE = re.compile(r"\b(мене|мені|мною|нас|нам|нами|я|ми|мій|моє\w*|мого|моєму|моїм|моя|моїй|наш\w*|"
                   r"вдома|додому|дім|дому|хата|хаті)\b", re.I)
KM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:км|кілометр\w*|km|кі?лом\w*)", re.I)
MIN_RE = re.compile(r"(\d+)\s*(?:хв\w*|хвилин\w*|мін\w*)", re.I)
HOUR_RE = re.compile(r"(\d+)\s*(?:год\w*)|\b(годин[уи]|пів\s*години|півгодини)\b", re.I)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class UserPlace:
    name: str
    lat: float
    lon: float


@dataclass
class Query:
    text: str
    masked: str
    intent: str = "chitchat"
    confidence: float = 0.0
    types: frozenset = frozenset()
    place: Place | None = None          # місто з питання
    place_text: str = ""
    oblast_key: str = ""                # область з питання
    user_place: UserPlace | None = None  # «моє місце»/названі місця користувача
    about_me: bool = False
    radius_km: float | None = None
    horizon_min: float | None = None
    inherited: list = field(default_factory=list)


def extract(text: str, gaz: Gazetteer, places: list[UserPlace], near: tuple[float, float] | None) -> Query:
    q = Query(text=text, masked="")
    t = norm(text)
    spans: list[tuple[int, int, str]] = []

    # місця користувача за назвою («на дачі», «біля роботи»): основа назви = основа слова
    words = [(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(t)]
    for up in places:
        ustems = [lemma_stems(w) for w in WORD_RE.findall(norm(up.name))]
        if not ustems:
            continue
        for i in range(len(words) - len(ustems) + 1):
            if all(us & query_stems(words[i + k][0]) for k, us in enumerate(ustems)):
                q.user_place = up
                spans.append((words[i][1], words[i + len(ustems) - 1][2], "<місце>"))
                break
        if q.user_place:
            break

    for m in gaz.find(text, near=near):
        if any(not (m.end <= s or m.start >= e) for s, e, _ in spans):
            continue
        if m.place is None:
            q.oblast_key = q.oblast_key or m.oblast_key
            spans.append((m.start, m.end, "<область>"))
        elif q.place is None:
            q.place, q.place_text = m.place, text[m.start:m.end]
            spans.append((m.start, m.end, "<місце>"))
        else:
            spans.append((m.start, m.end, "<місце>"))

    if (km := KM_RE.search(t)):
        q.radius_km = float(km.group(1).replace(",", "."))
    if (mn := MIN_RE.search(t)):
        q.horizon_min = float(mn.group(1))
    elif (h := HOUR_RE.search(t)):
        q.horizon_min = float(h.group(1)) * 60 if h.group(1) else (30.0 if "пів" in h.group(0) else 60.0)
    q.types = detect_types(t)
    q.about_me = bool(ME_RE.search(t)) and q.place is None and not q.oblast_key

    masked, last = [], 0
    for s, e, tok in sorted(spans):
        masked.append(t[last:s])
        masked.append(tok)
        last = e
    masked.append(t[last:])
    q.masked = mask_types(NUM_RE.sub("<n>", "".join(masked)))
    return q


# ---------- базовий класифікатор на правилах (і запасний, якщо моделі немає) ----------
RULES: list[tuple[str, str]] = [
    ("refuse", r"влуча|влучи|влучан|прил[еі]т|наслідк|\bппо\b|\bпво\b|мобільн\w* груп|збил|збит|збив|руйнуван|"
               r"постраждал|загибл|вибух|розбомб|куди потрап|що підбил|де стоїть|позиці"),
    ("glossary", r"що таке|що це за|що за (\w+ )?(шахед|ракет|каб|бпла|дрон)|хто так|що означа|як виглядає|"
                 r"розкажи про|яка швидкість|як швидко літа|що значить"),
    ("help", r"що ти вмієш|що можеш|що ти можеш|як (тобою )?користуват|допомог|\bhelp\b|команд|які питання|"
             r"що тебе можна|про що (можна )?пита|ти хто|хто ти"),
    ("safety", r"безпечн|небезпечн|можна (виходит|вийти|гуляти|йти|спати|їхати|вже)|укритт|ховатис|сховат|"
               r"в коридор|в підвал|до підвал|правило двох стін|чи варто (йти|вихо)"),
    ("alert", r"тривог|сирен|відбій|відбою"),
    ("eta", r"коли (він |вона |вони |це |ця |цей )?(буде|будуть|прилет|долет|підлет|дол[еі]т|з'явит)|через скільки|"
            r"скільки (часу|хвилин|хв)|час підльоту|як скоро|за скільки|скоро (буде|прилет)|о котрій"),
    ("count", r"скільки"),
    ("nearest", r"найближч|ближче всього|найближче|що ближче"),
    ("approaching", r"лет\w* на|лет\w* в|в (мою|нашу) сторону|в наш бік|в мій бік|до (мене|нас)|на (мене|нас)|"
                    r"підлет|прямує|курсом на|наближа|загрожу|долетить|прилетить|в напрямку|на <місце>|до <місце>"),
    ("around", r"біля|поруч|поряд|навколо|довкола|над|в радіусі|поблизу|в районі|коло|неподалік|в <місце>|у <місце>"),
    ("overview", r"обстановк|ситуаці|що відбуваєт|що зараз|що там|загалом|по країні|що на|як (там|справи)|"
                 r"що (літає|летить|лет)|де (шахед|ракет|ціл|дрон|бпла)|звідки|що в <область>|<область>"),
]
_RULES = [(i, re.compile(p, re.I)) for i, p in RULES]


def rule_intent(masked: str) -> tuple[str, float]:
    for intent, rx in _RULES:
        if rx.search(masked):
            return intent, 0.6
    return "chitchat", 0.3
