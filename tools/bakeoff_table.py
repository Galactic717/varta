"""docs/intent_bakeoff.json → таблиця Markdown (для README)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"rules": "правила (ключові слова)", "ngram": "char-n-gram + LogReg", "minilm": "MiniLM-L12 + LogReg",
         "mmbert": "mmBERT-small, fine-tune", "qwen35": "Qwen3.5-0.8B, few-shot", "gpt2": "GPT-2 small, few-shot"}
res = json.loads((ROOT / "docs" / "intent_bakeoff.json").read_text(encoding="utf-8"))
rows = ["| модель | навчання | тест C (n={}) | ручний (n={}) | dev B | мс/питання |".format(
    next(iter(res.values()))["test_C"]["n"], next(iter(res.values()))["human"]["n"]), "|---|---|---|---|---|---|"]
for key, r in sorted(res.items(), key=lambda kv: -kv[1]["test_C"]["acc"]):
    name, train = key.split("|")
    rows.append(f"| {NAMES.get(name, name)} | {train} | **{r['test_C']['acc']:.1%}** | {r['human']['acc']:.1%} | "
                f"{r['dev_B']['acc']:.1%} | {r['test_C']['ms_per_q']} |")
print("\n".join(rows))
