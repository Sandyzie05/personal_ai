# Forecasting system (`src/forecaster/`)

Research support for US stocks and ETFs: gather dated public evidence, freeze it, let independent specialists review it, and (only if you choose)
save the forecast so it can be scored after its horizon. **It never trades and never promises returns.** Scores are uncalibrated heuristics.

## Flow

```
ticker ─► validate ─► providers (public, fixed routes) ─► EvidenceSnapshot (immutable, hashed)
                                                              ├─► research prediction (heuristic, 5 / 21 sessions)
                                                              └─► ToT thesis search (tot.py): ≤6 theses ─► rubric ─► scenario-diverse beam (3)
                                                                    └─ only if not decisive: ScenarioSwarm critics (macro · valuation · event · downside,
                                                                       parallel, one shared review) ─► re-score / prune ─► select or abstain
      ─► thesis:<SYM> finding (uncertain when close, critic-less, or contradicting the heuristic)
      ─► [optional, you approve] save forecast ─► evaluate after horizon
```
## Modules

| Module | Role |
|---|---|
| `providers.py` | `PublicDataProviders`: fixed allowlisted routes (Yahoo chart + news search, Alpha Vantage, FRED, SEC EDGAR), timeouts, size caps, retry only transient errors, queries containing personal identifiers are refused. Returns typed results with explicit `completed / partial / unavailable`. |
| `models.py` | Immutable contracts (`ResearchRequest`, `EvidenceSnapshot`, `ResearchPrediction`, ...). |
| `research.py` | `ResearchPredictionService`: parallel market/macro/filing fetch, scoring, `abstained`/`unsupported` states. |
| `scenarios.py` | `ScenarioSwarmService`: 4 specialist reviews on the same frozen snapshot, bounded workers, deadline, model-call budget. |
| `tot.py` | **Tree-of-Thought thesis search** ([reasoning_design.md](reasoning_design.md)): competing bull/base/bear theses per evidence family, deterministic rubric, beam with a diversity slot, specialist critics only when depth 1 is not decisive, pruning with recorded reasons, conservative tie-break, abstention. Coordinator path only for now; the Forecast page still shows the raw swarm verdict. |
| `technical.py` | Deterministic indicators from closes (SMA/EMA/RSI/MACD/Bollinger, trend, support/resistance, volatility). ATR(14) and 20-bar volume ratio use `PriceBar.high/low/volume` (Yahoo/Alpha parsed; misaligned arrays dropped) and are reported as unavailable when absent. News failures carry their own `yahoo-news` SourceId. |
| `recommend.py` | **Research lean** (bullish/neutral/bearish + confidence + risk) from technicals and evidence; ranking and diversified selection. Never buy/sell wording; `insufficient_evidence` when inputs are missing. |
| `screens.py` | Period high/low, price-position (valuation proxy) and momentum screens over a bundled 50-name universe. States its window; no fundamentals. |
| `news.py` | News search and "today" report; stories are untrusted data (sanitised, length-capped), conflicting signals are surfaced as disagreement, never netted. |
| `outcomes.py`, `report.py` | Forecast records, outcome evaluation vs SPY, scoreboard, trend, CSV export (formula-injection safe), naive baselines, "insufficient sample" below 10. |
| `health.py` | Per-provider status/latency/failure counts shown in the UI. |
| `themes.py`, `events.py`, `filings.py`, `macro.py`, `source_registry.py`, `cache.py` | AI-infrastructure theme exposure, event extraction, SEC filing and FRED signals, source provenance, short-lived cache. |

## Correctness rules

- **Point in time:** evidence is stamped and frozen; historical (`as_of`) research is unsupported rather than faked.
- **No leakage:** an outcome counts only after its horizon elapsed and only if evaluated after the target close. Horizons 5 and 21 sessions are scored separately.
- **Explicit uncertainty:** provider outage, abstention, stale data or specialist disagreement lowers confidence and is shown; it is never hidden.
- **Metrics:** directional accuracy, return-estimate error, coverage/abstention rate, always-up and persistence baselines. Small samples report "insufficient sample".

## Bounds and privacy

Local inference is capped app-wide at `PERSONAL_AI_INFERENCE_CONCURRENCY` in-flight requests (default 2), and specialists can use the smaller `PERSONAL_AI_OMLX_WORKER_MODEL` (see [ai_engine.md](ai_engine.md) "Memory budget"). Max 5 orchestration rounds, shared tool/model-call budgets, 45 s research and 60 s scenario deadlines, 4 specialist workers (`config.FORECAST_*`).
Only tickers, topics and fixed parameters leave the machine; specialists run on the local model. Saved forecasts and outcomes live in the encrypted vault,
and saving needs an explicit click bound to the exact content shown.

## In the UI (Forecast page)

Section picker: **Research** (ticker research, local specialists, save and evaluate) · **Recommendations** (research leans) · **Validation** (scoreboard, CSV) ·
**Events** (news and conflicts) · **Drilldown & System** (technicals, provider health) · **Screens**. Nothing is fetched on page load; each section runs on a button.

## Known limits and stock_predictor port gaps (reviewed 2026-11-02)

stock_predictor's FastAPI/SQLAlchemy/Redis layers were dropped on purpose. These are the gaps in the
*reasoning* that was ported:

| stock_predictor | Here | Gap | Disposition |
|---|---|---|---|
| `research_prediction.py` | `research.py` | 63-session horizon dropped; news score never fed in (`event_strength` is always 0, because headlines are untrusted text and stay UI-only); buy/sell labels, risk factors and signal breakdown dropped | Keep news out of scoring until it can enter as quarantined, templated sentiment counts (backlog B17); horizons stay 5/21 |
| `scenario_swarm.py` | `scenarios.py` + `tot.py` | Original only critiqued one fixed direction, with nothing generated, compared or pruned | **Closed by ToT** (Week 4): specialists now act as critics over competing theses |
| `recommendation_engine.py`, `market_intelligence.py` | `recommend.py`, `news.py` | Fundamental score, valuation bonus, idea ranking and swarm-adjusted ranking dropped; `technical_score` ignores the ATR/volume that `technical.py` computes | Backlog B18; ranking stays UI-only |
| `stock_lists.py` | `screens.py` | Valuation from a price z-score, not P/E or P/B; "period" high/low, not all-time; fixed 50-name universe | Documented proxies; fundamentals need a new provider (B11 follow-up) |
| `local_model_analysis.py` | none | Single-call thesis review not ported | Superseded by ToT critics |
| `prediction_tracker.py` | `outcomes.py` | Nothing records forecasts or evaluates outcomes automatically; UI-only, explicit approval | Intentional (approval-gated persistence) |

Coordinator workers consume only `research.py`, `tot.py`/`scenarios.py` and the technical lean.
`recommend.rank_leans`, `screens` and `report` are Forecast-page features.
