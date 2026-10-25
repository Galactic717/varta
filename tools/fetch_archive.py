"""Завантажує архів атак MAPA.UA (публічний API, без ключів) у data/archive/attack_<id>.json.gz.

Джерело: https://mapa.ua/api/v1/attacks + /objects (документація https://mapa.ua/docs/).
Дані — для навчання/оцінки прогнозного мозку (tools/build_dataset.py). Дані MAPA.UA мають
посилання-атрибуцію (див. README). Запити послідовні, з паузою, щоб не навантажувати сервіс.

Запуск:  python tools/fetch_archive.py [--limit 255] [--pause 1.0]
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import httpx

API = "https://mapa.ua/api/v1"
OUT = Path(__file__).resolve().parents[1] / "data" / "archive"
KEEP = ("id", "kind", "subkind", "status", "amount", "heading", "lat", "lon", "first_seen",
        "last_seen", "from_zone", "to_city", "speed_kmh", "title")


def compact(obj: dict) -> dict:
    row = {k: obj.get(k) for k in KEEP}
    row["trail"] = [[round(p[0], 4), round(p[1], 4), int(p[2])] for p in obj.get("trail") or [] if len(p) >= 3]
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "VARTA-FUSION-AI/0.2 (research; archive fetch)"}
    with httpx.Client(timeout=90, headers=headers, follow_redirects=True) as http:
        attacks = http.get(f"{API}/attacks", params={"limit": args.limit}).json()["attacks"]
        (OUT / "attacks.json").write_text(json.dumps(attacks, ensure_ascii=False), encoding="utf-8")
        todo = [a for a in attacks if a.get("status") == "ended"]
        print(f"атак у списку: {len(attacks)}, завершених: {len(todo)}", flush=True)
        done = 0
        for att in sorted(todo, key=lambda a: a["started_at"]):
            path = OUT / f"attack_{att['id']}.json.gz"
            if path.exists():
                continue
            for attempt in range(3):
                try:
                    r = http.get(f"{API}/objects", params={"attack_id": att["id"]})
                    r.raise_for_status()
                    objs = [compact(o) for o in r.json().get("objects", [])]
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"  attack {att['id']} спроба {attempt + 1}: {exc}", flush=True)
                    time.sleep(5 * (attempt + 1))
            else:
                continue
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                json.dump({"attack": att, "objects": objs}, fh, ensure_ascii=False)
            done += 1
            print(f"attack {att['id']}: {len(objs)} об'єктів ({done}/{len(todo)})", flush=True)
            time.sleep(args.pause)
    return 0


if __name__ == "__main__":
    sys.exit(main())
