"""Живі джерела: NEPTUN (WebSocket + REST-фолбек + тривоги) і MAPA.UA (/api/v1/current).

Умови NEPTUN: безкоштовно, лише GET, REST не частіше ніж раз на 5 с, видиме посилання на NEPTUN.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from .situation import Situation

log = logging.getLogger("varta.sources")
REST_MIN_S = 5.0
UA = {"User-Agent": "VARTA/2.0 (personal air-threat map; attribution: neptun.in.ua, mapa.ua)"}


async def _get_json(s: aiohttp.ClientSession, url: str) -> dict | None:
    try:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status == 200:
                return await r.json(content_type=None)
            log.warning("%s -> HTTP %s", url, r.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as e:
        log.warning("%s -> %s", url, e)
    return None


async def run_neptun(situation: Situation, base: str = "https://neptun.in.ua", alerts_every_s: float = 60) -> None:
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + "/api/v1/stream"
    async with aiohttp.ClientSession(headers=UA) as s:
        async def alerts_loop() -> None:
            while True:
                data = await _get_json(s, base + "/api/v1/alerts")
                if isinstance(data, dict):
                    situation.set_alerts(data)
                await asyncio.sleep(alerts_every_s)

        alerts = asyncio.create_task(alerts_loop())
        backoff, last_rest = 1.0, 0.0
        try:
            while True:
                try:
                    async with s.ws_connect(ws_url, heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                        log.info("NEPTUN stream connected")
                        backoff = 1.0
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    situation.apply_neptun_frame(json.loads(msg.data))
                                except json.JSONDecodeError:
                                    log.warning("NEPTUN: зіпсований фрейм")
                                except Exception:          # битий фрейм не має зупинити стрім
                                    log.exception("NEPTUN: не вдалося застосувати фрейм")
                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    log.warning("NEPTUN stream: %s", e)
                situation.mark("neptun", False)
                # REST-фолбек, поки WS лежить; умова NEPTUN — не частіше ніж раз на 5 с
                if time.monotonic() - last_rest >= REST_MIN_S:
                    last_rest = time.monotonic()
                    snap = await _get_json(s, base + "/api/v1/threats")
                    if isinstance(snap, dict):
                        situation.set_neptun_threats(snap.get("threats") or [])
                await asyncio.sleep(max(REST_MIN_S, min(15.0, backoff)))
                backoff *= 2
        finally:
            alerts.cancel()


async def run_mapa(situation: Situation, base: str = "https://mapa.ua", every_s: float = 15) -> None:
    async with aiohttp.ClientSession(headers=UA) as s:
        while True:
            data = await _get_json(s, base + "/api/v1/current")
            if isinstance(data, dict):
                try:
                    situation.set_mapa(data)
                except Exception:
                    log.exception("MAPA: не вдалося застосувати знімок")
            else:
                situation.mark("mapa", False)
            await asyncio.sleep(every_s)


async def supervise(name: str, start, situation: Situation) -> None:
    """Перезапускає джерело, якщо воно впало: інакше сервер мовчки показував би заморожені дані."""
    while True:
        try:
            await start()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("джерело %s впало — перезапуск за 5 с", name)
            situation.mark(name, False)
        await asyncio.sleep(5)
