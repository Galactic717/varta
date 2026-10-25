"""Поточна обстановка: треки NEPTUN (основне джерело) + свіжі обʼєкти MAPA.UA, офіційні тривоги, статистика.

Правила свіжості (перевірено на живих даних, див. docs/PLAN.md):
* NEPTUN сам веде життєвий цикл треку (upsert/remove); вік `updatedAt` > 7 хв = «давно не оновлювалась».
* MAPA тримає «active» обʼєкти годинами з замороженою позицією → беремо лише ті, чия ОСТАННЯ ТОЧКА ТРЕЙЛУ
  молодша за 7 хв. Вік позиції, а не час опитування, вирішує.
* Будь-яке поле фіда може бути битим: битий запис пропускається, джерело живе далі.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .geo import DEFAULT_SPEED_KMH, dead_reckon, haversine_km

log = logging.getLogger("varta.situation")
LIVE_S = 420          # як STALE_AFTER_SECONDS у SDK NEPTUN
USABLE_S = 1800       # старіші за 30 хв не беремо у відповіді про відстані
MATCH_KM = 30         # MAPA-обʼєкт ↔ трек NEPTUN: базовий шлюз…
MATCH_KM_MAX = 60     # …і стеля, навіть якщо NEPTUN каже «±100 км»
NEAR_PLACE_KM = 20    # «біля н. п. X» лише якщо X справді поруч

MAPA_KIND = {"drone_jet": "uav", "drone_piston": "uav", "banderol": "uav", "drone_fpv": "fpv",
             "drone_recon": "recon", "missile_cruise": "missile", "missile_ballistic": "ballistic",
             "ballistic": "ballistic", "bomb": "kab", "aviation": "mig31k"}


def num(v) -> float | None:
    """Число з фіда або None (рядки, null, NaN, нескінченність — відкидаємо)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_ts(v) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return num(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Track:
    id: str
    type: str
    title: str
    lat: float
    lon: float
    updated_at: float
    heading: float | None = None
    speed_kmh: float | None = None       # з velocity джерела; None = невідома
    confirmed_at: float | None = None
    region: str = ""
    district: str = ""
    locality: str = ""
    status: str = "active"
    confidence: str = "medium"
    source_count: int = 1
    count: int = 0
    uncertainty_km: float = 5.0
    quality: str = "approx"              # confirmed | approx
    area_only: bool = False              # точки немає, лише область
    advisory: bool = False               # спостереження, не сигнал ховатися
    destination: bool = False            # мітка поставлена перед названим пунктом (курс «на X»)
    presumptive: bool = False            # курс припущений
    explanation: str = ""
    trail: list = field(default_factory=list)       # [(lat, lon, t)]
    sources: tuple = ("neptun",)
    target_name: str = ""                # куди летить (MAPA to_city)

    def age_s(self, now: float) -> float:
        return now - self.updated_at

    def position(self, now: float) -> tuple[float, float, bool]:
        """Поточна оцінка позиції (dead-reckoning як у NEPTUN) і чи мітка ще «летить»."""
        if self.area_only or self.status != "active" or self.speed_kmh is None or self.heading is None:
            return self.lat, self.lon, False
        anchor = self.confirmed_at or self.updated_at
        return dead_reckon(self.type, self.lat, self.lon, self.heading, self.speed_kmh, (now - anchor) / 60)

    def speed_for_estimate(self) -> tuple[float, bool]:
        """(швидкість, чи це типове значення, а не з даних)."""
        if self.speed_kmh:
            return self.speed_kmh, False
        return DEFAULT_SPEED_KMH.get(self.type, 150), True


def neptun_track(d) -> Track | None:
    """Трек з запису NEPTUN; битий запис → None (пропускаємо один запис, а не все джерело)."""
    try:
        if not isinstance(d, dict):
            return None
        lat, lon, upd = num(d.get("lat")), num(d.get("lon")), parse_ts(d.get("updatedAt"))
        if lat is None or lon is None or upd is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        vel = d.get("velocity") if isinstance(d.get("velocity"), dict) else {}
        heading = num(vel["bearingDeg"]) if vel.get("bearingDeg") is not None else num(d.get("heading"))
        trail = []
        for p in d.get("trail") or []:
            if isinstance(p, dict) and num(p.get("lat")) is not None and num(p.get("lon")) is not None:
                trail.append((num(p["lat"]), num(p["lon"]), parse_ts(p.get("t")) or 0))
        return Track(
            id="N" + str(d["id"]), type=str(d.get("type") or "unknown"), title=str(d.get("title") or ""),
            lat=lat, lon=lon, updated_at=upd, heading=heading, speed_kmh=num(vel.get("speedKmh")),
            confirmed_at=parse_ts(d.get("confirmedAt")), region=str(d.get("region") or ""),
            district=str(d.get("district") or ""), locality=str(d.get("locality") or ""),
            status=str(d.get("status") or "active"),
            confidence=str(d.get("displayConfidence") or d.get("confidenceLevel") or "medium"),
            source_count=int(num(d.get("sourceCount")) or 1), count=int(num(d.get("count")) or 0),
            uncertainty_km=num(d.get("uncertaintyKm")) or 5.0, quality=str(d.get("positionQuality") or "approx"),
            area_only=bool(d.get("areaOnly")), advisory=bool(d.get("advisory")),
            destination=bool(d.get("destination")), presumptive=bool(d.get("presumptiveCourse")),
            explanation=str(d.get("explanationShort") or ""), trail=trail,
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class Situation:
    def __init__(self, regions=None, gazetteer=None, mapa_cities: dict | None = None, log_dir: Path | None = None):
        self.regions, self.gazetteer = regions, gazetteer
        self.mapa_cities = mapa_cities or {}
        self.log_dir = log_dir
        self.neptun: dict[str, Track] = {}
        self.mapa_tracks: list[Track] = []
        self.mapa_attack: dict | None = None
        self.alerts: dict = {"raions": [], "oblasts": []}
        self.alerts_at = 0.0
        self.source_state = {"neptun": {"ok": False, "at": 0.0}, "mapa": {"ok": False, "at": 0.0}}
        self._log_f, self._log_day = None, ""

    # ---------- приймання даних ----------
    def _log(self, frame: dict) -> None:
        """Власний архів фреймів NEPTUN. Файл тримаємо відкритим (USB-диск); збій диска не валить джерело."""
        if not self.log_dir:
            return
        try:
            day = time.strftime("%Y-%m-%d")
            if day != self._log_day:
                if self._log_f:
                    self._log_f.close()
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._log_f = gzip.open(self.log_dir / f"neptun-{day}.jsonl.gz", "at", encoding="utf-8")
                self._log_day = day
            self._log_f.write(json.dumps(frame, ensure_ascii=False) + "\n")
            self._log_f.flush()
        except OSError as e:
            log.warning("журнал NEPTUN: %s — вимикаю запис", e)
            self.log_dir = None

    def mark(self, source: str, ok: bool) -> None:
        self.source_state[source] = {"ok": ok, "at": time.time() if ok else self.source_state[source]["at"]}

    def apply_neptun_frame(self, frame) -> None:
        if not isinstance(frame, dict):
            return
        kind, data = frame.get("type"), frame.get("data")
        if kind in ("snapshot", "upsert", "remove", "alerts"):
            self._log(frame)
        if kind == "snapshot" and isinstance(data, dict):
            self.set_neptun_threats(data.get("threats") or [])
        elif kind == "upsert":
            t = neptun_track(data)
            if t:
                self.neptun[t.id] = self._fill(t)
        elif kind == "remove" and isinstance(data, dict):
            self.neptun.pop("N" + str(data.get("id")), None)
        elif kind == "alerts" and isinstance(data, dict):
            self.set_alerts(data)
        self.mark("neptun", True)

    def set_neptun_threats(self, threats) -> None:
        tracks = (neptun_track(d) for d in (threats if isinstance(threats, list) else []))
        self.neptun = {t.id: self._fill(t) for t in tracks if t}   # нова dict цілком — читачі бачать стару або нову
        self.mark("neptun", True)

    def _fill(self, t: Track) -> Track:
        """NEPTUN інколи не дає область — визначаємо за координатами."""
        if not t.region and self.regions and not t.area_only:
            t.region = self.regions.locate(t.lat, t.lon)["oblast"]
        return t

    def set_alerts(self, data: dict) -> None:
        def clean(v):
            return [a for a in v if isinstance(a, dict)] if isinstance(v, list) else []
        self.alerts = {"raions": clean(data.get("raions")), "oblasts": clean(data.get("oblasts"))}
        self.alerts_at = time.time()

    def set_mapa(self, current) -> None:
        if not isinstance(current, dict):
            return
        now, tracks = time.time(), []
        for o in current.get("objects") or []:
            try:
                t = self._mapa_track(o, now)
            except (KeyError, TypeError, ValueError, AttributeError, IndexError):
                t = None                      # один битий обʼєкт MAPA не має валити все джерело
            if t:
                tracks.append(t)
        self.mapa_tracks = tracks
        self.mapa_attack = current.get("attack") if isinstance(current.get("attack"), dict) else None
        self.mark("mapa", True)

    # ---------- злиття ----------
    def _mapa_track(self, o: dict, now: float) -> Track | None:
        # трейл MAPA буває з точками в МАЙБУТНЬОМУ (їхній прогноз) — позиція = остання точка не пізніше «зараз»
        trail = []
        for p in o.get("trail") or []:
            if isinstance(p, (list, tuple)) and len(p) >= 3 and None not in (num(p[0]), num(p[1]), num(p[2])):
                if num(p[2]) <= now + 30:
                    trail.append((num(p[1]), num(p[0]), num(p[2])))      # (lat, lon, t)
        if o.get("status") != "active" or not trail or now - trail[-1][2] > LIVE_S:
            return None
        lat, lon, pos_t = trail[-1]
        type_ = MAPA_KIND.get(o.get("subkind") or "", MAPA_KIND.get(o.get("kind") or "", "unknown"))
        loc = self.regions.locate(lat, lon) if self.regions else {}
        near = self.gazetteer.nearest(lat, lon, NEAR_PLACE_KM) if self.gazetteer else None
        to = self.mapa_cities.get(o.get("to_city") or "", {}).get("ua", "")
        return Track(
            # заголовок MAPA — вільний текст («Маневрує у бік Дніпра») → в пояснення, назва — за типом
            id=f"M{o['id']}", type=type_, title="", explanation=str(o.get("title") or ""), lat=lat, lon=lon,
            updated_at=pos_t, heading=num(o.get("heading")), speed_kmh=num(o.get("speed_kmh")), confirmed_at=pos_t,
            region=loc.get("oblast", ""), locality=near.name if near else "", confidence="low", quality="approx",
            count=max(1, int(num(o.get("amount")) or 1)), uncertainty_km=10.0,
            trail=trail[-40:], sources=("mapa",), target_name=to,
        )

    def tracks(self, now: float | None = None) -> list[Track]:
        """Злиті треки: NEPTUN + свіжі MAPA. MAPA поруч із треком NEPTUN того ж типу → лише збагачує його."""
        now = now or time.time()
        # list(...) — знімок: /api/ask читає з потоку, поки цикл подій застосовує upsert/remove
        out = {k: replace(t) for k, t in list(self.neptun.items()) if t.status != "resolved"}
        absorbed: dict[str, int] = {}
        for m in list(self.mapa_tracks):
            if now - m.updated_at > LIVE_S:
                continue
            m = replace(m)
            best, best_d = None, float("inf")
            for t in out.values():
                if "neptun" not in t.sources or t.area_only or t.type != m.type:
                    continue
                if absorbed.get(t.id, 0) >= max(1, t.count):      # трек-група ×N поглинає не більше N обʼєктів
                    continue
                gate = min(max(MATCH_KM, t.uncertainty_km), MATCH_KM_MAX)
                d = haversine_km(m.lat, m.lon, t.lat, t.lon)
                if d < gate and d < best_d:
                    best, best_d = t, d
            if best:
                absorbed[best.id] = absorbed.get(best.id, 0) + 1
                best.sources = tuple(sorted(set(best.sources) | {"mapa"}))
                if not best.trail and m.trail:
                    best.trail = m.trail
                if best.speed_kmh is None and m.speed_kmh and m.heading is not None:
                    best.speed_kmh = m.speed_kmh
                    if best.heading is None:
                        best.heading = m.heading
                    best.confirmed_at = best.confirmed_at or best.updated_at
                best.target_name = best.target_name or m.target_name
            else:
                out[m.id] = m
        return list(out.values())

    # ---------- тривоги ----------
    def alert_at(self, lat: float, lon: float) -> dict | None:
        """Офіційна тривога в районі/області точки або None."""
        if not self.regions:
            return None
        loc = self.regions.locate(lat, lon)
        for a in self.alerts["raions"]:
            if loc["raion_key"] and a.get("key") == loc["raion_key"]:
                return {**a, "scope": "raion"}
        for a in self.alerts["oblasts"]:
            if loc["oblast_key"] and a.get("key") == loc["oblast_key"]:
                return {**a, "scope": "oblast"}
        return None

    def alert_oblasts(self) -> list[str]:
        """Області з будь-якою тривогою (для «де зараз тривога»)."""
        names = {a.get("oblast") or a.get("name") for a in self.alerts["raions"] + self.alerts["oblasts"]}
        return sorted(str(n) for n in names if n)

    def data_age_s(self, now: float | None = None) -> float | None:
        at = self.source_state["neptun"]["at"]
        return (now or time.time()) - at if at else None
