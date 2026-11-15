# Forecast evaluation (2025)

Models: `D:\NASA\Fire Prediction model\models` · every 2 issue day(s) · full land grid · evaluated 2026-11-15T11:51:07

| Lead | Method | PR-AUC | Brier ×1e4 | Poisson dev/cell | FSS 0.1° | FSS 0.5° | FSS 1.1° | Top-1000 row capture | Daily total bias % | Daily total log corr |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 d | **model** | 0.401 | 31.06 | 0.1416 | 0.466 | 0.747 | 0.830 | 0.646 | -1.3 | 0.926 |
| 1 d | persistence | 0.286 | 35.71 | 0.2330 | 0.311 | 0.542 | 0.635 | 0.565 | -2.4 | 0.869 |
| 1 d | climatology | 0.034 | 40.52 | 0.3141 | 0.078 | 0.346 | 0.532 | 0.126 | +4.7 | 0.613 |
| 3 d | **model** | 0.175 | 37.31 | 0.2127 | 0.230 | 0.576 | 0.725 | 0.385 | -7.0 | 0.872 |
| 3 d | persistence | 0.085 | 40.11 | 0.3130 | 0.099 | 0.252 | 0.348 | 0.282 | -4.1 | 0.651 |
| 3 d | climatology | 0.034 | 40.52 | 0.3141 | 0.078 | 0.346 | 0.532 | 0.126 | +4.7 | 0.613 |
| 7 d | **model** | 0.089 | 39.36 | 0.2487 | 0.125 | 0.460 | 0.655 | 0.238 | -14.1 | 0.829 |
| 7 d | persistence | 0.025 | 41.07 | 0.3397 | 0.031 | 0.131 | 0.221 | 0.127 | -4.0 | 0.507 |
| 7 d | climatology | 0.034 | 40.52 | 0.3141 | 0.078 | 0.346 | 0.532 | 0.126 | +4.7 | 0.613 |

Higher is better for PR-AUC, FSS, top-k capture and log correlation; lower for Brier and deviance.
The model is only useful where it beats both baselines.
