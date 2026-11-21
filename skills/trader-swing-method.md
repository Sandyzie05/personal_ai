---
name: Trader Swing Method
description: Daily-close setups (20-day breakout, oversold bounce), adapted from day-trader-skill (MIT) trader-scanner/exit
---

Adapted from https://github.com/jakeschincariol/day-trader-skill (MIT). Paper research only: you never place, size or price anything.

1. Setups on daily closes: `breakout20` = the close is above every close of the 20 sessions before it; `bounce` = RSI 14 was under 30 yesterday and today closed up. Entry is the next session's open; the trade is held at most 10 sessions.
2. Stops: `atr1` / `atr2` = 1 or 2 times the 14-day ATR under the entry, `pct3` = 3% under. A stop inside normal daily noise (`atr_pct`) gets hit by noise.
3. Targets in R. Break-even win rate is 1 / (1 + R).
4. Context: `dist_sma20_pct` / `dist_sma50_pct` (trend), `return_5d_pct` / `return_20d_pct` (extension), `volume_x` (participation), `off_high_pct`, and `history_*` (this setup on this stock's earlier sessions, outcomes known before the signal).
5. A breakout far above its averages after a big run is extended; a bounce against a falling 50-day average fights the trend. Prefer skip when evidence is thin (`history_fired` under 3) or mixed.
