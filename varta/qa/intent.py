"""Завантаження класифікатора наміру з models/intent (переможець bake-off). Немає моделі → правила."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger("varta.intent")
Classifier = Callable[[str], tuple[str, float]]


def load_classifier(model_dir: Path) -> Classifier | None:
    if (model_dir / "config.json").exists():
        return _hf(model_dir)
    if (model_dir / "ngram.joblib").exists():
        return _sk(model_dir / "ngram.joblib")
    log.warning("модель наміру не знайдена в %s — працюють правила", model_dir)
    return None


def _sk(path: Path) -> Classifier:
    import joblib
    clf = joblib.load(path)

    def classify(masked: str) -> tuple[str, float]:
        p = clf.predict_proba([masked])[0]
        i = int(p.argmax())
        return str(clf.classes_[i]), float(p[i])
    return classify


def _hf(model_dir: Path) -> Classifier:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
    labels = model.config.id2label

    @torch.no_grad()
    def classify(masked: str) -> tuple[str, float]:
        b = tok([masked], truncation=True, max_length=48, return_tensors="pt")
        p = model(**b).logits.softmax(-1)[0]
        i = int(p.argmax())
        return labels[i], float(p[i])
    return classify
