---
name: Trader ORB Method
description: Opening-range breakout method, adapted from day-trader-skill (MIT, Jake Schincariol) trader-entry/exit/timemachine
---

Adapted from https://github.com/jakeschincariol/day-trader-skill (MIT). Paper research only: you never place, size or price anything.

1. The setup: the opening range is the first 15 minutes (9:30-9:45 New York). The trigger is the first completed 5-minute bar that CLOSES above the range high. A wick is not a trigger. Long only, flat by 15:55.
2. Stops: `or-mid` (range midpoint, the default) or `or-low` (wider: fewer shares for the same max loss, survives more noise).
3. Targets in R (multiples of the risk). Break-even win rate is 1 / (1 + R): 50% at 1R, 33% at 2R, 25% at 3R.
4. Read `sim_target_first` / `sim_stop_first` (a replay of the rest of the day from this stock's own past bars) against that break-even, and `history_*` (the same rule on past sessions). Within 5 points of break-even is a coin flip: prefer skip.
5. Warning signs: a tiny range (`or_range_pct` under 0.3 is noise), a weak trigger bar (`trigger_volume_x` under 1), a late trigger (`minutes_after_open` over 90), a big gap already extended (`gap_pct` over 3).
6. Most day trades lose. Skipping is a valid, often correct, answer.
