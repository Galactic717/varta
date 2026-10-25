"""Перевірка прогнозу «чи увійде ціль у радіус R за H хвилин» на архіві атак MAPA.UA (data/archive).

Для точки трейлу беремо поточну позицію, курс (за двома останніми точками) і швидкість, кладемо місця навколо
(частина — біля майбутнього маршруту, частина — випадково в 150 км) і порівнюємо ймовірність моделі з тим, чи
маршрут справді пройшов у межах R. Атаки з id % 5 == 0 — тест, решта — підбір σ (похибки курсу).

Застереження: трейли MAPA — їхня оцінка маршруту, а не радарна правда; це перевірка узгодженості, не точності.
Вихід: docs/forecast_eval.json
"""
import gzip
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from varta.geo import Motion, approach, bearing_deg, dest, haversine_km  # noqa: E402

UAV = {"drone_piston", "drone_jet"}
R_KM, HORIZONS = 20.0, (30.0, 60.0)
SIGMAS = (10, 15, 20, 25, 30, 40, 50, 60)


def entered(trail, t0, horizon_min, lat, lon, r_km) -> bool:
    """Чи проходить маршрут (лінійна інтерполяція між точками) у межах r_km від місця за [t0, t0+H]."""
    end = t0 + horizon_min * 60
    pts = [(la, lo, t) for lo, la, t in trail if t0 <= t <= end]
    for (a1, o1, _), (a2, o2, _) in zip(pts, pts[1:]):
        for f in (0, 0.25, 0.5, 0.75, 1):
            if haversine_km(lat, lon, a1 + (a2 - a1) * f, o1 + (o2 - o1) * f) <= r_km:
                return True
    return any(haversine_km(lat, lon, a, o) <= r_km for a, o, _ in pts)


def samples(rng: random.Random):
    for f in sorted((ROOT / "data" / "archive").glob("attack_*.json.gz")):
        d = json.load(gzip.open(f, "rt", encoding="utf-8"))
        test = int(d["attack"]["id"]) % 5 == 0 if d.get("attack") else False
        for o in d.get("objects") or []:
            tr = o.get("trail") or []
            if o.get("kind") not in UAV or len(tr) < 6 or tr[-1][2] - tr[0][2] < 1800:
                continue
            for k in rng.sample(range(2, len(tr) - 2), min(3, len(tr) - 4)):
                (o1, a1, t1), (o2, a2, t2) = tr[k - 1], tr[k]
                if t2 <= t1:
                    continue
                km = haversine_km(a1, o1, a2, o2)
                speed = km / ((t2 - t1) / 3600)
                if not 60 <= speed <= 700:
                    continue
                head = bearing_deg(a1, o1, a2, o2)
                future = [p for p in tr[k:] if p[2] - t2 <= 3600]
                places = []
                for p in rng.sample(future, min(2, len(future))):          # біля майбутнього маршруту
                    places.append(dest(p[1], p[0], rng.uniform(0, 360), rng.uniform(0, 30)))
                for _ in range(4):                                         # випадкові в 150 км
                    places.append(dest(a2, o2, rng.uniform(0, 360), 150 * math.sqrt(rng.random())))
                for la, lo in places:
                    for h in HORIZONS:
                        yield test, (a2, o2, head, speed), (la, lo), h, entered(tr, t2, h, la, lo, R_KM)


def brier(rows, sigma):
    s = 0.0
    for (a, o, head, speed), (la, lo), h, y in rows:
        p = approach(Motion(a, o, head, speed, sigma), la, lo, R_KM, h).p_enter or 0.0
        s += (p - y) ** 2
    return s / max(1, len(rows))


def main() -> None:
    rng = random.Random(0)
    train, test = [], []
    for is_test, m, place, h, y in samples(rng):
        (test if is_test else train).append((m, place, h, y))
    base = sum(y for *_, y in train) / len(train)
    grid = {s: round(brier(train, s), 4) for s in SIGMAS}
    best = min(grid, key=grid.get)
    prior_test = sum((base - y) ** 2 for *_, y in test) / len(test)
    res = {"radius_km": R_KM, "horizons_min": HORIZONS, "n_train": len(train), "n_test": len(test),
           "positive_rate_train": round(base, 3), "brier_train_by_sigma": grid, "best_sigma": best,
           "brier_test_physics": round(brier(test, best), 4), "brier_test_sigma20": round(brier(test, 20), 4),
           "brier_test_prior": round(prior_test, 4)}
    (ROOT / "docs" / "forecast_eval.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
