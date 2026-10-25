"""Агент питань-відповідей: модель розуміє питання, код рахує відповідь."""
from __future__ import annotations

import re
import time
from typing import Callable

from ..gazetteer import Gazetteer, Place
from ..situation import Situation
from .answer import Executor
from .lexicon import INTENTS
from .parse import Query, UserPlace, extract, rule_intent

FOLLOW_RE = re.compile(r"^\s*(а|і|й|ще|та|ну)\b|\b(там|туди|звідти|тут|теж|також)\b", re.I)
# еліптичне уточнення: у маскованій формі лишились тільки слоти — «а <ціль>?», «а в <місце>?», «ракети?»
ELLIPSIS_RE = re.compile(r"^\s*(а|і|й|ну|ще|та)?\s*(у|в|на|біля|до|для|по)?\s*"
                         r"(<ціль>|<місце>|<область>|<n> км)(\s*(і|та|,)\s*(<ціль>|<місце>|<область>))*\s*[?!.…]*\s*$")
PLACE_WORDS_RE = re.compile(r"\b(там|туди|звідти|тут)\b", re.I)
PLACE_INTENTS = {"nearest", "around", "approaching", "eta", "alert", "count", "safety"}


class Agent:
    def __init__(self, situation: Situation, gazetteer: Gazetteer,
                 classifier: Callable[[str], tuple[str, float]] | None = None):
        self.sit, self.gaz = situation, gazetteer
        self.classify = classifier or rule_intent
        self.executor = Executor(situation)

    def parse(self, text: str, my_place: dict | None = None, places: list[dict] | None = None,
              ctx: dict | None = None) -> Query:
        ups = [UserPlace(p["name"], float(p["lat"]), float(p["lon"])) for p in places or [] if p.get("name")]
        near = (float(my_place["lat"]), float(my_place["lon"])) if my_place and my_place.get("lat") is not None else None
        q = extract(text, self.gaz, ups, near)
        q.intent, q.confidence = self.classify(q.masked)
        if ctx:
            self._inherit(q, ctx, near is not None)
        return q

    @staticmethod
    def _inherit(q: Query, ctx: dict, has_my_place: bool) -> None:
        """Уточнення («а ракети?», «а там тривога є?») беруть пропущене з попереднього питання.

        Рішення — за формою питання: «а <ціль>?» — уточнення, «де найближчий шахед?» — нове питання, навіть коротке.
        """
        ellipsis = bool(ELLIPSIS_RE.match(q.masked))
        if not (ellipsis or FOLLOW_RE.search(q.text)):
            return
        if ellipsis and ctx.get("intent") in INTENTS and ctx["intent"] not in ("chitchat", "help", "refuse"):
            q.intent = ctx["intent"]
            q.inherited.append("intent")
        no_place = not (q.place or q.user_place or q.oblast_key or q.about_me)
        # місце з попереднього питання — лише для явного уточнення; «моє місце» не перебиваємо без «там/туди»
        place_ok = ellipsis or bool(PLACE_WORDS_RE.search(q.text)) or not has_my_place
        if no_place and place_ok and q.intent in PLACE_INTENTS | {"overview"}:
            if ctx.get("place"):
                p = ctx["place"]
                q.place = Place(p["name"], float(p["lat"]), float(p["lon"]), "ctx", 0, "", "")
                q.inherited.append("place")
            elif ctx.get("oblast_key"):
                q.oblast_key = ctx["oblast_key"]
                q.inherited.append("oblast")
        if not q.types and ctx.get("types") and q.intent == ctx.get("intent"):
            q.types = frozenset(ctx["types"])
            q.inherited.append("types")

    def ask(self, text: str, my_place: dict | None = None, places: list[dict] | None = None,
            ctx: dict | None = None, now: float | None = None) -> dict:
        t0 = time.perf_counter()
        q = self.parse(text, my_place, places, ctx)
        ans = self.executor.run(q, my_place, now)
        explicit = q.place if q.place and q.place.kind != "ctx" else None
        new_ctx = {"intent": ans.intent, "types": sorted(q.types), "oblast_key": q.oblast_key,
                   "place": ({"name": q.place.name, "lat": q.place.lat, "lon": q.place.lon}
                             if q.place else (ctx or {}).get("place") if "place" in q.inherited else None)}
        if explicit is None and q.user_place:
            new_ctx["place"] = {"name": q.user_place.name, "lat": q.user_place.lat, "lon": q.user_place.lon}
        return {
            "answer": ans.text, "intent": ans.intent, "confidence": round(q.confidence, 3),
            "focus": ans.focus, "place": ans.place, "ctx": new_ctx,
            "parsed": {"masked": q.masked, "types": sorted(q.types), "place": q.place.name if q.place else None,
                       "oblast": q.oblast_key or None, "about_me": q.about_me, "radius_km": q.radius_km,
                       "horizon_min": q.horizon_min, "inherited": q.inherited},
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
