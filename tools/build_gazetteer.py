"""Збирає газетир населених пунктів України з GeoNames (CC-BY 4.0) → data/geo/places.json.

Джерела (завантажити в data/geo/_src і розпакувати в main/ та alt/):
  https://download.geonames.org/export/dump/UA.zip
  https://download.geonames.org/export/dump/alternatenames/UA.zip
Запис: [назва_uk, lat, lon, код_типу, населення, oblast_key, raion_key].
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from varta.regions import Regions  # noqa: E402

SRC = ROOT / "data" / "geo" / "_src"
UK_ONLY = set("іїєґІЇЄҐ")
RU_ONLY = set("ыэъёЫЭЪЁ")
CYR = re.compile("[а-яіїєґ]", re.I)


def uk_names() -> dict[str, tuple[int, str]]:
    """geonameid → (пріоритет, українська назва). Пріоритет: preferred > звичайна; історичні/розмовні — ні."""
    best: dict[str, tuple[int, str]] = {}
    with open(SRC / "alt" / "UA.txt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 8 or c[2] != "uk" or c[6] == "1" or c[7] == "1" or not CYR.search(c[3]):
                continue
            rank = 2 if c[4] == "1" else 1
            if rank > best.get(c[1], (0, ""))[0]:
                best[c[1]] = (rank, c[3])
    return best


def fallback_name(alternates: str) -> str:
    """Кирилична назва з українськими літерами, якщо явної 'uk' немає."""
    for n in alternates.split(","):
        if any(ch in UK_ONLY for ch in n) and not any(ch in RU_ONLY for ch in n):
            return n
    return ""


def main() -> None:
    regions = Regions(ROOT / "data" / "geo")
    names = uk_names()
    out, stats = [], {"no_name": 0, "outside": 0}
    with open(SRC / "main" / "UA.txt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if c[6] != "P":
                continue
            name = names.get(c[0], (0, ""))[1] or fallback_name(c[3])
            if not name:
                stats["no_name"] += 1
                continue
            lat, lon = float(c[4]), float(c[5])
            loc = regions.locate(lat, lon)
            if not loc["oblast_key"]:
                stats["outside"] += 1
                continue
            out.append([name, round(lat, 5), round(lon, 5), c[7], int(c[14] or 0),
                        loc["oblast_key"], loc["raion_key"]])
    path = ROOT / "data" / "geo" / "places.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(out), stats, path)


if __name__ == "__main__":
    main()
