"""Ядро: геометрія, газетир, злиття джерел, розбір питань, відповіді, API. Запуск: python -m pytest tests"""
import asyncio
import re
import time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from varta.config import Config
from varta.gazetteer import Gazetteer
from varta.geo import Motion, approach, bearing_deg, dead_reckon, dest, haversine_km
from varta.qa import Agent
from varta.regions import Regions
from varta.server import build_app
from varta.situation import Situation

ROOT = Path(__file__).resolve().parents[1]
KYIV = (50.4547, 30.5238)


@pytest.fixture(scope="session")
def gaz():
    return Gazetteer(ROOT / "data" / "geo" / "places.json")


@pytest.fixture(scope="session")
def regions():
    return Regions(ROOT / "data" / "geo")


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def neptun(id_, lat, lon, **kw):
    d = {"id": id_, "type": "uav", "title": "БпЛА", "region": "", "district": "", "locality": "Бровари",
         "lat": lat, "lon": lon, "heading": None, "updatedAt": iso(time.time() - 60), "status": "active",
         "explanationShort": "", "sourceCount": 2, "positionQuality": "confirmed", "uncertaintyKm": 4}
    d.update(kw)
    return d


# ---------- геометрія ----------
def test_geo_basics():
    assert 460 < haversine_km(*KYIV, 49.84, 24.03) < 475            # Київ–Львів
    assert abs(bearing_deg(50, 30, 50, 31) - 90) < 1
    lat, lon = dest(*KYIV, 45, 100)
    assert abs(haversine_km(*KYIV, lat, lon) - 100) < 0.1


def test_approach_head_on_vs_crossing():
    north_50 = dest(*KYIV, 0, 50)
    a = approach(Motion(*north_50, 180, 150, 20), *KYIV, 20, 60)     # летить прямо на Київ
    assert a.closing and a.cpa_km < 1 and 11 < a.eta_min < 13 and a.p_enter > 0.6
    b = approach(Motion(*north_50, 90, 150, 20), *KYIV, 20, 60)      # летить на схід — мимо
    assert not b.closing and b.eta_min is None and b.p_enter < 0.05
    c = approach(Motion(*north_50, None, 150, 20), *KYIV, 20, 60)    # курс невідомий — ймовірності немає
    assert c.p_enter is None and c.closing is None
    far = dest(*KYIV, 0, 400)
    assert approach(Motion(*far, 180, 150, 20), *KYIV, 20, 60).p_enter == 0.0   # за годину не долетить


def test_dead_reckon_is_capped_like_neptun():
    lat, lon, flying = dead_reckon("uav", 50, 30, 90, 150, age_min=60)
    assert haversine_km(50, 30, lat, lon) <= 18.01 and not flying   # MAX_GHOST_KM uav = 18, горизонт 12 хв


# ---------- газетир ----------
@pytest.mark.parametrize("text,name", [
    ("що летить на Київ", "Київ"), ("тривога у Києві", "Київ"), ("біля Кривого Рогу", "Кривий Ріг"),
    ("у Кривому Розі", "Кривий Ріг"), ("над Білою Церквою", "Біла Церква"), ("у Броварах", "Бровари"),
    ("біля Борисполя", "Бориспіль"), ("в Ірпені", "Ірпінь"), ("у Миколаєві", "Миколаїв"), ("у Марганці", "Марганець"),
    ("в Кропивницькому", "Кропивницький"), ("тривога в Ізюмі", "Ізюм"), ("на Салтівці", "Салтівка"),
])
def test_gazetteer_inflections(gaz, text, name):
    assert [m.place.name for m in gaz.find(text) if m.place] == [name]


def test_gazetteer_oblasts_and_stopwords(gaz):
    assert [m.oblast_key for m in gaz.find("що на Харківщині")] == ["харківська"]
    assert [m.oblast_key for m in gaz.find("ракети на Одеську область")] == ["одеська"]
    assert gaz.find("де найближчий шахед біля мене") == []          # «мене» ≠ смт Мена


# ---------- ситуація і злиття ----------
def test_mapa_freshness_and_fusion(regions, gaz):
    sit = Situation(regions, gaz)
    now = time.time()
    sit.set_neptun_threats([neptun("t1", 50.51, 30.79)])
    sit.set_mapa({"objects": [
        # той самий шахед за MAPA, 5 км поруч, свіжий → лише збагачує трек NEPTUN
        {"id": 1, "kind": "drone_jet", "status": "active", "lat": 50.55, "lon": 30.75, "heading": 200, "speed_kmh": 170,
         "trail": [[30.9, 50.7, now - 300], [30.75, 50.55, now - 60]]},
        # заморожений «active» обʼєкт: остання точка 2 год тому → відкинути
        {"id": 2, "kind": "drone_jet", "status": "active", "lat": 48.0, "lon": 35.0, "trail": [[35.0, 48.0, now - 7200]]},
        # точка трейлу в МАЙБУТНЬОМУ → позиція = остання точка до «зараз»
        {"id": 3, "kind": "missile_cruise", "status": "active", "lat": 47.0, "lon": 33.0, "speed_kmh": 800,
         "trail": [[32.0, 46.5, now - 30], [33.0, 47.0, now + 600]]},
        {"id": 4, "kind": "drone_jet", "status": "lost", "lat": 49, "lon": 30, "trail": [[30, 49, now - 10]]},
    ]})
    tracks = {t.id: t for t in sit.tracks(now)}
    assert set(tracks) == {"Nt1", "M3"}
    assert tracks["Nt1"].sources == ("mapa", "neptun") and tracks["Nt1"].speed_kmh == 170
    assert (round(tracks["M3"].lat, 1), round(tracks["M3"].lon, 1)) == (46.5, 32.0)
    assert tracks["Nt1"].region == "Київська область"                # область дозаповнена за координатами


def test_alert_lookup(regions, gaz):
    sit = Situation(regions, gaz)
    loc = regions.locate(50.51, 30.79)
    sit.set_alerts({"raions": [{"key": loc["raion_key"], "name": "Броварський район", "level": "red"}], "oblasts": []})
    assert sit.alert_at(50.51, 30.79)["name"] == "Броварський район"
    assert sit.alert_at(49.84, 24.03) is None


# ---------- агент ----------
@pytest.fixture()
def agent(regions, gaz):
    sit = Situation(regions, gaz)
    north = dest(*KYIV, 0, 40)
    sit.set_neptun_threats([
        neptun("near", *north, velocity={"bearingDeg": 180, "speedKmh": 160}, confirmedAt=iso(time.time())),
        neptun("far", 46.48, 30.73, locality="Одеса", type="missile", title="Крилата ракета"),
        neptun("area", 46.6, 30.5, areaOnly=True, region="Одеська область", locality="", type="missile",
               title="Ракета"),
    ])
    return Agent(sit, gaz)


ME = {"lat": KYIV[0], "lon": KYIV[1], "name": "Дім"}


def test_nearest_uses_real_distance(agent):
    r = agent.ask("де найближчий шахед?", ME)
    assert r["intent"] == "nearest" and r["focus"] == ["Nnear"]
    km = int(re.search(r"(\d+) км на північ", r["answer"]).group(1))
    assert 38 <= km <= 41                                              # 40 км мінус кілька секунд руху


def test_approaching_and_eta(agent):
    r = agent.ask("чи летить щось на мене?", ME)
    assert r["intent"] == "approaching" and "Nnear" in r["focus"] and "Nfar" not in r["focus"]
    r = agent.ask("коли буде у мене?", ME)
    assert r["intent"] == "eta" and "через ~" in r["answer"]


def test_place_from_question_and_no_place(agent):
    r = agent.ask("найближча ракета до Одеси")
    assert r["parsed"]["place"] == "Одеса" and r["parsed"]["types"] == ["missile"] and "Крилата ракета" in r["answer"]
    assert "Не знаю, щодо якого місця" in agent.ask("де найближчий шахед")["answer"]


def test_area_only_never_gets_distance(agent):
    r = agent.ask("скільки ракет зараз?")
    assert r["intent"] == "count" and "2 ракети" in r["answer"]
    near = agent.ask("найближча ракета до Одеси")["answer"]
    assert "Ракета" not in near.split("\n")[0].split("—")[1] if "—" in near else True


def test_sound_question_shows_neighbourhood(agent):
    r = agent.ask("що за звук гуде?", ME)
    assert r["answer"].startswith("Що саме гуде, я визначити не можу") and "Nnear" in r["focus"]


def test_kyiv_oblast_includes_city(regions, gaz):
    sit = Situation(regions, gaz)
    sit.set_neptun_threats([neptun("city", *KYIV), neptun("obl", 50.51, 30.79)])
    assert "2 цілі" in Agent(sit, gaz).ask("скільки шахедів на Київщині")["answer"]


def test_refuse_and_followup(agent):
    assert agent.ask("куди влучило в Києві?")["intent"] == "refuse"
    first = agent.ask("де найближчий шахед?", ME)
    follow = agent.ask("а ракети?", ME, ctx=first["ctx"])
    assert follow["intent"] == "nearest" and follow["parsed"]["types"] == ["missile"]


# ---------- API ----------
def test_api_end_to_end(tmp_path):
    cfg = Config(offline=True, log_events=False, model_dir=tmp_path / "none")
    app = build_app(cfg)
    app["sit"].set_neptun_threats([neptun("t1", 50.51, 30.79)])

    async def run():
        async with TestClient(TestServer(app)) as c:
            st = await (await c.get("/api/state")).json()
            assert st["tracks"][0]["id"] == "Nt1"
            ans = await (await c.post("/api/ask", json={"q": "що біля мене?", "place": ME})).json()
            assert ans["intent"] == "around" and "Nt1" in ans["focus"]
            assert (await c.post("/api/ask", json={"q": ""})).status == 400
            assert (await c.post("/api/ask", data=b"{bad")).status == 400
            bad_place = await (await c.post("/api/ask", json={"q": "де найближчий шахед", "place": {"lat": 999}})).json()
            assert "Не знаю" in bad_place["answer"]
            places = await (await c.get("/api/places?q=Бров")).json()
            assert places[0]["name"] == "Бровари"
    asyncio.run(run())


# ---------- регресії з код-рев'ю ----------
def test_approach_east_west_far():
    """Ракета за 400 км на схід летить точно на Київ — має бути «наближається» з ETA ≈ 30 хв."""
    east = dest(*KYIV, 90, 400)
    a = approach(Motion(*east, bearing_deg(*east, *KYIV), 800, 20), *KYIV, 20, 60)
    assert a.closing and a.cpa_km < 2 and 27 < a.eta_min < 31


def test_bad_feed_records_are_skipped_not_fatal(regions, gaz):
    sit = Situation(regions, gaz)
    now = time.time()
    sit.apply_neptun_frame(["not", "a", "dict"])
    sit.apply_neptun_frame({"type": "snapshot", "data": {"threats": ["x", None, neptun("ok", 50.5, 30.8),
                                                                        neptun("badlat", "n/a", 30.0)]}})
    sit.apply_neptun_frame({"type": "upsert", "data": {**neptun("h", 50.4, 30.6), "heading": "east",
                                                       "velocity": {"bearingDeg": None, "speedKmh": "fast"}}})
    sit.set_mapa({"objects": [{"kind": "drone_jet", "status": "active", "trail": [[30, 50, now]]},   # без id
                              {"id": 9, "kind": "drone_jet", "status": "active", "amount": "2-3",
                               "trail": [[30.1, 50.1, None], [30.2, 50.2, now - 10]]}]})
    ids = {t.id for t in sit.tracks(now)}
    assert ids == {"Nok", "Nh", "M9"}
    h = next(t for t in sit.tracks(now) if t.id == "Nh")
    assert h.heading is None and h.speed_kmh is None


def test_stale_feed_warns_instead_of_all_clear(agent):
    agent.sit.source_state["neptun"]["at"] = time.time() - 40 * 60
    r = agent.ask("що біля мене?", ME)
    assert r["answer"].startswith("⚠️ Дані NEPTUN не оновлювались")


def test_fusion_gate_does_not_swallow_neighbours(regions, gaz):
    sit = Situation(regions, gaz)
    now = time.time()
    near = dest(*KYIV, 0, 10)
    wide = dest(*KYIV, 90, 70)
    sit.set_neptun_threats([neptun("near", *near), neptun("wide", *wide, uncertaintyKm=100)])
    m1, m2 = dest(*near, 0, 3), dest(*wide, 90, 90)
    sit.set_mapa({"objects": [
        {"id": 1, "kind": "drone_jet", "status": "active", "trail": [[m1[1], m1[0], now - 30]]},
        {"id": 2, "kind": "drone_jet", "status": "active", "trail": [[m2[1], m2[0], now - 30]]}]})
    t = {x.id: x for x in sit.tracks(now)}
    assert "mapa" in t["Nnear"].sources and "M2" in t       # другий дрон не зник у «широкому» треку


def test_short_new_question_keeps_my_place(agent):
    first = agent.ask("що летить на Одесу?", ME)
    r = agent.ask("де найближчий шахед?", ME, ctx=first["ctx"])
    assert r["answer"].startswith("Біля вас (Дім)")


def test_followup_with_real_classifier(regions, gaz):
    from varta.qa.intent import load_classifier
    clf = load_classifier(ROOT / "models" / "intent")
    if clf is None:
        pytest.skip("модель наміру не навчена")
    sit = Situation(regions, gaz)
    sit.set_neptun_threats([neptun("near", *dest(*KYIV, 0, 40))])
    ag = Agent(sit, gaz, clf)
    first = ag.ask("де найближчий шахед?", ME)
    follow = ag.ask("а ракети?", ME, ctx=first["ctx"])
    assert follow["intent"] == "nearest" and follow["parsed"]["types"] == ["missile"]
    al = ag.ask("чи є тривога у Львові?", ME)
    assert ag.ask("де шахеди?", ME, ctx=al["ctx"])["intent"] != "alert"


def test_capital_alerts_and_counts(regions, gaz):
    sit = Situation(regions, gaz)
    brov = regions.locate(50.51, 30.79)
    sit.set_alerts({"raions": [{"key": brov["raion_key"], "name": "Броварський район", "oblast": "Київська область",
                                "level": "yellow"}], "oblasts": []})
    sit.set_neptun_threats([neptun("c", *KYIV)])
    ag = Agent(sit, gaz)
    assert ag.ask("чи є тривога в столиці?")["answer"].startswith("м. Київ: офіційної тривоги зараз немає")
    assert "1 ціль" in ag.ask("скільки цілей у столиці?")["answer"]


@pytest.mark.parametrize("text,types", [
    ("fpv-дрон біля мене", ["fpv"]), ("найближча балістична ракета", ["ballistic"]),
    ("розвідувальний дрон", ["recon"]), ("крилата ракета", ["missile"]), ("шахеди і ракети", ["missile", "uav"]),
])
def test_type_detection_no_double_count(text, types):
    from varta.qa.lexicon import detect_types
    assert sorted(detect_types(text)) == types


def test_api_rejects_malformed_bodies(tmp_path):
    cfg = Config(offline=True, log_events=False, model_dir=tmp_path / "none")
    app = build_app(cfg)

    async def run():
        async with TestClient(TestServer(app)) as c:
            assert (await c.post("/api/ask", json=["q"])).status == 400
            assert (await c.post("/api/ask", json="q")).status == 400
            r = await c.post("/api/ask", json={"q": "де найближчий шахед", "places": {"a": 1},
                                               "ctx": {"intent": "candidates", "types": ["bogus", "uav"],
                                                       "oblast_key": "<script>"}})
            assert r.status == 200
    asyncio.run(run())
