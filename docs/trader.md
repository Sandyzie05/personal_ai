# Trader: intraday day-trade research and planning (Track T)

Adapted from [day-trader-skill](https://github.com/jakeschincariol/day-trader-skill) (MIT, Jake Schincariol): 11 stdlib-Python
skills (scanner, news, entry, exit, timemachine, budget, risk, broker, alerts, analyst, pelosi). Rules: `platform_plan.md` §2
(S21-S23 below), card list: `roadmap.md` Track T.

**Advice only.** Trader researches setups and prints a plan (entry trigger, stop, target, odds, share count, rule check). It never
builds an order, never calls an order tool, never reads broker account data. The upstream `trader-broker` (the only skill that
places orders) is **not ported**. Instead, AI **paper agents** (below) trade simulated money in a local ledger; sending
real or broker-paper orders is planned card T9 and needs new invariants plus user sign-off.

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
| broker | `src/trader/paper.py` (local paper ledger) | Simulated fills from real bars; nothing is sent anywhere. Broker orders = T9. |
| alerts, analyst, pelosi | not ported | alerts needs a daemon; analyst/pelosi out of scope. |

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

## Paper agents (Forecast → Trader → Paper agents)

Three LLM strategy agents, one per upstream method, each a tool-less slot editable in Agents → Forecast workflow:

| Agent (slot) | Signals | Skills (`skills/`) | Stop rules |
|---|---|---|---|
| ORB Paper Trader (`forecast.trader.orb`) | opening-range breakout, 5-minute bars, last 60 days | `trader-orb-method`, `trader-risk-rules` | or-mid, or-low |
| Breakout Paper Trader (`forecast.trader.breakout`) | 20-day closing-high breakout, daily bars, 1 year | `trader-swing-method`, `trader-risk-rules` | atr1, atr2, pct3 |
| Bounce Paper Trader (`forecast.trader.bounce`) | RSI-14 oversold bounce, daily bars, 1 year | `trader-swing-method`, `trader-risk-rules` | atr1, atr2, pct3 |
| Paper Trading Reviewer (`forecast.trader.reviewer`) | the scoreboard | `trader-review` | - |

Flow per signal (`src/trader/runner.py`): code detects signals (`strategies.py`) and builds **features from bars up to the signal
only** (no lookahead) as relative numbers: no ticker, date or price level reaches the model, so it cannot lean on remembered history.
The agent replies with closed choices only (`TraderDecision`: take/skip, stop rule, target R, predicted first outcome, confidence,
closed reason). Code then sets entry, size (`sizing.shares`, fixed per-trade budget) and the risk gate (`risk.check`), and
`paper.execute` resolves the outcome from the bars that followed (stop first when one bar touches both; slippage
`TRADER_SLIPPAGE_BPS` each side). Every signal is also traded by a **baseline** that takes everything with the default plan, so each
agent is judged on whether its choices beat "take every signal".

- **Backtest** replays the past year (ORB: the 60 days Yahoo serves). **Forward** decides only on the newest session (no hindsight).
  The first run that records a trade sets its mode.
- **Budget**: at most `TRADER_AGENT_MAX_CALLS` new decisions per run, newest signals first; decisions are cached in the ledger and
  reused, so repeated runs extend coverage further back at no cost. With no local model only the baseline is recorded.
- **Ledger**: vault key `trader_paper_v1` (encrypted), trades older than `TRADER_HISTORY_DAYS` (365) pruned. ORB trades persist after
  their bars leave Yahoo's 60-day window, so the intraday record grows to a year with regular runs.
- **Scoreboard** (`performance.py`): per agent/baseline P&L, return, win rate, net average R, max drawdown, prediction accuracy,
  Brier score of the confidence, `skip_saved` (share of skips that would have hit the stop); equity curve, P&L by month, trade log.
- **Reviewer**: closed verdict + one closed suggestion, shown next to the code's own verdict from the numbers and flagged when they
  disagree. Suggestions are never applied automatically; tweak an agent in the Agents tab.

## Invariants

- **S21** Trader output has no order/ticket shape and no write tool is reachable.
- **S22** Sizing and risk use only user-entered limits; account figures never enter trader inputs or outputs.
- **S23** Monte Carlo is seeded (deterministic), bounded (`TRADER_*` caps) and labelled a simulation.
- **S24** Paper agents see code-built numbers only (no ticker/date/price, no lookahead) and answer in closed choices; code alone sets
  entry, size and the risk gate; the paper ledger is local (vault) and never reaches a broker.

## Policy wording

`policy.screen` is not loosened. Allowed: "research day-trade setups for NVDA", "entry, stop and target levels for TSLA". Still
refused (known false positives, by design): "go long ...", "exit position ...", "buy-side ...", "place/send the order".

## Limits

Not financial advice. Most day traders lose money. 60 days of 5-minute bars cannot contain an earnings gap, halt or headline the
future will; a stop can fill worse than its price. Odds are a reality check only. Paper results use free unofficial bars, a fixed
slippage and no partial fills, commissions or halts; a backtest on one year of a few tickers is a small sample.
