"""Bake-off класифікаторів наміру. Навчання: synth (шаблони) + Gemma-партія A. Тест: Gemma-партія B + ручний набір.

Запуск: .venv\\Scripts\\python tools\\intent_bakeoff.py [кандидати…]
Результат: docs/intent_bakeoff.json і таблиця в консолі. Модель-переможця зберігає tools/train_intent.py.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from varta.gazetteer import Gazetteer  # noqa: E402
from varta.qa.lexicon import INTENTS  # noqa: E402
from varta.qa.parse import extract, rule_intent  # noqa: E402

D = ROOT / "data" / "intents"
DESC_UA = {
    "nearest": "найближча ціль", "around": "що біля/над місцем", "approaching": "чи летить на місце",
    "eta": "коли долетить", "alert": "тривога", "overview": "загальна обстановка", "count": "скільки цілей",
    "safety": "чи безпечно/ховатися", "glossary": "що таке (довідка)", "help": "що вміє бот",
    "refuse": "влучання/ППО/наслідки", "chitchat": "інше/балачки",
}


def load(name: str, batch: str | None = None) -> list[dict]:
    path = D / name
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # "drop" — питання поза межами системи (напр. «що за звук?»), вручну виключені з набору
    return [r for r in rows if (batch is None or r.get("batch") == batch) and r["intent"] != "drop"]


def masked(rows: list[dict], gaz: Gazetteer) -> tuple[list[str], list[str]]:
    return [extract(r["text"], gaz, [], None).masked for r in rows], [r["intent"] for r in rows]


def metrics(pred: list[str], gold: list[str]) -> dict:
    acc = sum(p == g for p, g in zip(pred, gold)) / max(1, len(gold))
    f1s = []
    for c in INTENTS:
        tp = sum(p == c and g == c for p, g in zip(pred, gold))
        fp = sum(p == c and g != c for p, g in zip(pred, gold))
        fn = sum(p != c and g == c for p, g in zip(pred, gold))
        if tp + fn == 0:
            continue
        pr, rc = tp / max(1, tp + fp), tp / max(1, tp + fn)
        f1s.append(0 if pr + rc == 0 else 2 * pr * rc / (pr + rc))
    return {"acc": round(acc, 3), "macro_f1": round(float(np.mean(f1s)), 3), "n": len(gold)}


# ---------------- кандидати: кожен повертає predict(list[str]) -> list[str] ----------------
def cand_rules(Xtr, ytr):
    return lambda xs: [rule_intent(x)[0] for x in xs]


def cand_ngram(Xtr, ytr):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline, make_union
    vec = make_union(TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, min_df=2),
                     TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True, token_pattern=r"[^\s?!.,]+"))
    clf = make_pipeline(vec, LogisticRegression(C=8, max_iter=3000, class_weight="balanced"))
    clf.fit(Xtr, ytr)

    def predict(xs):
        return list(clf.predict(xs))
    predict.model = clf          # для збереження в tools/train_intent.py
    return predict


def _embedder(name: str, device: str):
    import torch
    from transformers import AutoModel, AutoTokenizer
    tok, model = AutoTokenizer.from_pretrained(name), AutoModel.from_pretrained(name).to(device).eval()

    @torch.no_grad()
    def emb(xs: list[str]) -> np.ndarray:
        out = []
        for i in range(0, len(xs), 64):
            b = tok(xs[i:i + 64], padding=True, truncation=True, max_length=64, return_tensors="pt").to(device)
            h = model(**b).last_hidden_state
            m = b["attention_mask"].unsqueeze(-1).float()
            v = (h * m).sum(1) / m.sum(1)
            out.append(torch.nn.functional.normalize(v, dim=-1).cpu().numpy())
        return np.concatenate(out)
    return emb


def cand_minilm(Xtr, ytr):
    from sklearn.linear_model import LogisticRegression
    emb = _embedder("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "cpu")
    clf = LogisticRegression(C=20, max_iter=3000, class_weight="balanced").fit(emb(Xtr), ytr)
    return lambda xs: list(clf.predict(emb(xs)))


def finetune_encoder(name: str, Xtr, ytr, epochs: int = 5, lr: float = 5e-5, out_dir: Path | None = None):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    labels = list(INTENTS)
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(
        name, num_labels=len(labels), id2label=dict(enumerate(labels)), label2id={l: i for i, l in enumerate(labels)}
    ).to(dev)
    y = torch.tensor([labels.index(v) for v in ytr])
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    bs, steps = 32, epochs * math.ceil(len(Xtr) / 32)
    sched = get_linear_schedule_with_warmup(opt, int(0.1 * steps), steps)
    g = torch.Generator().manual_seed(0)
    model.train()
    for ep in range(epochs):
        perm, tot = torch.randperm(len(Xtr), generator=g), 0.0
        for i in range(0, len(Xtr), bs):
            idx = perm[i:i + bs]
            b = tok([Xtr[j] for j in idx], padding=True, truncation=True, max_length=48, return_tensors="pt").to(dev)
            loss = model(**b, labels=y[idx].to(dev)).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(), sched.step(), opt.zero_grad()
            tot += loss.item()
        print(f"  {name} epoch {ep + 1}: loss {tot / math.ceil(len(Xtr) / bs):.3f}", flush=True)
    model.eval()
    if out_dir:
        model.save_pretrained(out_dir)
        tok.save_pretrained(out_dir)

    @torch.no_grad()
    def predict(xs: list[str]) -> list[str]:
        out = []
        for i in range(0, len(xs), 64):
            b = tok(xs[i:i + 64], padding=True, truncation=True, max_length=48, return_tensors="pt").to(dev)
            out += [labels[k] for k in model(**b).logits.argmax(-1).tolist()]
        return out
    return predict


def cand_mmbert(Xtr, ytr):
    return finetune_encoder("jhu-clsp/mmBERT-small", Xtr, ytr)


def fewshot(name: str, Xtr, ytr, shots_per_class: int, max_ctx: int):
    """Генеративна LM як класифікатор: few-shot промпт, вибір мітки-літери за логітами наступного токена."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(name)
    model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev).eval()
    letters = "ABCDEFGHIJKL"
    head = "Класифікуй питання до чат-бота про повітряні загрози. Класи:\n" + "\n".join(
        f"{letters[i]} = {DESC_UA[c]}" for i, c in enumerate(INTENTS)) + "\n\n"
    rng = np.random.default_rng(0)
    shots = []
    for c in INTENTS:
        pool = [x for x, y in zip(Xtr, ytr) if y == c]
        shots += [(pool[k], c) for k in rng.choice(len(pool), shots_per_class, replace=False)]
    rng.shuffle(shots)
    prompt = head + "".join(f"Питання: {x}\nКлас: {letters[INTENTS.index(c)]}\n\n" for x, c in shots)
    ids_letters = [tok.encode(" " + l, add_special_tokens=False)[-1] for l in letters]
    ids_plain = [tok.encode(l, add_special_tokens=False)[-1] for l in letters]

    @torch.no_grad()
    def predict(xs: list[str]) -> list[str]:
        out = []
        for x in xs:
            ids = tok(prompt + f"Питання: {x}\nКлас:", return_tensors="pt").input_ids[:, -max_ctx:].to(dev)
            logits = model(ids).logits[0, -1].float()
            score = torch.maximum(logits[ids_letters], logits[ids_plain])
            out.append(INTENTS[int(score.argmax())])
        return out
    return predict


def cand_qwen35(Xtr, ytr):
    return fewshot("Qwen/Qwen3.5-0.8B-Base", Xtr, ytr, shots_per_class=3, max_ctx=4096)


def cand_gpt2(Xtr, ytr):
    return fewshot("openai-community/gpt2", Xtr, ytr, shots_per_class=1, max_ctx=1000)


CANDS = {"rules": cand_rules, "ngram": cand_ngram, "minilm": cand_minilm, "mmbert": cand_mmbert,
         "qwen35": cand_qwen35, "gpt2": cand_gpt2}


def main() -> None:
    which = sys.argv[1:] or list(CANDS)
    gaz = Gazetteer(ROOT / "data" / "geo" / "places.json")
    Xs, ys = masked(load("synth.jsonl"), gaz)
    Xa, ya = masked(load("llm_questions.jsonl", "A"), gaz)
    # B — dev (помилки на ньому аналізувались), C — фінальний тест (мітки виставлені до будь-яких прогонів), human — ручний
    tests = {"dev_B": masked(load("llm_questions.jsonl", "B"), gaz), "test_C": masked(load("llm_questions.jsonl", "C"), gaz),
             "human": masked(load("human_test.jsonl"), gaz)}
    print(f"train synth={len(Xs)} gemmaA={len(Xa)}; tests " + ", ".join(f"{k}={len(v[0])}" for k, v in tests.items()))
    res_path = ROOT / "docs" / "intent_bakeoff.json"
    results = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() and len(sys.argv) > 1 else {}
    for name in which:
        for train_name, (Xtr, ytr) in {"synth": (Xs, ys), "synth+gemmaA": (Xs + Xa, ys + ya)}.items():
            if name in ("rules",) and train_name != "synth":
                continue
            if name in ("qwen35", "gpt2", "minilm") and train_name != "synth+gemmaA":
                continue
            t0 = time.time()
            pred = CANDS[name](Xtr, ytr)
            train_s = time.time() - t0
            row = {"train": train_name, "train_s": round(train_s, 1)}
            for tname, (Xt, yt) in tests.items():
                t1 = time.time()
                p = pred(Xt)
                row[tname] = metrics(p, yt)
                row[tname]["ms_per_q"] = round((time.time() - t1) * 1000 / max(1, len(Xt)), 2)
            results[f"{name}|{train_name}"] = row
            print(name, row, flush=True)
            res_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
