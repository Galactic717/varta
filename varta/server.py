"""HTTP-сервер: карта (web/) і API. Слухає 127.0.0.1 за замовчуванням."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time

import httpx
from aiohttp import web

from .config import Config
from .gazetteer import Gazetteer
from .qa import Agent
from .gazetteer import OBLAST_FORMS
from .qa.intent import load_classifier
from .qa.lexicon import INTENTS, TYPE_FORMS
from .regions import Regions
from .situation import Situation, Track
from .sources import run_mapa, run_neptun, supervise

log = logging.getLogger("varta.server")
MAX_Q = 300
OBLAST_KEYS = set(OBLAST_FORMS)
EXPL_TAIL = re.compile(r"\s*Підтверджень:\s*\d+\.?")


def track_json(t: Track) -> dict:
    return {
        "id": t.id, "type": t.type, "title": t.title, "lat": t.lat, "lon": t.lon, "heading": t.heading,
        "speed": t.speed_kmh, "confirmedAt": (t.confirmed_at or t.updated_at) * 1000, "updatedAt": t.updated_at * 1000,
        "status": t.status, "region": t.region, "locality": t.locality, "confidence": t.confidence,
        "sourceCount": t.source_count, "count": t.count, "quality": t.quality, "uncertaintyKm": t.uncertainty_km,
        "areaOnly": t.area_only, "advisory": t.advisory, "destination": t.destination, "presumptive": t.presumptive,
        "sources": list(t.sources), "explanation": EXPL_TAIL.sub("", t.explanation).strip(), "target": t.target_name,
        # лише останні 30 хв сліду: довгі «хвости» з Росії захаращують карту
        "trail": [[round(lon, 5), round(lat, 5)] for lat, lon, ts in t.trail[-60:] if not ts or t.updated_at - ts <= 1800],
    }


def _float(v, lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and lo <= f <= hi else None


def _place(d) -> dict | None:
    if not isinstance(d, dict):
        return None
    lat, lon = _float(d.get("lat"), -90, 90), _float(d.get("lon"), -180, 180)
    if lat is None or lon is None:
        return None
    return {"lat": lat, "lon": lon, "name": str(d.get("name") or "Моє місце")[:60]}


def _ctx(d) -> dict | None:
    """Контекст діалогу від клієнта — лише відомі значення (намір, типи, область, місце)."""
    if not isinstance(d, dict):
        return None
    types = d.get("types") if isinstance(d.get("types"), list) else []
    ok = d.get("oblast_key") if isinstance(d.get("oblast_key"), str) and d.get("oblast_key") in OBLAST_KEYS else ""
    return {"intent": d.get("intent") if d.get("intent") in INTENTS else None,
            "types": [t for t in types if t in TYPE_FORMS], "oblast_key": ok,
            "place": _place(d.get("place"))}


def load_mapa_cities(path, cfg: Config) -> dict:
    """Довідник MAPA slug → назва (для «прямує на X»). Немає файлу — один раз завантажуємо; не вдалось — без назв."""
    if not path.exists() and not cfg.offline:
        try:
            r = httpx.get(cfg.mapa_base + "/api/v1/geo/cities", timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
        except (httpx.HTTPError, OSError) as e:
            log.warning("довідник міст MAPA не завантажився: %s", e)
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("cities", {}) if path.exists() else {}
    except (ValueError, OSError):
        return {}


def build_app(cfg: Config) -> web.Application:
    geo = cfg.data_dir / "geo"
    regions = Regions(geo)
    gaz = Gazetteer(geo / "places.json")
    cities = load_mapa_cities(geo / "mapa_cities.json", cfg)
    sit = Situation(regions, gaz, cities, log_dir=(cfg.data_dir / "log") if cfg.log_events else None)
    agent = Agent(sit, gaz, None)          # до завантаження моделі працюють правила
    app_model = {"name": "правила (модель завантажується)"}

    def load_model() -> None:
        # mmBERT вантажиться ~20 с з USB-диска: карта стартує одразу, агент перемикається, щойно модель готова
        for d in (cfg.model_dir, cfg.model_dir.parent / "intent_ngram"):
            try:
                clf = load_classifier(d)
            except Exception:             # немає torch/transformers або битий файл → наступний варіант
                log.exception("класифікатор %s не завантажився", d.name)
                clf = None
            if clf:
                agent.classify = clf
                app_model["name"] = d.name
                break
        else:
            app_model["name"] = "правила"
        log.info("класифікатор наміру: %s", app_model["name"])
    app = web.Application(client_max_size=64 * 1024)
    app["cfg"], app["sit"], app["agent"], app["gaz"], app["regions"] = cfg, sit, agent, gaz, regions

    async def index(_):
        # ?v=<час зміни> до наших js/css: браузер не тримає старий код після оновлення файлів
        html = (cfg.web_dir / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'/static/(app\.js|icons\.js|style\.css)"',
                      lambda m: f'/static/{m[1]}?v={int((cfg.web_dir / m[1]).stat().st_mtime)}"', html)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def state(_):
        now = time.time()
        tracks = sit.tracks(now)
        return web.json_response({
            "serverTime": now * 1000, "tracks": [track_json(t) for t in tracks],
            "alerts": sit.alerts, "attack": sit.mapa_attack, "sources": sit.source_state,
        })

    async def geo_file(req):
        name = req.match_info["name"]
        if name == "ukraine":
            body = regions.ukraine_geojson()
        elif name == "raions":
            body = regions.raions_fc
        elif name == "oblasts":
            body = regions.oblasts_fc
        else:
            raise web.HTTPNotFound()
        return web.json_response(body, headers={"Cache-Control": "max-age=86400"})

    async def places(req):
        q = req.query.get("q", "")[:60]
        return web.json_response([{"name": p.name, "lat": p.lat, "lon": p.lon,
                                   "oblast": regions.oblast_names.get(p.oblast_key, p.oblast_key)}
                                  for p in gaz.search(q)])

    async def where(req):
        lat, lon = _float(req.query.get("lat"), -90, 90), _float(req.query.get("lon"), -180, 180)
        if lat is None or lon is None:
            raise web.HTTPBadRequest(text="lat/lon")
        p = gaz.nearest(lat, lon)
        return web.json_response({"name": p.name if p else None, **regions.locate(lat, lon)})

    async def ask(req):
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text="bad json")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="body must be an object")
        q = str(body.get("q") or "").strip()[:MAX_Q]
        if not q:
            raise web.HTTPBadRequest(text="empty question")
        mine = _place(body.get("place"))
        raw_places = body.get("places") if isinstance(body.get("places"), list) else []
        ups = [p for p in (_place(x) for x in raw_places[:20]) if p]
        ctx = _ctx(body.get("ctx"))
        res = await asyncio.to_thread(agent.ask, q, mine, ups, ctx)
        log.info("ask %r -> %s (%.2f) %sms", q, res["intent"], res["confidence"], res["ms"])
        return web.json_response(res)

    async def health(_):
        return web.json_response({"ok": True, "sources": sit.source_state, "tracks": len(sit.tracks()),
                                  "intent_model": app_model["name"]})

    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/geo/{name}", geo_file)
    app.router.add_get("/api/places", places)
    app.router.add_get("/api/where", where)
    app.router.add_post("/api/ask", ask)
    app.router.add_get("/api/health", health)
    app.router.add_static("/static/", cfg.web_dir)

    async def start_sources(app_):
        app_["model_task"] = asyncio.create_task(asyncio.to_thread(load_model))
        if cfg.offline:
            return
        app_["tasks"] = [asyncio.create_task(supervise("neptun", lambda: run_neptun(sit, cfg.neptun_base), sit))]
        if cfg.mapa:
            app_["tasks"].append(asyncio.create_task(supervise("mapa", lambda: run_mapa(sit, cfg.mapa_base), sit)))

    async def stop_sources(app_):
        for t in app_.get("tasks", []):
            t.cancel()

    async def no_stale_static(req, resp):
        # браузер має перепитувати файли карти (304 якщо не змінились), інакше після оновлення лишається старий JS
        if req.path.startswith("/static/") and "vendor/" not in req.path:
            resp.headers["Cache-Control"] = "no-cache"

    app.on_response_prepare.append(no_stale_static)
    app.on_startup.append(start_sources)
    app.on_cleanup.append(stop_sources)
    return app
