"""Райони й області України (межі з NEPTUN raions/oblasts.geojson — їхні key збігаються з /api/v1/alerts)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

GEO_URLS = {"raions": "https://neptun.in.ua/raions.geojson", "oblasts": "https://neptun.in.ua/oblasts.geojson"}


def load_geojson(geo_dir: Path, name: str) -> dict:
    path = geo_dir / f"{name}.geojson"
    if not path.exists():
        geo_dir.mkdir(parents=True, exist_ok=True)
        resp = httpx.get(GEO_URLS[name], timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return json.loads(path.read_text(encoding="utf-8"))


class Regions:
    def __init__(self, geo_dir: Path):
        self.raions_fc = load_geojson(geo_dir, "raions")
        self.oblasts_fc = load_geojson(geo_dir, "oblasts")
        self._r = [(f["properties"]["key"], f["properties"].get("rayon", ""), shape(f["geometry"]))
                   for f in self.raions_fc["features"]]
        self._o = [(f["properties"]["key"], f["properties"].get("region", ""), shape(f["geometry"]))
                   for f in self.oblasts_fc["features"]]
        self._rt = STRtree([g for *_, g in self._r])
        self._ot = STRtree([g for *_, g in self._o])
        self.oblast_names = {k: n or ("м. Київ" if k == "м. київ" else k.capitalize()) for k, n, _ in self._o}
        self._ukraine = None

    @staticmethod
    def _hit(tree: STRtree, items: list, lat: float, lon: float):
        pt = Point(lon, lat)
        for i in tree.query(pt, predicate="intersects"):
            return items[int(i)]
        return None

    def locate(self, lat: float, lon: float) -> dict:
        """{'oblast_key', 'oblast', 'raion_key', 'raion'} або порожні рядки поза Україною."""
        o = self._hit(self._ot, self._o, lat, lon)
        r = self._hit(self._rt, self._r, lat, lon)
        return {"oblast_key": o[0] if o else "", "oblast": o[1] if o else "",
                "raion_key": r[0] if r else "", "raion": r[1] if r else ""}

    def ukraine_geojson(self) -> dict:
        """Контур України (обʼєднання областей) — для затемнення світу навколо."""
        if self._ukraine is None:
            geom = unary_union([g.buffer(0.01) for *_, g in self._o]).buffer(-0.01).simplify(0.01)
            self._ukraine = {"type": "Feature", "properties": {}, "geometry": mapping(geom)}
        return self._ukraine
