| модель | навчання | тест C (n=436) | ручний (n=115) | dev B | мс/питання |
|---|---|---|---|---|---|
| mmBERT-small, fine-tune | synth+gemmaA | **90.1%** | 93.9% | 90.4% | 0.86 |
| char-n-gram + LogReg | synth+gemmaA | **86.5%** | 96.5% | 92.0% | 0.07 |
| char-n-gram + LogReg | synth | **84.6%** | 96.5% | 88.7% | 0.07 |
| mmBERT-small, fine-tune | synth | **79.6%** | 93.0% | 85.3% | 0.85 |
| MiniLM-L12 + LogReg | synth+gemmaA | **71.1%** | 90.4% | 85.3% | 3.89 |
| правила (ключові слова) | synth | **59.6%** | 71.3% | 55.6% | 0.04 |
| Qwen3.5-0.8B, few-shot | synth+gemmaA | **58.0%** | 71.3% | 53.3% | 308.66 |
| GPT-2 small, few-shot | synth+gemmaA | **3.7%** | 8.7% | 6.4% | 39.66 |
