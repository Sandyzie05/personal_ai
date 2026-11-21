# Trader: intraday day-trade research and planning (Track T)

Adapted from [day-trader-skill](https://github.com/jakeschincariol/day-trader-skill) (MIT, Jake Schincariol): 11 stdlib-Python
skills (scanner, news, entry, exit, timemachine, budget, risk, broker, alerts, analyst, pelosi). Rules: `platform_plan.md` §2
(S21-S23 below), card list: `roadmap.md` Track T.

**Advice only.** Trader researches setups and prints a plan (entry trigger, stop, target, odds, share count, rule check). It never
builds an order, never calls an order tool, never reads broker account data. The upstream `trader-broker` (the only skill that
places orders) is **not ported**; a paper-order path is planned card T9 and needs new invariants plus user sign-off.

## Skill map

| Upstream skill | Trader piece | Notes |
|---|---|---|
| scanner | `src/trader/scan.py` | Universe: Robinhood watchlists/scans (`forecaster.robinhood.import_symbols`) plus typed tickers. Daily setups (20-day high, bull flag, reclaimed 20-day average, oversold bounce) and opening-range breakout (ORB) on 5-minute bars. Pure functions over bars. |
| news | existing F7 `news_analyst` + `quarantine.extract_facts` | No new fetch path. Headlines are untrusted; summaries stay templated (S16). |
| entry | `src/trader/entry.py` | Opening range = first 15 minutes; `waiting` / `triggered` / `expired` on the first 5-minute close above it. Long only. |
| exit | `src/trader/exit_plan.py` | Stop (range mid/low, percent, ATR), target (1R / 2R / price). Refuses stop at/above entry, target at/below entry. |
| timemachine | `src/trader/timemachine.py` | Seeded Monte Carlo (stdlib `random`, default 10,000 draws) built from the ticker's own 5-minute bars plus a re-run on past sessions. Odds: target first / stop first / neither. A simulation, not a prediction. |
| budget | `src/trader/sizing.py` | Whole shares under user-entered max loss, budget and position cap. No broker data. |
| risk | `src/trader/risk.py` | PASS/BLOCK label: stop required, loss ≤ max, reward:risk ≥ min, entry window (New York time), daily loss. Never loosened to pass. |
| broker, alerts, analyst, pelosi | not ported | broker = execution (T9); alerts needs a daemon; analyst/pelosi out of scope. |

## Data

- Intraday: Yahoo chart `interval=5m` through the existing allowlisted `yahoo-chart` route (`PublicDataProviders.intraday`,
  generic UA per `config.HTTP_USER_AGENT`). Free, unofficial, can lag or rate-limit; labelled as such in the UI.
- Robinhood (read-only, S20): quotes, technical indicators, watchlist/scan import through the existing exact-name allowlists.
  No new tool name is added. Robinhood `5minute` historicals are an unverified live-check item (USER).

## Agents

Worker `trader_worker` (registered in `build_coordinator`, templated findings, no model text), edge `coordinator → trader`
(`TraderRequest`, `extra="forbid"`). `plan_for` (chat routing) is **not** changed: the Trader tab calls the pipeline directly. News
reaches it only via a future `DEPENDENCY_INPUTS` entry (S19), not added in the first cut. A tool-less narrator slot and a
`day-trader-method` skill pack were dropped from the first cut: with templated findings they would have nothing to do.

## Invariants

- **S21** Trader output has no order/ticket shape and no write tool is reachable.
- **S22** Sizing and risk use only user-entered limits; account figures never enter trader inputs or outputs.
- **S23** Monte Carlo is seeded (deterministic), bounded (`TRADER_*` caps) and labelled a simulation.

## Policy wording

`policy.screen` is not loosened. Allowed: "research day-trade setups for NVDA", "entry, stop and target levels for TSLA". Still
refused (known false positives, by design): "go long ...", "exit position ...", "buy-side ...", "place/send the order".

## Limits

Not financial advice. Most day traders lose money. 60 days of 5-minute bars cannot contain an earnings gap, halt or headline the
future will; a stop can fill worse than its price. Odds are a reality check only.
