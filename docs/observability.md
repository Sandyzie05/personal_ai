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

**Traces (Tempo).** One trace per top-level run; span names say what ran. Open any trace id (dashboard tables or
Explore → Tempo) for the waterfall:

```
chat email                      agent, planner, plan=email:ok, claims, escalations, escalation_kinds
└─ wave 1                       agents in the wave
   └─ step email                status, attempts, findings, depends_on
      └─ quarantine EmailFacts  source_kind=gmail, chars, flagged, rule=send_or_forward, extract_error
         └─ chat Qwen3-4B       LLM call: temperature, max_tokens, messages, response_format, tools offered /
                                tool_calls returned (names), input/output tokens, finish reason
sync gmail                      new, duplicates, notices, not_bill, failed, model_calls
├─ tool search_threads          server, http.status
└─ tool get_thread → quarantine BillEmailFacts → chat <model>
forecast ai_review → forecast.critics (agents=bull:completed,...) → chat <model> …; forecast.thesis_explainer
trader.paper_agent              agent_id, model, status, latency_ms, invalid_reason
agent turn → chat <model> → tool <mcp tool>   (configurable agents' tool loop)
```

**Metrics (Prometheus).** `personal_ai_runs_total{run=chat|sync|forecast|trader|agent}` (every root trace),
steps, refusals, escalations, injection flags `{rule, source_kind, schema}`, bus messages, RAG turns, reviews, slot runs,
model/tool calls; latency + token histograms. (`run`, not `job`: Prometheus reserves `job` for the service name.)

**Logs (Loki).** Every Activity-log entry plus `injection_flagged` events, level INFO/WARN, body built from the cleaned
fields, e.g. `injection_flagged schema=EmailFacts source_kind=gmail rule=send_or_forward
action=free_text_dropped_closed_fields_kept`. Rule `send_or_forward` also matches ordinary marketing ("email us at"),
which is why the counter rises on promotional mail; the text is inert either way (closed schema).

**Dashboards** (folder *personal_ai*; every panel has a description, hover the ⓘ): *Agent runs* (runs by type, steps,
latency, Recent traces), *Guardrails* (refusals, injection flags by rule/source, escalations, WARN log), *Models* (LLM calls,
latency, tokens, slot runs, LLM-call traces, Forecast/Trader traces), *Sync and tools* (tool calls, Sync traces, activity log).
Explore is enabled for the local viewer (`GF_USERS_VIEWERS_CAN_EDIT`): without it trace links opened the home page.

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
