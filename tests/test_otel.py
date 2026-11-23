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
    root = by["agent.run"]
    steps = [s for s in spans.get_finished_spans() if s.name == "agent.step"]
    waves = [s for s in spans.get_finished_spans() if s.name == "coordinator.wave"]
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
    assert {"sync.gmail", "tool.call", "quarantine.extract"} <= set(names)
    sync = next(s for s in spans.get_finished_spans() if s.name == "sync.gmail")
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
        assert all(t["expr"] for p in d["panels"] for t in p["targets"])


def test_activity_table_has_one_type_per_column():
    import pyarrow as pa

    from src.interface.agents import activity_rows

    rows = activity_rows([{"kind": "bus", "outcome": "delivered"},
                          {"agent_name": "Gmail bill sync", "model_calls": 6, "duration_ms": 7000, "ok": True},
                          {"kind": "rag_turn", "model_calls": None, "duration_ms": None}])
    pa.Table.from_pylist(rows)  # live crash: "Expected bytes, got a 'int' object" on 'model calls'
    assert rows[1]["model calls"] == "6" and rows[0]["agent"] == "bus" and rows[2]["ms"] == 0
