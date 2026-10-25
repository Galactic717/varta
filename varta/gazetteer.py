"""Пошук населених пунктів і областей у тексті з урахуванням відмінків.

«у Києві», «на Бровари», «біля Кривого Рогу», «над Білою Церквою», «на Харківщину» — без морфологічного
словника: назва → набір можливих основ (чергування і→о/е, ї→є, г→з, к→ц, х→с, випадні е/о), слово питання →
набір основ після відрізання відмінкових закінчень. Збіг основ = збіг слова.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .geo import haversine_km

WORD_RE = re.compile(r"[a-zа-щьюяґєії'0-9]+(?:-[a-zа-щьюяґєії'0-9]+)*", re.I)
QUERY_ENDINGS = sorted({"ами", "ями", "ові", "еві", "єві", "ого", "ому", "ими", "іми", "ою", "ею", "єю", "ах", "ях",
                        "ів", "їв", "ам", "ям", "ом", "ем", "єм", "им", "ім", "ий", "ій", "ої", "еї", "а", "я", "у",
                        "ю", "і", "ї", "е", "є", "о", "и", "ь", "й"}, key=len, reverse=True)
LEMMA_ENDINGS = ("ий", "ій", "ая", "а", "я", "о", "е", "є", "и", "і", "ь", "й")
MUTATE = {"г": "з", "к": "ц", "х": "с"}
# Рангування однойменних пунктів: столиця > обласний центр > районний > інші.
KIND_RANK = {"PPLC": 5, "PPLA": 4, "PPLA2": 3, "PPLA3": 2, "PPLX": 2}

# Службові й предметні слова, які не можна приймати за назву села («біля мене» ≠ смт Мена).
STOP = set("""
я мене мені мій моя моє мої мною нас нам ми ти тебе тобі ви вас вам він вона воно вони його її їх ним ними нею
де що щось хто як коли куди звідки скільки чи чому який яка яке які якого котра котрий коли-небудь
є буде було були був була зараз тепер вже ще досі поки біля коло близько поруч поряд над під на до від з із зі у в
через по про для за після перед між навколо довкола тут там всюди весь вся все всі усе усі цей ця це ці той та те ті
так ні не ні-ні ага ок окей будь ласка дякую привіт добре погано можна треба потрібно варто мабуть може
летить летять полетить полетіть летіла летів летіли рухається рухаються йде іде ідуть йдуть пролетить прилетить
підлетить долетить долетять прилетять наближається наближаються курс курсом напрямок напрямку сторону бік боку
ціль цілі цілей ціллю загроза загрози загроз загрозу шахед шахеди шахедів шахід шахіди герань мопед мопеди дрон
дрони дронів бпла безпілотник безпілотники ракета ракети ракет ракету крилата крилаті балістика балістичні каб
каби бомба бомби авіація літак літаки міг розвідка тривога тривоги тривогу відбій обстановка ситуація ситуацію
місто міста місті село села селі область області областю район району районі км кілометр кілометрів хв хвилин
хвилини година годин годину радіус радіусі найближчий найближча найближче найближчі найближчого далеко близько
дім дому вдома додому хата хаті робота роботі дача дачі тут небо небі укриття укритті
сирена сирени сирену сиреною безпечно небезпечно правда правду голова голови головою сильно тихо спокійно
страшно добре погано рано пізно весело швидко повільно нормально норм знову ще раз
""".split())
# Прийменники, після яких однослівна назва села — справді назва («біля Бучі», «у малинівці»)
PREP = set("у в на біля коло над під до з із зі від через по поблизу поруч поряд неподалік довкола навколо "
           "повз між околиці району околицях".split())
# Старі й розмовні українські назви → чинна назва (беремо найбільший пункт з такою назвою)
ALIASES = {
    "дніпропетровськ": "Дніпро", "кіровоград": "Кропивницький", "кривбас": "Кривий Ріг", "артемівськ": "Бахмут",
    "дніпродзержинськ": "Кам'янське", "новомосковськ": "Самар", "комсомольськ": "Горішні Плавні",
}

OBLAST_FORMS = {
    "вінницька": ["вінниччин", "вінницьк обл"], "волинська": ["волин", "волинськ обл"],
    "дніпропетровська": ["дніпропетровщин", "дніпровщин", "дніпропетровськ обл"],
    "донецька": ["донеччин", "донецьк обл"], "житомирська": ["житомирщин", "житомирськ обл"],
    "закарпатська": ["закарпатт", "закарпатськ обл"], "запорізька": ["запоріжчин", "запорізьк обл"],
    "івано-франківська": ["івано-франківщин", "прикарпатт", "івано-франківськ обл"],
    "київська": ["київщин", "київськ обл"], "кіровоградська": ["кіровоградщин", "кіровощин", "кіровоградськ обл"],
    "луганська": ["луганщин", "луганськ обл"], "львівська": ["львівщин", "львівськ обл"],
    "миколаївська": ["миколаївщин", "миколаївськ обл"], "одеська": ["одещин", "одеськ обл"],
    "полтавська": ["полтавщин", "полтавськ обл"], "рівненська": ["рівненщин", "рівненськ обл"],
    "сумська": ["сумщин", "сумськ обл"], "тернопільська": ["тернопільщин", "тернопільськ обл"],
    "харківська": ["харківщин", "харківськ обл"], "херсонська": ["херсонщин", "херсонськ обл"],
    "хмельницька": ["хмельниччин", "хмельницьк обл"], "черкаська": ["черкащин", "черкаськ обл"],
    "чернівецька": ["буковин", "чернівецьк обл"], "чернігівська": ["чернігівщин", "чернігівськ обл"],
    "м. київ": ["столиц"],
    "автономна республіка крим": ["крим"],
}


def norm(text: str) -> str:
    return re.sub(r"[’ʼ`´‘]", "'", text.lower())


def query_stems(word: str) -> set[str]:
    out = {word}
    for e in QUERY_ENDINGS:
        if word.endswith(e) and len(word) - len(e) >= 3:
            out.add(word[: -len(e)])
    return out


def lemma_stems(word: str) -> set[str]:
    base = {word}
    for e in LEMMA_ENDINGS:
        if word.endswith(e) and len(word) - len(e) >= 2:
            base.add(word[: -len(e)])
            break
    out = set(base)
    for s in base:
        if s.endswith("ець"):
            out.add(s[:-3] + "ц")
        if s.endswith("ок"):
            out.add(s[:-2] + "к")
        # чергування в закритому складі: Харків→Харкова, Київ→Києва, Бориспіль→Борисполя, Ірпінь→Ірпеня
        m = re.search(r"([ії])([^аеєиіїоуюяь']+)ь?$", s)
        if m:
            head, tail = s[: m.start(1)], m.group(2)
            for v in (("є",) if m.group(1) == "ї" else ("о", "е")):
                out.add(head + v + tail)
    for s in list(out):
        s2 = s.rstrip("ь")
        out.add(s2)
        if s2 and s2[-1] in MUTATE:
            out.add(s2[:-1] + MUTATE[s2[-1]])
    return {s for s in out if len(s) >= 2}


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    kind: str
    population: int
    oblast_key: str
    raion_key: str

    @property
    def rank(self) -> int:
        return KIND_RANK.get(self.kind, 1)


@dataclass(frozen=True)
class Match:
    place: Place | None      # None для збігу області
    oblast_key: str
    start: int               # позиція в нормалізованому тексті
    end: int
    words: int


class Gazetteer:
    def __init__(self, places_path: Path):
        rows = json.loads(Path(places_path).read_text(encoding="utf-8"))
        self.places = [Place(*r) for r in rows]
        self._index: dict[str, list[tuple[int, list[set[str]]]]] = {}
        for i, p in enumerate(self.places):
            words = WORD_RE.findall(norm(p.name))
            if not words:
                continue
            stems = [lemma_stems(w) for w in words]
            for s in stems[0]:
                self._index.setdefault(s, []).append((i, stems))
        best: dict[str, int] = {}
        for i, p in enumerate(self.places):
            if p.rank > self.places[best.get(p.name, i)].rank or p.name not in best:
                best[p.name] = i
        for alias, name in ALIASES.items():
            if name in best:
                self._index.setdefault(alias, []).append((best[name], [lemma_stems(alias)]))
                for st in lemma_stems(alias):
                    self._index.setdefault(st, []).append((best[name], [lemma_stems(alias)]))
        self.major = [p for p in self.places if p.rank >= 2]
        self._grid: dict[tuple[int, int], list[Place]] = {}
        for p in self.major:
            self._grid.setdefault((int(p.lat // 1), int(p.lon // 1)), []).append(p)
        self._norm_names = [norm(p.name) for p in self.places]
        self._oblast_index = [(key, [f.split() for f in forms]) for key, forms in OBLAST_FORMS.items()]

    def find(self, text: str, near: tuple[float, float] | None = None) -> list[Match]:
        """Усі згадки місць у тексті, найдовші збіги, без перекриттів."""
        cased = re.sub(r"[’ʼ`´‘]", "'", text)   # ті самі позиції, що й norm(text), але з регістром
        t = norm(text)
        toks = [(m.group(0), m.start(), m.end()) for m in WORD_RE.finditer(t)]
        raw = [cased[s:e] for _, s, e in toks]
        cands: list[tuple[int, int, int, Match]] = []   # (слів, ранг, -відстань, Match)
        for i, (w, s, _) in enumerate(toks):
            qs = [query_stems(x) for x, _, _ in toks[i:i + 4]]
            # області: «Київщина», «київській області»
            for key, forms in self._oblast_index:
                for form in forms:
                    if len(form) <= len(qs) and all(f in q or any(x.startswith(f) for x in q)
                                                    for f, q in zip(form, qs)):
                        cands.append((len(form), 10, 0, Match(None, key, s, toks[i + len(form) - 1][2], len(form))))
            if w in STOP or w.isdigit():
                continue
            capital = raw[i][:1].isupper()
            for stem in qs[0]:
                for idx, lstems in self._index.get(stem, ()):
                    n = len(lstems)
                    if n > len(qs) or not all(lstems[k] & qs[k] for k in range(1, n)):
                        continue
                    if any(toks[i + k][0] in STOP for k in range(n)):
                        continue
                    p = self.places[idx]
                    # однослівна назва малого села приймається лише після прийменника або з великої літери
                    # не на початку тексту: інакше «Сирена працює?», «вже безпечно» стають «селами»
                    after_prep = i > 0 and toks[i - 1][0] in PREP
                    if n == 1 and p.rank == 1 and not after_prep and not (capital and i > 0):
                        continue
                    dist = haversine_km(near[0], near[1], p.lat, p.lon) if near else 0.0
                    cands.append((n, p.rank, -dist, Match(p, p.oblast_key, s, toks[i + n - 1][2], n)))
        # жадібно: довші збіги, вищий ранг, ближче до користувача; без перекриттів
        cands.sort(key=lambda c: (-c[0], -c[1], -c[2], c[3].start))
        out: list[Match] = []
        for *_, m in cands:
            if all(m.end <= o.start or m.start >= o.end for o in out):
                out.append(m)
        return sorted(out, key=lambda m: m.start)

    def nearest(self, lat: float, lon: float, max_km: float | None = None) -> Place | None:
        """Найближчий помітний пункт (громада і більше) — для підпису «біля X». Сітка 1°: без перебору всіх."""
        cell = (int(lat // 1), int(lon // 1))
        cands = [p for dy in (-1, 0, 1) for dx in (-1, 0, 1) for p in self._grid.get((cell[0] + dy, cell[1] + dx), ())]
        best = min(cands, default=None, key=lambda p: haversine_km(lat, lon, p.lat, p.lon))
        if best and max_km is not None and haversine_km(lat, lon, best.lat, best.lon) > max_km:
            return None
        return best

    def search(self, prefix: str, limit: int = 8) -> list[Place]:
        """Пошук для рядка пошуку на карті: префікс назви, більші пункти вище."""
        q = norm(prefix).strip()
        if len(q) < 2:
            return []
        hits = [p for p, n in zip(self.places, self._norm_names) if n.startswith(q)]
        hits.sort(key=lambda p: (-p.rank, -p.population, p.name))
        return hits[:limit]
