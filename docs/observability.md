# Observability (Track O): OpenTelemetry → local Grafana LGTM

Decision: platform_plan D14. Off by default. Telemetry never leaves the machine and never carries personal data.

## Run it

```bash
make obs-up       # grafana/otel-lgtm: Grafana http://127.0.0.1:3000, OTLP/HTTP 127.0.0.1:4318 (loopback only)
make run-obs      # the app with PERSONAL_AI_OTEL_ENABLED=true
make obs-down     # stops the stack and deletes all telemetry (no volume)
```

**No `personal_ai` folder in Grafana?** Check you are on *our* Grafana: `docker ps` must list `personal_ai_lgtm`, and
`curl -s 127.0.0.1:${OBS_GRAFANA_PORT:-3000}/api/search?query=personal_ai` must return the folder. If `make obs-up` failed
(port in use) the container never started and whatever answers on :3000 is another Grafana. Panels stay empty until
`make run-obs` has done at least one run (metrics export every 10 s). Verified live 2026-11-22: folder + 4 dashboards
provisioned, metrics in Prometheus, `agent.run` traces in Tempo, guardrail logs in Loki.

Port taken (e.g. a local Jaeger already listens on 4318; `make obs-up` checks and stops)? Either stop it, or pick other
host ports: `OBS_OTLP_PORT=14318 OBS_GRAFANA_PORT=3001 make obs-up run-obs` (`run-obs` exports to the same port). An
existing Jaeger also works as a traces-only target: `PERSONAL_AI_OTEL_ENABLED=true make run` (metrics/logs then fail
quietly; dashboards need the LGTM stack).

Settings (`src/config.py`): `PERSONAL_AI_OTEL_ENABLED` (default false), `PERSONAL_AI_OTEL_ENDPOINT` (default
`http://127.0.0.1:4318`; a non-loopback endpoint is refused at start-up).

## What is emitted

| Signal | Name | Where |
|---|---|---|
| Trace root | `agent.run` (steps, claims, conflicts, escalations, agents, refused/refusal) | `service.run_query` |
| Spans | `coordinator.wave` → `agent.step` (agent, status, attempts, error type; pool threads keep the parent) | `coordinator.py` |
| Spans | `gen_ai.chat` (GenAI semconv: model, input/output tokens, finish reason) | `OmlxClient.chat` |
| Spans | `quarantine.extract` (schema, flagged, error), `tool.call` (tool, server, HTTP status), `sync.gmail` (counts), `tot.search` (termination, nodes, critic calls) | quarantine, `ReadOnlyMcp`, `GmailApi`, `gmail_bills`, `tot` |
| Root spans (Forecast / Trader) | `forecast.critics`, `forecast.thesis_explainer`, `forecast.watchlist_explainer`, `forecast.news_analyst`, `forecast.portfolio_fit`, `trader.paper_agent` (model calls nest under them) | `@otel.traced` on the entry functions |
| Metrics | `personal_ai.runs/steps/refusals/escalations/injection_flags/bus.messages/rag.turns/reviews/model.calls/tool.calls/activity`; histograms `personal_ai.run.duration`, `personal_ai.step.duration`, `gen_ai.client.operation.duration`, `gen_ai.client.token.usage` | `src/observability/otel.py` |
| Logs | every encrypted Activity-log entry mirrored as an OTel log record (same code-built fields, cleaned); level INFO, WARN for refusals, injection flags, failed/invalid runs, rejected bus messages; body built from the cleaned fields (e.g. `refused refusal=trade`, `slot_run slot_id=forecast.news_analyst status=completed latency_ms=1200`) | `store.log_activity` |

Dashboards (Grafana folder *personal_ai*): **Agent runs** (with a *Recent traces* table: click a trace id for the span
tree), **Guardrails**, **Models** (incl. Forecast/Trader slot runs + their traces), **Sync and tools** (incl. Sync traces)
(`observability/dashboards/*.json`). Counter panels show running totals since the app started (a rate over a short
window drops a series' first sample, so a single Sync or forecast run showed nothing). Traces also: Explore → Tempo.

## Privacy controls (why this is safe to store in plain text)

- `otel.clean`: only keys in `ALLOWED_ATTRS`, only scalars, strings must be id/enum shaped (no spaces). Prompts, email or
  document text, questions, amounts, account ids and provider names have no allowed key.
- Exceptions recorded as their type name only. No auto-instrumentation (HTTP/LLM instrumentors record bodies).
- Private tracer/meter/logger providers: third-party libraries cannot emit through ours.
- Eval row "Telemetry leakage" (hard gate): golden set + injection corpus + Gmail sync with in-memory exporters, 0 canaries.

## Course tools considered

LangSmith (cloud; prompts with vault data leave the machine), Langfuse self-hosted (Postgres + ClickHouse + Redis, too heavy),
CrewAI (agent framework, not observability) were rejected; Arize Phoenix (open source, OTel) can be added later as a second
local OTLP target. See roadmap Track O.
