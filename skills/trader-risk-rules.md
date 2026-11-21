---
name: Trader Risk Rules
description: Risk discipline from day-trader-skill (MIT) trader-risk/budget, for paper agents
---

1. Code sizes every paper trade to the max loss per trade and checks reward:risk, the entry window and the budget. You cannot change those limits and never ask to.
2. A stop is a plan, not a guarantee: a fast market or a gap can fill past it.
3. Confidence is the probability your `predicted` outcome happens. Be calibrated: 0.5 means a coin flip; overconfidence is scored against you.
