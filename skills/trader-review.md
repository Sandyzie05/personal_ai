---
name: Trader Review
description: How to review a paper agent's record against its take-every-signal baseline
---

1. Compare the agent with the baseline that takes every signal with the default plan: P&L, win rate, average R, drawdown.
2. `skip_saved` = share of the agent's skipped signals that would have hit the stop: high is good skipping.
3. Under 5 closed trades in total is `too_few_trades`. Within 1% of the starting cash in P&L is `matches_baseline`.
4. Suggest one closed change, or `no_change`. Results on few trades are noise; say so with low confidence.
