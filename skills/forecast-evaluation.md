---
name: Forecast Evaluation
description: Judge forecasts only after their horizon, without leakage
---

1. A forecast is evaluated only after its horizon has elapsed, using data available after the cutoff, never earlier.
2. Report directional accuracy, error, coverage and abstentions next to the naive baselines (always-up, persistence).
3. Samples below the minimum size are reported as insufficient sample, not as a percentage.
4. Never use an outcome to rewrite the original forecast or its reasoning.
