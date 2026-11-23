"""Track O: OpenTelemetry traces, metrics and logs; allowlist, loopback-only export, no personal data in telemetry."""

import json
from datetime import date

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.ingest import ReadOnlyMcp
from src.agents.orchestration.policy import RequestRefused
from src.agents.orchestration.schemas import Finding, Plan, PlanStep
from src.data_ingestion import gmail_bills
from src.observability import otel
from test_forecast_page import MemoryVault
from test_gmail_bills import BODY, Extractor, FakeGmail

SECRET_Q = "what did I pay ACCT-778812 for Rocky Mountain Power"


@pytest.fixture
def tel():
    spans, metrics, logs = (
        InMemorySpanExporter(),
        InMemoryMetricReader(),
        InMemoryLogRecordExporter(),
    )
    assert otel.init(span_exporter=spans, metric_reader=metrics, log_exporter=logs)
    yield spans, metrics, logs
    otel.shutdown()


def _metrics(reader):
    out = {}
    for rm in reader.get_metrics_data().resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                for p in m.data.data_points:
                    out.setdefault(m.name, []).append(
                        (dict(p.attributes), getattr(p, "value", None))
                    )
    return out


def _everything(spans, metrics, logs) -> str:
    """Every exported attribute/name/body as one string (leakage check)."""
    parts = [
        json.dumps(
            {
                "n": s.name,
                "a": dict(s.attributes or {}),
                "st": str(s.status.description),
            }
        )
        for s in spans.get_finished_spans()
    ]
    parts += [
        json.dumps(
            {"b": str(r.log_record.body), "a": dict(r.log_record.attributes or {})}
        )
        for r in logs.get_finished_logs()
    ]
    parts.append(json.dumps(_metrics(metrics), default=str))
    return "\n".join(parts)


def test_disabled_by_default_and_endpoint_must_be_loopback(monkeypatch):
    assert otel.init() is False and not otel.enabled()
    with otel.span("x") as s:
        s.set(agent="a")  # no-op, no error
    monkeypatch.setattr(otel, "PERSONAL_AI_OTEL_ENABLED", True)
    with pytest.raises(ValueError, match="loopback"):
        otel.init(endpoint="http://collector.example.com:4318")
    assert not otel.enabled()


def test_clean_keeps_only_allowlisted_scalar_tokens():
    got = otel.clean(
        {
            "agent": "expense",
            "question": "x",
            "provider": "Rocky Mountain Power",
            "steps": 3,
            "status": "has spaces so prose",
            "fields": ["a"],
            "ok": True,
        }
    )
    assert got == {"agent": "expense", "steps": 3, "ok": True}


def test_one_connected_trace_with_worker_spans_metrics_and_logs(tel):
    from src.agents.orchestration import service

    spans, metrics, logs = tel
    v = MemoryVault()

    def worker(step, inputs):
        return [
            Finding(
                agent=step.agent,
                claim_key=step.id,
                value=1.0,
                summary="s",
                confidence=0.8,
            )
        ]

    c = Coordinator({"expense": worker, "market": worker})
    c.audit_vault = v
    plan = Plan(
        steps=(
            PlanStep(id="1", agent="expense", goal="g"),
            PlanStep(id="2", agent="market", goal="g"),
            PlanStep(id="3", agent="expense", goal="g", depends_on=("1",)),
        )
    )
    service._EMAIL_ONLY  # noqa: B018 (module loaded)
    import src.agents.orchestration.service as svc

    orig = svc.plan_for
    svc.plan_for = lambda q: plan
    try:
        rep = service.run_query(c, SECRET_Q)
    finally:
        svc.plan_for = orig
    assert all(r.status == "ok" for r in rep.steps)
    by = {s.name: s for s in spans.get_finished_spans()}
    root = by["chat coordinator"]
    steps = [s for s in spans.get_finished_spans() if s.name.startswith("step ")]
    waves = [s for s in spans.get_finished_spans() if s.name.startswith("wave ")]
    assert len(steps) == 3 and len(waves) == 2
    assert {w.parent.span_id for w in waves} == {root.context.span_id}
    assert {s.parent.span_id for s in steps} <= {
        w.context.span_id for w in waves
    }  # pool threads keep the parent
    assert len({s.context.trace_id for s in spans.get_finished_spans()}) == 1
    assert (
        root.attributes["steps"] == 3 and root.attributes["agents"] == "expense,market"
    )
    m = _metrics(metrics)
    assert (
        sum(
            v
            for a, v in m["personal_ai.steps"]
            if a == {"agent": "expense", "status": "ok"}
        )
        == 2
    )
    assert "personal_ai.run.duration" in m and "personal_ai.step.duration" in m
    with pytest.raises(RequestRefused):
        service.run_query(c, "buy 100 shares of AAPL for me")
    assert v.records["agent_activity_log"]["entries"][-1] == {
        **v.records["agent_activity_log"]["entries"][-1],
        "kind": "refused",
    }
    assert any(a.get("refusal") for a, _ in _metrics(metrics)["personal_ai.refusals"])
    assert any(
        r.log_record.attributes.get("kind") == "refused"
        for r in logs.get_finished_logs()
    )
    assert "ACCT-778812" not in _everything(
        spans, metrics, logs
    ) and "Rocky" not in _everything(spans, metrics, logs)


def test_gmail_sync_and_quarantine_spans_carry_no_email_text_or_provider(tel):
    spans, metrics, logs = tel
    v = MemoryVault()
    gmail_bills.add_sender(
        v, "Rocky Mountain Power", "electricity", "rockymountainpower.net"
    )
    rep = gmail_bills.sync(
        v, ReadOnlyMcp(FakeGmail(), "gm"), Extractor(), today=date(2026, 11, 20)
    )
    assert rep.new
    names = [s.name for s in spans.get_finished_spans()]
    assert {"sync gmail", "tool search_threads", "tool get_thread", "quarantine BillEmailFacts"} <= set(names)
    sync = next(s for s in spans.get_finished_spans() if s.name == "sync gmail")
    assert sync.attributes["new"] == 1 and sync.attributes["model_calls"] == 1
    dump = _everything(spans, metrics, logs)
    for secret in (BODY, "84.12", "Rocky Mountain Power", "evil.example"):
        assert secret not in dump


def test_stack_binds_loopback_only_and_dashboards_are_valid():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "observability"
    compose = (root / "docker-compose.yml").read_text()
    ports = re.findall(r'^\s*-\s*"([^"]+)"', compose, re.M)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports)
    assert "volumes:" in compose and "lgtm" in compose and "0.0.0.0" not in compose
    boards = sorted(root.glob("dashboards/*.json"))
    assert [b.stem for b in boards] == ["agent-runs", "guardrails", "models", "sync"]
    for b in boards:
        d = json.loads(b.read_text())
        assert d["uid"].startswith("pai-") and d["panels"]
        assert all(t.get("expr") or t.get("query") for p in d["panels"] for t in p["targets"])


def test_activity_table_has_one_type_per_column():
    import pyarrow as pa

    from src.interface.agents import activity_rows

    rows = activity_rows([{"kind": "bus", "outcome": "delivered"},
                          {"agent_name": "Gmail bill sync", "model_calls": 6, "duration_ms": 7000, "ok": True},
                          {"kind": "rag_turn", "model_calls": None, "duration_ms": None}])
    pa.Table.from_pylist(rows)  # live crash: "Expected bytes, got a 'int' object" on 'model calls'
    assert rows[1]["model calls"] == "6" and rows[0]["agent"] == "bus" and rows[2]["ms"] == 0


def test_logs_have_level_and_readable_body_and_forecast_runs_are_traced(tel):
    import asyncio

    spans, metrics, logs = tel
    v = MemoryVault()
    from src.agents import store
    from src.agents.slots import log_run

    @otel.traced("forecast.news_analyst")
    def run():
        log_run(v, "forecast.news_analyst", "builtin-news-analyst", "invalid", 900, "schema")

    @otel.traced("forecast.critics")
    async def critics():
        return 1

    run()
    assert asyncio.run(critics()) == 1
    store.log_activity(v, {"kind": "refused", "refusal": "trade"})
    store.log_activity(v, {"agent_name": "Gmail bill sync", "tool": "search_threads/get_thread", "ok": True,
                           "args_digest": "Rocky Mountain Power", "duration_ms": 7000, "model_calls": 6})
    recs = [r.log_record for r in logs.get_finished_logs()]
    by = {str(r.body).split(" ")[0]: r for r in recs}
    assert str(by["refused"].body) == "refused refusal=trade" and by["refused"].severity_text == "WARN"
    assert by["slot_run"].severity_text == "WARN" and "slot_id=forecast.news_analyst" in str(by["slot_run"].body)
    assert by["tool_call"].severity_text == "INFO" and "model_calls=6" in str(by["tool_call"].body)
    assert "Rocky" not in json.dumps([str(r.body) for r in recs] + [dict(r.attributes) for r in recs])
    names = {s.name for s in spans.get_finished_spans()}
    assert {"forecast.news_analyst", "forecast.critics"} <= names
    assert ({"slot_id": "forecast.news_analyst", "status": "invalid"}, 1) in _metrics(metrics)["personal_ai.slot.runs"]


def test_readable_trace_tree_llm_call_attrs_runs_per_job_and_injection_rule(tel):
    from src.agents.orchestration.quarantine import extract_facts
    from src.agents.orchestration.schemas import EmailFacts
    from src.ai_engine.omlx_client import OmlxClient

    spans, metrics, logs = tel
    c = OmlxClient.__new__(OmlxClient)
    c.model, c.temperature, c.max_tokens, c._supports_tools = "m-4b", 0.1, 256, True
    c._inference_slot = lambda: __import__("contextlib").nullcontext()
    c._make_request = lambda *a: {"usage": {"prompt_tokens": 40, "completion_tokens": 5}, "choices": [{
        "finish_reason": "tool_calls", "message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "type": "function", "function": {"name": "search_threads", "arguments": "{}"}}]}}]}
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("search_threads", "get_thread")]
    with otel.span("agent turn"):
        c.chat(messages=[{"role": "user", "content": "SECRET-QUESTION"}], tools=tools)
    llm = next(s for s in spans.get_finished_spans() if s.name == "chat m-4b")
    a = dict(llm.attributes)
    assert a["gen_ai.request.tools"] == "search_threads,get_thread" and a["gen_ai.response.tool_calls"] == "search_threads"
    assert a["gen_ai.usage.input_tokens"] == 40 and a["messages"] == 1 and a["gen_ai.request.temperature"] == 0.1
    assert "SECRET-QUESTION" not in json.dumps(a)
    ex = Extractor(reply={"sender_domain": "shop.example", "category": "other"})
    extract_facts("Big sale! Email us at deals@shop.example SECRET-BODY", "gmail:abc", EmailFacts, ex)
    q = next(s for s in spans.get_finished_spans() if s.name == "quarantine EmailFacts")
    assert q.attributes["rule"] == "send_or_forward" and q.attributes["source_kind"] == "gmail"
    rec = next(r.log_record for r in logs.get_finished_logs() if str(r.log_record.body).startswith("injection_flagged"))
    assert "rule=send_or_forward" in str(rec.body) and "source_kind=gmail" in str(rec.body)
    assert "action=free_text_dropped_closed_fields_kept" in str(rec.body)
    assert "SECRET-BODY" not in _everything(spans, metrics, logs)
    m = _metrics(metrics)
    assert ({"run": "agent"}, 1) in m["personal_ai.runs"] and ({"run": "quarantine"}, 1) in m["personal_ai.runs"]
    assert any(attrs.get("rule") == "send_or_forward" for attrs, _ in m["personal_ai.injection_flags"])


def test_email_digest_agent_answers_off_topic_asks_with_its_scope():
    from src.agents.orchestration import service
    from src.agents.orchestration.workers import render_email_digest

    class Coord:
        workers, records, audit_vault, memory_box = {"email": None}, [], None, None
        prefetch, fetched = {"email": lambda: (_ for _ in ()).throw(AssertionError("mail must not be read"))}, {}

    rep = service.run_query(Coord(), "Can you check my account details for Robinhood?", only="email")
    assert rep.steps == [] and "only reads your email" in render_email_digest(rep)
    assert service._EMAIL_WORDS.search("Summarize emails for today")
    assert not service._NOT_EMAIL.search("anything new?")  # vague asks still read mail
