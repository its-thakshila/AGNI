# Forecast evaluation (2025)

Models: `D:\NASA\Fire Prediction model\models_archive\models_20261115_1027_v3_weather_target` · every 2 issue day(s) · full land grid · evaluated 2026-11-15T12:03:18

| Lead | Method | PR-AUC | Brier ×1e4 | Poisson dev/cell | FSS 0.1° | FSS 0.5° | FSS 1.1° | Top-1000 row capture | Daily total bias % | Daily total log corr |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 d | **model** | 0.409 | 30.36 | 0.1391 | 0.457 | 0.745 | 0.832 | 0.649 | +6.4 | 0.928 |
| 1 d | persistence | 0.286 | 35.19 | 0.2301 | 0.273 | 0.479 | 0.567 | 0.565 | +0.3 | 0.856 |
| 1 d | climatology | 0.031 | 40.68 | 0.3220 | 0.078 | 0.346 | 0.530 | 0.120 | +5.3 | 0.615 |
| 3 d | **model** | 0.178 | 37.08 | 0.2112 | 0.223 | 0.566 | 0.719 | 0.390 | +9.4 | 0.867 |
| 3 d | persistence | 0.085 | 39.88 | 0.3119 | 0.082 | 0.214 | 0.300 | 0.282 | -0.3 | 0.646 |
| 3 d | climatology | 0.031 | 40.68 | 0.3220 | 0.078 | 0.346 | 0.530 | 0.120 | +5.3 | 0.615 |
| 7 d | **model** | 0.090 | 39.21 | 0.2472 | 0.113 | 0.431 | 0.631 | 0.241 | +2.0 | 0.831 |
| 7 d | persistence | 0.025 | 40.99 | 0.3391 | 0.026 | 0.112 | 0.190 | 0.127 | -0.9 | 0.502 |
| 7 d | climatology | 0.031 | 40.68 | 0.3220 | 0.078 | 0.346 | 0.530 | 0.120 | +5.3 | 0.615 |

Higher is better for PR-AUC, FSS, top-k capture and log correlation; lower for Brier and deviance.
The model is only useful where it beats both baselines.
