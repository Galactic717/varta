| модель | навчання | тест C (n=436) | ручний (n=115) | dev B | мс/питання |
|---|---|---|---|---|---|
| mmBERT-small, fine-tune | synth+gemmaA | **90.1%** | 94.8% | 90.4% | 0.85 |
| char-n-gram + LogReg | synth+gemmaA | **87.6%** | 96.5% | 92.0% | 0.08 |
| char-n-gram + LogReg | synth | **84.4%** | 96.5% | 88.9% | 0.09 |
| mmBERT-small, fine-tune | synth | **80.5%** | 88.7% | 88.4% | 0.85 |
| MiniLM-L12 + LogReg | synth+gemmaA | **71.1%** | 91.3% | 84.9% | 3.8 |
| правила (ключові слова) | synth | **59.9%** | 71.3% | 55.6% | 0.04 |
| Qwen3.5-0.8B, few-shot | synth+gemmaA | **46.1%** | 68.7% | 48.9% | 266.9 |
| GPT-2 small, few-shot | synth+gemmaA | **8.0%** | 7.8% | 6.2% | 27.33 |
