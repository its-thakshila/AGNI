# Forecast evaluation (2023, 2024, 2025)

Models: `D:\NASA\Fire Prediction model\models` · every 2 issue day(s) · full land grid · evaluated 2026-11-15T04:23:07

| Lead | Method | PR-AUC | Brier ×1e4 | Poisson dev/cell | FSS 0.1° | FSS 0.5° | FSS 1.1° | Top-1000 row capture | Daily total bias % | Daily total log corr |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 d | **model** | 0.425 | 31.18 | 0.1541 | 0.470 | 0.753 | 0.835 | 0.642 | -4.7 | 0.936 |
| 1 d | persistence | 0.315 | 36.04 | 0.2561 | 0.287 | 0.500 | 0.585 | 0.559 | -6.9 | 0.874 |
| 1 d | climatology | 0.032 | 42.43 | 0.3706 | 0.078 | 0.336 | 0.511 | 0.109 | -6.7 | 0.667 |
| 3 d | **model** | 0.174 | 38.78 | 0.2429 | 0.201 | 0.515 | 0.656 | 0.360 | -10.1 | 0.848 |
| 3 d | persistence | 0.098 | 41.43 | 0.3504 | 0.088 | 0.226 | 0.311 | 0.273 | -9.7 | 0.730 |
| 3 d | climatology | 0.032 | 42.43 | 0.3706 | 0.078 | 0.336 | 0.511 | 0.109 | -6.7 | 0.667 |
| 7 d | **model** | 0.082 | 41.21 | 0.2910 | 0.097 | 0.369 | 0.542 | 0.209 | -15.9 | 0.795 |
| 7 d | persistence | 0.029 | 42.81 | 0.3829 | 0.028 | 0.113 | 0.188 | 0.119 | -11.4 | 0.625 |
| 7 d | climatology | 0.032 | 42.43 | 0.3706 | 0.078 | 0.336 | 0.511 | 0.109 | -6.7 | 0.667 |

Higher is better for PR-AUC, FSS, top-k capture and log correlation; lower for Brier and deviance.
The model is only useful where it beats both baselines.
