"""Виконання Query над Situation і відповідь українською за шаблонами.

Жодне число, місто чи статус тут не генерується моделлю: усе береться з треків, тривог і газетира.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from ..geo import Approach, Motion, approach, compass, haversine_km
from ..situation import USABLE_S, Situation, Track, parse_ts
from .lexicon import GLOSSARY, INTENTS, TYPE_FORMS, TYPE_ORDER, plural, type_count
from .parse import Query

STALE_S = 120
DATA_INTENTS = {"nearest", "around", "approaching", "eta", "count", "overview", "safety", "alert"}
SOUND_RE = re.compile(r"звук|гуде|гудуть|гул|шум|гуркіт|бахкає|чути")
AROUND_KM, APPROACH_KM, HORIZON_MIN, COUNT_KM = 30.0, 20.0, 60.0, 50.0
NO_PLACE = ("Не знаю, щодо якого місця відповідати. Додайте «Моє місце» на карті (кнопка ⌖ або клік правою "
            "кнопкою) чи назвіть місто: «найближчий шахед до Києва».")
SAFETY_TAIL = "Рішення ховатися — за офіційною тривогою: якщо вона є, прямуйте в укриття (або правило двох стін)."
REFUSE = ("Про влучання, наслідки, збиття та роботу ППО не відповідаю: під час атаки така інформація допомагає "
          "ворогу коригувати удари, а її поширення заборонено (ст. 114-2 ККУ). Можу сказати, де цілі зараз і чи "
          "летять до вас.")
HELP = ("Я відповідаю лише за живими даними NEPTUN і MAPA.UA та офіційними тривогами — нічого не вигадую. Питайте:\n"
        "• «де найближчий шахед?» / «найближча ракета до Одеси»\n"
        "• «чи летить щось на мене?» / «що летить на Київ?»\n"
        "• «коли буде у мене?» / «через скільки підлетить?»\n"
        "• «що біля Броварів?» / «що в радіусі 20 км?»\n"
        "• «чи є тривога у Львові?» / «де зараз тривога?»\n"
        "• «скільки шахедів зараз?» / «що на Харківщині?» / «яка обстановка?»\n"
        "• «що таке КАБ?»\n"
        "Для питань «до мене» додайте своє місце на карті.")


@dataclass
class Answer:
    text: str
    intent: str
    focus: list[str] = field(default_factory=list)     # id треків для підсвітки
    place: dict | None = None                           # {'name','lat','lon','radius_km'} для кола на карті
    facts: dict = field(default_factory=dict)


# ---------- форматування ----------
def fmt_km(d: float) -> str:
    return "менше 1 км" if d < 1 else f"{round(d)} км"


def fmt_min(m: float) -> str:
    if m < 1:
        return "менше хвилини"
    m = round(m)
    if m < 60:
        return f"~{m} хв"
    return f"~{m // 60} год {m % 60:02d} хв"


def fmt_span(m: float) -> str:
    """Горизонт: «годину», «30 хв», «2 год»."""
    m = round(m)
    if m == 60:
        return "годину"
    return f"{m} хв" if m < 60 else f"{m // 60} год" + (f" {m % 60} хв" if m % 60 else "")


def ago(sec: float) -> str:
    if sec < 60:
        return "щойно"
    if sec < 3600:
        return f"{round(sec / 60)} хв тому"
    return f"{round(sec / 3600)} год тому"


def clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def name_of(t: Track) -> str:
    base = t.title.strip() or TYPE_FORMS.get(t.type, TYPE_FORMS["unknown"])[0]
    return f"{base} (група ×{t.count})" if t.count > 1 else base


def where_of(t: Track) -> str:
    if t.area_only:
        return f"{t.region} (точки немає — відомо лише область)"
    # назви не відмінюємо (вигадати відмінок = помилитися): «біля н. п. Вільнянськ»
    return f"біля н. п. {t.locality}" if t.locality else (t.region or "поза межами України")


def src_of(t: Track) -> str:
    return " + ".join({"neptun": "NEPTUN", "mapa": "MAPA.UA"}[s] for s in t.sources)


def sigma_of(t: Track) -> float:
    if t.speed_kmh and not t.presumptive:
        return 20.0
    return 35.0


# ---------- вибірки ----------
@dataclass
class View:
    t: Track
    lat: float
    lon: float
    a: Approach | None


def usable(sit: Situation, now: float, types: frozenset) -> list[Track]:
    return [t for t in sit.tracks(now)
            if t.status == "active" and t.age_s(now) <= USABLE_S and (not types or t.type in types)]


def views(tracks: list[Track], now: float, place: tuple[float, float] | None,
          radius: float = APPROACH_KM, horizon: float = HORIZON_MIN) -> list[View]:
    out = []
    for t in tracks:
        if t.area_only:
            continue
        lat, lon, _ = t.position(now)
        a = None
        if place:
            speed, _ = t.speed_for_estimate()
            m = Motion(lat, lon, t.heading, speed, sigma_of(t))
            a = approach(m, place[0], place[1], radius, horizon)
        out.append(View(t, lat, lon, a))
    return out


# «Київщина» в розмові включає саме місто; «Крим» — Севастополь (у межах NEPTUN це окремі ключі)
OBLAST_WITH = {"київська": {"київська", "м. київ"}, "автономна республіка крим": {"автономна республіка крим", "севастополь"}}


def oblast_keys(oblast_key: str) -> set[str]:
    return OBLAST_WITH.get(oblast_key, {oblast_key})


def oblast_name(sit: Situation, key: str) -> str:
    return (sit.regions.oblast_names.get(key) if sit.regions else "") or key


def in_oblast(sit: Situation, t: Track, oblast_key: str) -> bool:
    keys = oblast_keys(oblast_key)
    if not t.area_only and sit.regions:
        return sit.regions.locate(t.lat, t.lon)["oblast_key"] in keys
    return t.region.strip().lower() in {oblast_name(sit, k).lower() for k in keys}


def track_line(v: View, now: float, rel: bool = True) -> str:
    """«БпЛА біля Броварів: 24 км на схід, курс на захід — наближається; …»"""
    t, a = v.t, v.a
    parts = [f"{name_of(t)} {where_of(t)}".strip()]
    if rel and a:
        parts.append(f": {fmt_km(a.distance_km)} на {compass(a.bearing_from_place)}")
    if t.heading is not None:
        motion = f", курс на {compass(t.heading)}"
        if a and a.closing is not None:
            motion += " — наближається" if a.closing else " — віддаляється"
            if a.closing and a.cpa_km is not None and a.cpa_km < 50 and a.t_cpa_min:
                cpa = fmt_km(a.cpa_km)
                motion += f"; за незмінного курсу мине на відстані {'' if cpa.startswith('менше') else '~'}{cpa} через {fmt_min(a.t_cpa_min)}"
        parts.append(motion)
    elif t.target_name:
        parts.append(f", прямує на {t.target_name}")
    tail = []
    if t.quality != "confirmed":
        tail.append(f"позиція орієнтовна (±{round(t.uncertainty_km)} км)")
    tail.append(f"оновлено {ago(t.age_s(now))}")
    tail.append(f"дані: {src_of(t)}")
    if t.advisory:
        tail.append("це спостереження, не сигнал ховатися")
    tail_s = ", ".join(tail)
    return "".join(parts) + ". " + tail_s[:1].upper() + tail_s[1:] + "."


def alert_line(sit: Situation, lat: float, lon: float, label: str, mine: bool) -> str:
    al = sit.alert_at(lat, lon)
    if not al:
        return "Офіційної тривоги у вашому районі зараз немає." if mine else f"{label}: офіційної тривоги зараз немає."
    level = {"red": "червоний рівень", "yellow": "жовтий рівень"}.get(al.get("level") or "", "")
    ts = parse_ts(al.get("since"))
    since = f", з {clock(ts)}" if ts and time.time() - ts < 86400 else ""
    why = f" — {', '.join(al['reasons'])}" if al.get("reasons") else ""
    what = f"{al.get('name') or al.get('oblast')}{(' (' + level + ')') if level else ''}{why}{since}."
    return f"Тривога у вашому районі: {what}" if mine else f"{label}: тривога — {what}"


def breakdown(tracks: list[Track]) -> tuple[int, str]:
    by: dict[str, int] = {}
    for t in tracks:
        by[t.type] = by.get(t.type, 0) + max(1, t.count)
    total = sum(by.values())
    return total, ", ".join(type_count(k, by[k]) for k in TYPE_ORDER if k in by)


# ---------- виконання ----------
class Executor:
    def __init__(self, situation: Situation):
        self.sit = situation

    def run(self, q: Query, my_place: dict | None = None, now: float | None = None) -> Answer:
        now = now or time.time()
        # «що за звук?» класифікатор плутає з довідкою/допомогою; звук не визначити, але сусідство показати можна
        if SOUND_RE.search(q.text.lower()) and q.intent in {"chitchat", "help", "glossary"}:
            q.intent = "chitchat"
        if q.intent not in INTENTS:          # захист: намір з контексту клієнта
            q.intent = "chitchat"
        ans = getattr(self, "_" + q.intent)(q, my_place, now)
        if q.intent in DATA_INTENTS:
            age = self.sit.data_age_s(now)
            if age is None:
                ans.text = "⚠️ Немає звʼязку з джерелами даних — відповідь може бути неповною.\n" + ans.text
            elif age > STALE_S:
                # без цього «цілей немає / тривоги немає» після обриву звучало б як відбій
                ans.text = (f"⚠️ Дані NEPTUN не оновлювались {fmt_min(age / 60).lstrip('~')} — «немає» тут може бути "
                            f"неправдою. Орієнтуйтеся на сирену.\n" + ans.text)
        return ans
        return ans

    # місце, щодо якого відповідаємо: явне місто > назване місце користувача > «моє місце»
    @staticmethod
    def _place(q: Query, my_place: dict | None) -> tuple[float, float, str, bool] | None:
        if q.user_place:
            return q.user_place.lat, q.user_place.lon, q.user_place.name, True
        if q.place:
            return q.place.lat, q.place.lon, q.place.name, False
        if my_place and my_place.get("lat") is not None:
            return float(my_place["lat"]), float(my_place["lon"]), my_place.get("name") or "Моє місце", True
        return None

    @staticmethod
    def _who(mine: bool, name: str) -> str:
        return f"Біля вас ({name})" if mine else name

    def _nearest(self, q, my_place, now):
        p = self._place(q, my_place)
        if not p:
            return Answer(NO_PLACE, q.intent)
        tr = usable(self.sit, now, q.types)
        vs = sorted(views(tr, now, p[:2]), key=lambda v: v.a.distance_km)
        head = self._who(p[3], p[2])
        place = {"name": p[2], "lat": p[0], "lon": p[1], "radius_km": None}
        what = "ціль" if not q.types else ", ".join(TYPE_FORMS[t][0] for t in sorted(q.types))
        if not vs:
            extra = self._area_only_note(tr)
            text = f"{head}: зараз на карті немає позначок типу «{what}» з точкою.{extra}"
        else:
            v = vs[0]
            kind = "найближча ціль" if not q.types else f"найближча ціль типу «{what}»"
            text = f"{head}: {kind} — {track_line(v, now)}"
            place["radius_km"] = round(v.a.distance_km)
        text += "\n" + alert_line(self.sit, p[0], p[1], p[2], p[3])
        return Answer(text, q.intent, [vs[0].t.id] if vs else [], place,
                      {"distance_km": round(vs[0].a.distance_km) if vs else None})

    def _area_only_note(self, tracks: list[Track]) -> str:
        area = [t for t in tracks if t.area_only]
        if not area:
            return ""
        return " Без точки (лише область): " + "; ".join(f"{name_of(t)} — {t.region}" for t in area[:4]) + "."

    def _around(self, q, my_place, now):
        if q.oblast_key and not q.place and not q.user_place:
            return self._overview(q, my_place, now)
        p = self._place(q, my_place)
        if not p:
            return Answer(NO_PLACE, q.intent)
        r = q.radius_km or AROUND_KM
        tr = usable(self.sit, now, q.types)
        vs = sorted(views(tr, now, p[:2], radius=r), key=lambda v: v.a.distance_km)
        inside = [v for v in vs if v.a.distance_km <= r]
        head = self._who(p[3], p[2])
        if inside:
            lines = "\n".join("• " + track_line(v, now) for v in inside[:6])
            more = f"\n…і ще {len(inside) - 6}." if len(inside) > 6 else ""
            text = f"{head}: у радіусі {fmt_km(r)} — {len(inside)} {plural(len(inside), ('ціль', 'цілі', 'цілей'))}:\n{lines}{more}"
        else:
            text = f"{head}: у радіусі {fmt_km(r)} цілей немає."
            if vs:
                text += f" Найближча — {track_line(vs[0], now)}"
        text += "\n" + alert_line(self.sit, p[0], p[1], p[2], p[3])
        focus = [v.t.id for v in inside] or [v.t.id for v in vs[:1]]   # порожньо в радіусі → показати найближчу
        return Answer(text, q.intent, focus, {"name": p[2], "lat": p[0], "lon": p[1], "radius_km": r},
                      {"inside": len(inside)})

    def _candidates(self, q, p, now) -> tuple[list[View], float, float]:
        r = q.radius_km or APPROACH_KM
        h = q.horizon_min or HORIZON_MIN
        vs = views(usable(self.sit, now, q.types), now, p[:2], radius=r, horizon=h)
        def hit(a: Approach) -> bool:
            return a.distance_km <= r or (a.p_enter or 0) >= 0.2 or (a.eta_min is not None and a.eta_min <= h)

        cand = [v for v in vs if hit(v.a)]
        cand.sort(key=lambda v: (v.a.eta_min if v.a.eta_min is not None else 1e9, -(v.a.p_enter or 0)))
        return cand, r, h

    def _approaching(self, q, my_place, now):
        if q.oblast_key and not q.place and not q.user_place:
            return self._overview(q, my_place, now)
        p = self._place(q, my_place)
        if not p:
            return Answer(NO_PLACE, q.intent)
        cand, r, h = self._candidates(q, p, now)
        head = self._who(p[3], p[2])
        place = {"name": p[2], "lat": p[0], "lon": p[1], "radius_km": r}
        if cand:
            lines = []
            for v in cand[:5]:
                eta = "вже в радіусі" if v.a.eta_min == 0 else (f"через {fmt_min(v.a.eta_min)}" if v.a.eta_min else "")
                prob = f", ймовірність ~{round(v.a.p_enter * 100)} %" if v.a.p_enter is not None and v.a.eta_min else ""
                lines.append(f"• {name_of(v.t)} {where_of(v.t)}: {fmt_km(v.a.distance_km)} на "
                             f"{compass(v.a.bearing_from_place)}, {eta}{prob} (дані: {src_of(v.t)}, оновлено {ago(v.t.age_s(now))})")
            text = (f"{head}: так, у межах {fmt_km(r)} за {fmt_span(h)} можуть пройти "
                    f"{len(cand)} {plural(len(cand), ('ціль', 'цілі', 'цілей'))} (оцінка за поточним курсом):\n"
                    + "\n".join(lines) + "\nКурс може змінитися будь-якої миті.")
        else:
            vs = sorted(views(usable(self.sit, now, q.types), now, p[:2], r, h), key=lambda v: v.a.distance_km)
            text = f"{head}: зараз жодна ціль не прямує в межі {fmt_km(r)} (оцінка за поточним курсом на {fmt_span(h)})."
            if vs:
                text += f" Найближча — {track_line(vs[0], now)}"
        text += "\n" + alert_line(self.sit, p[0], p[1], p[2], p[3])
        return Answer(text, q.intent, [v.t.id for v in cand[:5]], place, {"approaching": len(cand)})

    def _eta(self, q, my_place, now):
        p = self._place(q, my_place)
        if not p:
            return Answer(NO_PLACE, q.intent)
        cand, r, h = self._candidates(q, p, now)
        head = self._who(p[3], p[2])
        place = {"name": p[2], "lat": p[0], "lon": p[1], "radius_km": r}
        timed = [v for v in cand if v.a.eta_min is not None]
        if not timed:
            text = f"{head}: зараз жодна ціль не прямує в межі {fmt_km(r)} — часу підльоту немає."
        else:
            v = timed[0]
            speed, assumed = v.t.speed_for_estimate()
            spd = f"швидкість {round(speed)} км/год" + (" (типова для цього типу, джерело її не дало)" if assumed else "")
            if v.a.eta_min == 0:
                text = f"{head}: {name_of(v.t)} {where_of(v.t)} вже в межах {fmt_km(r)} ({fmt_km(v.a.distance_km)})."
            else:
                text = (f"{head}: найраніше — {name_of(v.t)} {where_of(v.t)}: увійде в радіус {fmt_km(r)} через "
                        f"{fmt_min(v.a.eta_min)} (≈{clock(now + v.a.eta_min * 60)}), якщо не змінить курс; {spd}. "
                        f"Зараз {fmt_km(v.a.distance_km)} на {compass(v.a.bearing_from_place)}.")
            if len(timed) > 1:
                text += f" Ще {len(timed) - 1} {plural(len(timed) - 1, ('ціль', 'цілі', 'цілей'))} — пізніше."
        text += "\n" + alert_line(self.sit, p[0], p[1], p[2], p[3])
        return Answer(text, q.intent, [v.t.id for v in timed[:3]], place,
                      {"eta_min": round(timed[0].a.eta_min) if timed else None})

    def _alert(self, q, my_place, now):
        if q.oblast_key and not q.place:
            name = oblast_name(self.sit, q.oblast_key)
            keys = oblast_keys(q.oblast_key)
            names = {oblast_name(self.sit, k).lower() for k in keys}
            hits = [a for a in self.sit.alerts["raions"] + self.sit.alerts["oblasts"]
                    if a.get("key") in keys or str(a.get("oblast") or "").lower() in names
                    or str(a.get("name") or "").lower() in names]
            if not hits:
                return Answer(f"{name}: офіційної тривоги зараз немає.", q.intent)
            items = ", ".join(sorted({a.get("name", "") for a in hits}))
            return Answer(f"{name}: тривога — {items}.", q.intent)
        p = self._place(q, my_place)
        if p and (q.place or q.user_place or q.about_me):
            return Answer(alert_line(self.sit, p[0], p[1], p[2], p[3]), q.intent,
                          place={"name": p[2], "lat": p[0], "lon": p[1], "radius_km": None})
        obl = self.sit.alert_oblasts()
        if not obl:
            text = "Зараз офіційних тривог немає ніде."
        else:
            text = f"Тривога зараз у {len(obl)} {plural(len(obl), ('області', 'областях', 'областях'))}: {', '.join(obl)}."
        if p:
            text += "\n" + alert_line(self.sit, p[0], p[1], p[2], p[3])
        return Answer(text, q.intent)

    def _count(self, q, my_place, now):
        tr = usable(self.sit, now, q.types)
        scope = "Зараз на карті"
        p = None
        if q.oblast_key and not q.place:
            tr = [t for t in tr if in_oblast(self.sit, t, q.oblast_key)]
            scope = oblast_name(self.sit, q.oblast_key)
        elif q.place or q.user_place or q.about_me:
            p = self._place(q, my_place)
            if not p:
                return Answer(NO_PLACE, q.intent)
            r = q.radius_km or COUNT_KM
            tr = [t for t in tr if not t.area_only and haversine_km(p[0], p[1], *t.position(now)[:2]) <= r]
            scope = f"{self._who(p[3], p[2])}, у радіусі {fmt_km(r)}"
        total, parts = breakdown(tr)
        if not total:
            what = "цілей" if not q.types else ", ".join(TYPE_FORMS[t][2] for t in sorted(q.types))
            text = f"{scope}: {what} немає."
        else:
            marks = f" (позначок: {len(tr)})" if len(tr) != total else ""
            text = f"{scope}: {total} {plural(total, ('ціль', 'цілі', 'цілей'))}{marks} — {parts}."
        atk = self.sit.mapa_attack
        if atk and not q.oblast_key and p is None and atk.get("status") == "active":
            text += (f"\nЗа підрахунком MAPA.UA від початку атаки (з {clock(float(atk['started_at']))}): "
                     f"дронів — {atk.get('total_drones') or 0}, ракет — {atk.get('total_missiles') or 0}.")
        return Answer(text, q.intent, [t.id for t in tr][:30], facts={"total": total})

    def _overview(self, q, my_place, now):
        tr = usable(self.sit, now, q.types)
        if q.oblast_key and not q.place:
            name = oblast_name(self.sit, q.oblast_key)
            local = [t for t in tr if in_oblast(self.sit, t, q.oblast_key)]
            total, parts = breakdown(local)
            lines = [f"{name}: {total} {plural(total, ('ціль', 'цілі', 'цілей'))}" + (f" — {parts}." if total else ".")]
            for v in views(local, now, None)[:6]:
                lines.append(f"• {track_line(v, now, rel=False)}")
            lines += [f"• {name_of(t)} — {t.region} (без точки)" for t in local if t.area_only][:3]
            al = self._alert(q, my_place, now).text
            lines.append(al)
            return Answer("\n".join(lines), "overview", [t.id for t in local])
        total, parts = breakdown(tr)
        age = self.sit.data_age_s(now)
        lines = [f"Обстановка на {clock(now)}" + (f" (дані NEPTUN, оновлено {ago(age)})" if age is not None else "") + ":"]
        lines.append(f"• На карті {total} {plural(total, ('ціль', 'цілі', 'цілей'))}" + (f": {parts}." if total else "."))
        by_obl: dict[str, int] = {}
        for t in tr:
            if t.region:
                by_obl[t.region] = by_obl.get(t.region, 0) + max(1, t.count)
        if by_obl:
            top = sorted(by_obl.items(), key=lambda kv: -kv[1])[:5]
            lines.append("• Найбільше: " + ", ".join(f"{k} — {v}" for k, v in top) + ".")
        obl = self.sit.alert_oblasts()
        lines.append(f"• Тривога у {len(obl)} {plural(len(obl), ('області', 'областях', 'областях'))}"
                     + (f": {', '.join(obl)}." if obl else "."))
        p = self._place(q, my_place)
        focus = []
        if p:
            vs = sorted(views(tr, now, p[:2]), key=lambda v: v.a.distance_km)
            head = self._who(p[3], p[2])
            if vs:
                lines.append(f"• {head}: найближча ціль — {track_line(vs[0], now)}")
                focus = [vs[0].t.id]
            lines.append("• " + alert_line(self.sit, p[0], p[1], p[2], p[3]))
        return Answer("\n".join(lines), "overview", focus)

    def _safety(self, q, my_place, now):
        p = self._place(q, my_place)
        lines = ["Я не можу сказати, що «безпечно» — я бачу лише позначки на карті з затримкою."]
        if p:
            lines.append("• " + alert_line(self.sit, p[0], p[1], p[2], p[3]))
            vs = sorted(views(usable(self.sit, now, frozenset()), now, p[:2]), key=lambda v: v.a.distance_km)
            if vs:
                lines.append(f"• Найближча ціль — {track_line(vs[0], now)}")
        else:
            lines.append("• " + NO_PLACE)
        lines.append(SAFETY_TAIL)
        return Answer("\n".join(lines), q.intent)

    def _glossary(self, q, my_place, now):
        types = [t for t in TYPE_ORDER if t in q.types and t in GLOSSARY]
        if not types:
            return Answer("Можу пояснити: шахед (БпЛА), FPV-дрон, розвідувальний дрон, крилата ракета, балістика, КАБ, "
                          "МіГ-31К. Напишіть, наприклад: «що таке КАБ?»", q.intent)
        return Answer("\n".join(GLOSSARY[t] for t in types), q.intent)

    def _help(self, q, my_place, now):
        return Answer(HELP, q.intent)

    def _refuse(self, q, my_place, now):
        return Answer(REFUSE, q.intent)

    def _chitchat(self, q, my_place, now):
        low = q.text.lower()
        if SOUND_RE.search(low):
            # звук не визначити, але можна показати, що є поруч — це факти, а не здогадка
            ans = self._around(q, my_place, now)
            ans.text = "Що саме гуде, я визначити не можу. " + ans.text
            return ans
        if any(w in low for w in ("дякую", "спасибі", "дяки", "дякі", "сенкс")):
            return Answer("Будь ласка. Бережіть себе.", "chitchat")
        if any(w in low for w in ("привіт", "добрий", "доброго", "вітаю", "хай", "здоров")):
            return Answer("Привіт! Я відповідаю про повітряну обстановку. Спитайте, наприклад: «де найближчий шахед?»",
                          "chitchat")
        return Answer("Я відповідаю лише про повітряну обстановку за живими даними. Спробуйте: «яка обстановка?», "
                      "«де найближчий шахед?», «чи є тривога в Києві?». «Що ти вмієш?» — повний список.", "chitchat")
