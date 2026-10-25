"""Навчає фінальний класифікатор наміру і кладе в models/intent.

Переможець bake-off — mmBERT-small (docs/intent_bakeoff.json). Навчання: шаблони + Gemma A + Gemma B (dev
більше не потрібен для вибору). Тест: Gemma C і ручний набір — у навчання НЕ потрапляють.
Запуск: .venv\\Scripts\\python tools\\train_intent.py [mmbert|ngram]
"""
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
from intent_bakeoff import Gazetteer, cand_ngram, finetune_encoder, load, masked, metrics  # noqa: E402


def main() -> None:
    kind = (sys.argv[1:] or ["mmbert"])[0]
    gaz = Gazetteer(ROOT / "data" / "geo" / "places.json")
    X, y = [], []
    for name, batch in (("synth.jsonl", None), ("llm_questions.jsonl", "A"), ("llm_questions.jsonl", "B")):
        a, b = masked(load(name, batch), gaz)
        X += a
        y += b
    tests = {"test_C": masked(load("llm_questions.jsonl", "C"), gaz), "human": masked(load("human_test.jsonl"), gaz)}
    out = ROOT / "models" / ("intent" if kind == "mmbert" else "intent_ngram")
    tmp = ROOT / "models" / f"{out.name}_new"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    t0 = time.time()
    if kind == "mmbert":
        predict = finetune_encoder("jhu-clsp/mmBERT-small", X, y, out_dir=tmp)
    else:
        import joblib
        predict = cand_ngram(X, y)
        joblib.dump(predict.model, tmp / "ngram.joblib")
    report = {"model": kind, "n_train": len(X), "train_s": round(time.time() - t0, 1)}
    for name, (Xt, yt) in tests.items():
        report[name] = metrics(predict(Xt), yt)
    (tmp / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    # замінюємо робочу модель лише після успішного навчання
    if out.exists():
        shutil.rmtree(out)
    tmp.rename(out)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
