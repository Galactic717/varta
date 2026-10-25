"""Завантажує навчений класифікатор mmBERT-small (≈540 МБ) з GitHub Release у models/intent.

Без нього агент працює на легкій моделі models/intent_ngram (88.8 % проти 89.7 % на тесті C).
Запуск: .venv\\Scripts\\python tools\\fetch_model.py
"""
import sys
import zipfile
from pathlib import Path

import httpx

URL = "https://github.com/Galactic717/varta/releases/download/v2.0.0/varta-intent-mmbert.zip"
ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    out = ROOT / "models" / "intent"
    if (out / "model.safetensors").exists():
        print("models/intent вже є")
        return
    tmp = ROOT / "models" / "varta-intent-mmbert.zip"
    with httpx.stream("GET", URL, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length", 0)), 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r{done * 100 // total}%", end="", file=sys.stderr)
    with zipfile.ZipFile(tmp) as z:
        z.extractall(ROOT / "models")
    tmp.unlink()
    print("\nготово:", out)


if __name__ == "__main__":
    main()
