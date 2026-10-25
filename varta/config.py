"""Налаштування через змінні середовища (або .env поруч із main.py)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8080
    neptun_base: str = "https://neptun.in.ua"
    mapa_base: str = "https://mapa.ua"
    mapa: bool = True
    offline: bool = False               # не підключатися до джерел (тести, демо)
    log_events: bool = True             # писати фрейми NEPTUN у data/log (власний архів)
    data_dir: Path = ROOT / "data"
    model_dir: Path = ROOT / "models" / "intent"
    web_dir: Path = ROOT / "web"
    home: dict | None = field(default=None)   # VARTA_HOME="50.45,30.52,Дім" — для CLI

    @staticmethod
    def from_env() -> "Config":
        _load_dotenv()
        e = os.environ.get
        home = None
        if e("VARTA_HOME"):
            lat, lon, *name = e("VARTA_HOME").split(",")
            home = {"lat": float(lat), "lon": float(lon), "name": (name[0] if name else "Дім").strip()}
        return Config(host=e("VARTA_HOST", "127.0.0.1"), port=int(e("VARTA_PORT", "8080")),
                      mapa=e("VARTA_MAPA", "1") != "0", offline=e("VARTA_OFFLINE", "0") == "1",
                      log_events=e("VARTA_LOG_EVENTS", "1") != "0", home=home,
                      # VARTA_MODEL=intent_ngram — легкий класифікатор для слабких ПК (без torch, ~20 МБ RAM)
                      model_dir=ROOT / "models" / e("VARTA_MODEL", "intent"))
