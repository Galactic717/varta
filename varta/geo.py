"""Геометрія на сфері, dead-reckoning і оцінка підльоту до точки.

Константи горизонтів узято з публічного SDK NEPTUN (https://neptun.in.ua/sdk/neptun.js), щоб мітки
рухались так само, як на їхній карті.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

R_EARTH_KM = 6371.0

# Скільки хвилин після останньої засічки мітку ще можна рухати за курсом і на скільки км максимум.
FLY_HORIZON_MIN = {"uav": 12, "recon": 12, "fpv": 10, "missile": 5, "kab": 4, "ballistic": 1.5,
                   "mig31k": 6, "unknown": 6}
MAX_GHOST_KM = {"uav": 18, "recon": 12, "fpv": 10, "missile": 30, "kab": 10, "ballistic": 20,
                "mig31k": 24, "unknown": 10}
# Типова швидкість, коли джерело її не дало (км/год). Шахед ~150-185, крилата ракета ~750-900.
DEFAULT_SPEED_KMH = {"uav": 160, "recon": 120, "fpv": 70, "missile": 800, "kab": 450, "ballistic": 3000,
                     "mig31k": 0, "unknown": 150}

COMPASS_8 = ("північ", "північний схід", "схід", "південний схід",
             "південь", "південний захід", "захід", "північний захід")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Азимут з точки 1 на точку 2, 0 = північ, за годинниковою."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def dest(lat: float, lon: float, bearing: float, km: float) -> tuple[float, float]:
    if km <= 0:
        return lat, lon
    d = km / R_EARTH_KM
    b, p1, l1 = math.radians(bearing), math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def compass(bearing: float) -> str:
    return COMPASS_8[int(((bearing % 360) + 22.5) // 45) % 8]


def angle_diff(a: float, b: float) -> float:
    """Знакова різниця кутів a-b у діапазоні (-180, 180]."""
    d = (a - b + 180) % 360 - 180
    return 180.0 if d == -180 else d


@dataclass(frozen=True)
class Motion:
    """Вхід для оцінки: де ціль зараз (вже з dead-reckoning), куди і як швидко летить."""
    lat: float
    lon: float
    heading: float | None      # None = курс невідомий
    speed_kmh: float
    heading_sigma_deg: float   # невизначеність курсу


def dead_reckon(type_: str, lat: float, lon: float, bearing: float | None, speed_kmh: float | None,
                age_min: float) -> tuple[float, float, bool]:
    """Логіка predict() з SDK NEPTUN: зсув за курсом, обмежений горизонтом і MAX_GHOST_KM.

    Повертає (lat, lon, ще_летить).
    """
    if bearing is None or not speed_kmh or age_min <= 0:
        return lat, lon, False
    horizon = FLY_HORIZON_MIN.get(type_, 6)
    km = min(MAX_GHOST_KM.get(type_, 8), speed_kmh * min(age_min, horizon) / 60)
    la, lo = dest(lat, lon, bearing, km)
    return la, lo, age_min <= horizon and km > 0.05


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass(frozen=True)
class Approach:
    distance_km: float          # зараз від місця до цілі
    bearing_from_place: float   # де ціль відносно місця
    closing: bool | None        # наближається? None = курс невідомий
    cpa_km: float | None        # найменша відстань, якщо курс не зміниться
    t_cpa_min: float | None     # коли це станеться
    eta_min: float | None       # коли увійде в радіус (за незмінного курсу)
    p_enter: float | None       # ймовірність увійти в радіус за горизонт (модель похибки курсу)


def approach(m: Motion, place_lat: float, place_lon: float, radius_km: float, horizon_min: float) -> Approach:
    """Найближче проходження (CPA) і ймовірність пролету в радіусі.

    Модель: ціль летить прямо з похибкою курсу ε ~ N(0, σ). Вона увійде в коло радіуса R на дистанції D,
    якщо відхилення курсу від напрямку «на місце» лежить у ±asin(R/D). Час — (D−R)/v має вкластися в горизонт.
    """
    d = haversine_km(place_lat, place_lon, m.lat, m.lon)
    from_place = bearing_deg(place_lat, place_lon, m.lat, m.lon)
    if m.heading is None or m.speed_kmh <= 0:
        inside = d <= radius_km
        return Approach(d, from_place, None, None, None, 0.0 if inside else None, 1.0 if inside else None)

    to_place = bearing_deg(m.lat, m.lon, place_lat, place_lon)   # не from_place+180: меридіани сходяться
    off = math.radians(angle_diff(to_place, m.heading))
    along = d * math.cos(off)             # скільки пролетить до точки найближчого проходження
    closing = along > 0
    cpa = abs(d * math.sin(off)) if closing else d
    t_cpa = along / m.speed_kmh * 60 if closing else 0.0

    eta = None
    if d <= radius_km:
        eta = 0.0
    elif closing and cpa <= radius_km:
        eta = (along - math.sqrt(radius_km ** 2 - cpa ** 2)) / m.speed_kmh * 60

    if d <= radius_km:
        p = 1.0
    elif (d - radius_km) / m.speed_kmh * 60 > horizon_min:
        p = 0.0
    else:
        alpha = math.asin(min(1.0, radius_km / d))
        sigma = math.radians(max(m.heading_sigma_deg, 1.0))
        p = _phi((off + alpha) / sigma) - _phi((off - alpha) / sigma)
    return Approach(d, from_place, closing, cpa, t_cpa, eta, max(0.0, min(1.0, p)))
