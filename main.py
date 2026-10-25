"""VARTA — карта повітряних загроз + агент питань-відповідей.

  python main.py                      сервер і карта на http://127.0.0.1:8080/
  python main.py ask "де найближчий шахед" [--place "Київ"]   питання з терміналу (живі дані, ~10 с збору)
"""
import argparse
import asyncio
import logging
import sys

from aiohttp import web

from varta.config import Config
from varta.server import build_app


def serve(cfg: Config) -> None:
    app = build_app(cfg)
    print(f"VARTA: http://{cfg.host}:{cfg.port}/  (дані: NEPTUN, MAPA.UA)")
    web.run_app(app, host=cfg.host, port=cfg.port, print=None, access_log=None)


def ask(cfg: Config, question: str, place: str | None, wait_s: float) -> None:
    from varta.sources import run_mapa, run_neptun
    app = build_app(cfg)
    sit, agent, gaz = app["sit"], app["agent"], app["gaz"]
    my = cfg.home
    if place:
        hits = gaz.find(place) or []
        p = next((m.place for m in hits if m.place), None)
        if not p:
            sys.exit(f"Не знайшов місце «{place}»")
        my = {"lat": p.lat, "lon": p.lon, "name": p.name}

    async def collect():
        tasks = [asyncio.create_task(run_neptun(sit))] + ([asyncio.create_task(run_mapa(sit))] if cfg.mapa else [])
        await asyncio.sleep(wait_s)
        for t in tasks:
            t.cancel()
    asyncio.run(collect())
    res = agent.ask(question, my)
    print(res["answer"])
    print(f"\n[намір: {res['intent']} ({res['confidence']}), розбір: {res['parsed']}]")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="serve", choices=["serve", "ask"])
    ap.add_argument("question", nargs="?")
    ap.add_argument("--place")
    ap.add_argument("--wait", type=float, default=10)
    a = ap.parse_args()
    cfg = Config.from_env()
    if a.cmd == "ask":
        if not a.question:
            sys.exit("Напишіть питання в лапках")
        ask(cfg, a.question, a.place, a.wait)
    else:
        serve(cfg)
