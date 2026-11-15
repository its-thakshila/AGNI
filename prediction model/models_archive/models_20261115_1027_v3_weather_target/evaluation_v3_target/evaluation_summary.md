# Forecast evaluation (2023, 2024, 2025)

Models: `D:\NASA\Fire Prediction model\models` · every 2 issue day(s) · full land grid · evaluated 2026-11-15T10:58:28

| Lead | Method | PR-AUC | Brier ×1e4 | Poisson dev/cell | FSS 0.1° | FSS 0.5° | FSS 1.1° | Top-1000 row capture | Daily total bias % | Daily total log corr |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 d | **model** | 0.439 | 30.71 | 0.1503 | 0.478 | 0.768 | 0.853 | 0.647 | -1.0 | 0.951 |
| 1 d | persistence | 0.315 | 36.04 | 0.2561 | 0.287 | 0.500 | 0.585 | 0.559 | -6.9 | 0.874 |
| 1 d | climatology | 0.032 | 42.43 | 0.3706 | 0.078 | 0.336 | 0.511 | 0.109 | -6.7 | 0.667 |
| 3 d | **model** | 0.200 | 38.17 | 0.2312 | 0.239 | 0.587 | 0.734 | 0.383 | -0.6 | 0.917 |
| 3 d | persistence | 0.098 | 41.43 | 0.3504 | 0.088 | 0.226 | 0.311 | 0.273 | -9.7 | 0.730 |
| 3 d | climatology | 0.032 | 42.43 | 0.3706 | 0.078 | 0.336 | 0.511 | 0.109 | -6.7 | 0.667 |
| 7 d | **model** | 0.102 | 40.72 | 0.2765 | 0.121 | 0.435 | 0.625 | 0.231 | -9.0 | 0.888 |
| 7 d | persistence | 0.029 | 42.81 | 0.3829 | 0.028 | 0.113 | 0.188 | 0.119 | -11.4 | 0.625 |
| 7 d | climatology | 0.032 | 42.43 | 0.3706 | 0.078 | 0.336 | 0.511 | 0.109 | -6.7 | 0.667 |

Higher is better for PR-AUC, FSS, top-k capture and log correlation; lower for Brier and deviance.
The model is only useful where it beats both baselines.
