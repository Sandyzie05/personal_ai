"""OpenTelemetry for the agent platform (Track O, platform_plan D14). Off by default.

Telemetry is stored in plain text by the local collector, so the controls are here, not in the stack:
- export only to a loopback OTLP endpoint (refused otherwise);
- private providers (no global provider: third-party libraries cannot emit through ours);
- every span/metric/log attribute passes `clean`: only names in ALLOWED_ATTRS, only scalars, strings shaped like
  ids/enums/model names. Prompts, email/document text, questions, amounts, account ids and provider names have no
  allowed key and are dropped. Exceptions are recorded as their type name only."""

import re
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from src.config import PERSONAL_AI_OTEL_ENABLED, PERSONAL_AI_OTEL_ENDPOINT

ALLOWED_ATTRS = frozenset(
    {
        # run / plan / coordinator
        "agent",
        "agents",
        "only",
        "planner",
        "steps",
        "step_id",
        "status",
        "attempts",
        "error_type",
        "wave",
        "waves",
        "claims",
        "conflicts",
        "escalations",
        "escalation_kind",
        "refused",
        "refusal",
        "overlap",
        # model calls (OTel GenAI semantic conventions)
        "gen_ai.system",
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.response.finish_reasons",
        "stream",
        # tools / quarantine / bus / RAG / sync
        "tool",
        "server",
        "http.status",
        "schema",
        "flagged",
        "extract_error",
        "sender",
        "receiver",
        "outcome",
        "reason",
        "evidence",
        "top_score",
        "grounded",
        "abstained",
        "regenerated",
        "threads",
        "new",
        "duplicates",
        "notices",
        "not_bill",
        "failed",
        "model_calls",
        "kind",
        "ok",
        "duration_ms",
        "model",
        "decision",
        "job",
        "source",
        "slot_id",
        "agent_id",
        "latency_ms",
        "invalid_reason",
    }
)
_SAFE = re.compile(
    r"^[A-Za-z0-9_.:/@+,-]{0,160}$"
)  # ids, enums, model ids; no spaces => no prose
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "tracer": None,
    "meter": None,
    "logger": None,
    "instruments": {},
    "providers": (),
}

COUNTERS = {
    "personal_ai.runs": "Coordinated agent runs",
    "personal_ai.steps": "Agent steps by agent and status",
    "personal_ai.refusals": "Requests refused before planning",
    "personal_ai.escalations": "Escalations by kind",
    "personal_ai.injection_flags": "Untrusted text flagged as an injection attempt",
    "personal_ai.bus.messages": "Inter-agent messages by outcome",
    "personal_ai.rag.turns": "RAG turns by grounded/abstained",
    "personal_ai.reviews": "Reviewer decisions",
    "personal_ai.model.calls": "Model calls by model",
    "personal_ai.tool.calls": "Read-only tool calls",
    "personal_ai.activity": "Activity log entries by kind",
    "personal_ai.slot.runs": "Forecast/Trader agent slot runs by slot and status",
}
HISTOGRAMS = {
    "personal_ai.run.duration": "ms",
    "personal_ai.step.duration": "ms",
    "gen_ai.client.operation.duration": "s",
    "gen_ai.client.token.usage": "{token}",
}


def is_loopback(url: str) -> bool:
    from src.ai_engine.omlx_client import _is_loopback

    return _is_loopback(url)


def clean(attrs: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in attrs.items():
        if k not in ALLOWED_ATTRS or v is None:
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str) and _SAFE.match(v):
            out[k] = v
    return out


def enabled() -> bool:
    return _state["tracer"] is not None


def init(
    span_exporter=None,
    metric_reader=None,
    log_exporter=None,
    endpoint: Optional[str] = None,
) -> bool:
    """Start exporting. Without explicit exporters (tests pass in-memory ones) this is a no-op unless
    PERSONAL_AI_OTEL_ENABLED; the OTLP endpoint must be loopback."""
    with _lock:
        if _state["tracer"] is not None:
            return True
        explicit = span_exporter is not None  # tests: export synchronously
        if not explicit:
            if not PERSONAL_AI_OTEL_ENABLED:
                return False
            endpoint = (endpoint or PERSONAL_AI_OTEL_ENDPOINT).rstrip("/")
            if not is_loopback(endpoint):
                raise ValueError(
                    "PERSONAL_AI_OTEL_ENDPOINT must be loopback (telemetry never leaves this machine)"
                )
            from opentelemetry.exporter.otlp.proto.http._log_exporter import (
                OTLPLogExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            span_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
                export_interval_millis=10_000,
            )
            log_exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import (
            BatchLogRecordProcessor,
            SimpleLogRecordProcessor,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            SimpleSpanProcessor,
        )

        # One instance id per app process: each restart is its own series, so dashboards can sum per-process maxima.
        res = Resource.create({"service.name": "personal_ai", "service.instance.id": uuid.uuid4().hex[:12]})
        tp = TracerProvider(resource=res)
        tp.add_span_processor(
            (SimpleSpanProcessor if explicit else BatchSpanProcessor)(span_exporter)
        )
        mp = MeterProvider(
            resource=res, metric_readers=[metric_reader] if metric_reader else []
        )
        lp = LoggerProvider(resource=res)
        if log_exporter is not None:
            lp.add_log_record_processor(
                (SimpleLogRecordProcessor if explicit else BatchLogRecordProcessor)(
                    log_exporter
                )
            )
        meter = mp.get_meter("personal_ai")
        inst = {n: meter.create_counter(n, description=d) for n, d in COUNTERS.items()}
        inst.update(
            {n: meter.create_histogram(n, unit=u) for n, u in HISTOGRAMS.items()}
        )
        _state.update(
            tracer=tp.get_tracer("personal_ai"),
            meter=meter,
            logger=lp.get_logger("personal_ai"),
            instruments=inst,
            providers=(tp, mp, lp),
        )
        return True


def shutdown() -> None:
    with _lock:
        for p in _state["providers"]:
            p.shutdown()
        _state.update(
            tracer=None, meter=None, logger=None, instruments={}, providers=()
        )


class _NoSpan:
    def set(self, **attrs) -> None:
        pass


class _Span:
    def __init__(self, span):
        self._span = span

    def set(self, **attrs) -> None:
        for k, v in clean(attrs).items():
            self._span.set_attribute(k, v)


@contextmanager
def span(name: str, **attrs) -> Iterator[Any]:
    """Child of the current span. `.set(**attrs)` adds cleaned attributes. An exception marks the span as an error
    with its type name only (messages can quote untrusted text) and is re-raised."""
    tracer = _state["tracer"]
    if tracer is None:
        yield _NoSpan()
        return
    from opentelemetry.trace import Status, StatusCode

    with tracer.start_as_current_span(
        name,
        attributes=clean(attrs),
        record_exception=False,
        set_status_on_exception=False,
    ) as s:
        try:
            yield _Span(s)
        except BaseException as e:
            s.set_attribute("error_type", type(e).__name__)
            s.set_status(Status(StatusCode.ERROR, type(e).__name__))
            raise


def current_context():
    """Capture on the submitting thread; pass to `attached` in the pool thread so worker spans keep their parent."""
    if _state["tracer"] is None:
        return None
    from opentelemetry import context

    return context.get_current()


@contextmanager
def attached(ctx) -> Iterator[None]:
    if ctx is None:
        yield
        return
    from opentelemetry import context

    token = context.attach(ctx)
    try:
        yield
    finally:
        context.detach(token)


def count(name: str, n: int = 1, **attrs) -> None:
    inst = _state["instruments"].get(name)
    if inst is not None:
        inst.add(n, clean(attrs))


def observe(name: str, value: float, **attrs) -> None:
    inst = _state["instruments"].get(name)
    if inst is not None:
        inst.record(value, clean(attrs))


def traced(name: str):
    """Decorator: run the (sync or async) function inside a span `name` (a root span when nothing is active)."""
    import functools
    import inspect

    def wrap(fn):
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def aw(*a, **kw):
                with span(name):
                    return await fn(*a, **kw)

            return aw

        @functools.wraps(fn)
        def w(*a, **kw):
            with span(name):
                return fn(*a, **kw)

        return w

    return wrap


_WARN_KINDS = {"refused", "injection_flagged"}
_OK_STATUS = {"ok", "completed", "skipped", "cached", "valid"}
_BODY_KEYS = ("refusal", "tool", "server", "slot_id", "status", "outcome", "sender", "receiver", "reason", "decision",
              "escalation_kind", "grounded", "abstained", "ok", "duration_ms", "latency_ms", "model", "model_calls",
              "job", "invalid_reason")


def activity(entry: Dict[str, Any]) -> None:
    """Mirror one Activity-log entry as an OTel log record + counters (same code-built fields, cleaned). The body is
    built from the cleaned attributes only (e.g. "refused refusal=trade"), so it never carries more than they do."""
    logger = _state["logger"]
    if logger is None:
        return
    from opentelemetry._logs import LogRecord, SeverityNumber

    kind = str(entry.get("kind") or ("tool_call" if entry.get("tool") else "event"))
    attrs = clean({**entry, "kind": kind})
    bad = (kind in _WARN_KINDS or entry.get("ok") is False or entry.get("outcome") not in (None, "delivered")
           or str(entry.get("status") or "ok") not in _OK_STATUS)
    body = " ".join([kind] + [f"{k}={attrs[k]}" for k in _BODY_KEYS if k in attrs])
    logger.emit(LogRecord(body=body, attributes=attrs, severity_number=SeverityNumber.WARN if bad else
                          SeverityNumber.INFO, severity_text="WARN" if bad else "INFO"))
    count("personal_ai.activity", kind=kind)
    if kind == "slot_run":
        count("personal_ai.slot.runs", slot_id=entry.get("slot_id"), status=entry.get("status"))
    if kind == "injection_flagged":
        count("personal_ai.injection_flags")
    elif kind == "bus":
        count("personal_ai.bus.messages", outcome=entry.get("outcome"))
    elif kind == "rag_turn":
        count(
            "personal_ai.rag.turns",
            grounded=entry.get("grounded"),
            abstained=entry.get("abstained"),
        )
    elif kind == "review":
        count("personal_ai.reviews", decision=entry.get("decision"))
    elif kind == "refused":
        count("personal_ai.refusals", refusal=entry.get("refusal"))
