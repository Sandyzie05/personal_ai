"""Workers: adapt the real services (vault records, forecaster, market tools) to Finding streams.

Numbers come from deterministic code; models only interpret. Every worker degrades to an
explicit `unavailable`/`uncertain` Finding rather than raising or guessing.
"""

import asyncio
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.forecaster.models import ResearchRequest
from .bus import MessageBus
from .coordinator import Worker, check_cancel
from .schemas import Finding, Plan, PlanStep

BILL_WORDS = re.compile(r"\b(bill|bills|expense|expenses|spend|spending|statement|budget|utilit\w*|cash ?flow)\b", re.I)
MARKET_WORDS = re.compile(r"\b(stock|stocks|market|forecast|trading|ticker|invest\w*|watch)\b", re.I)
EMAIL_WORDS = re.compile(r"\b(emails?|inbox|gmail)\b", re.I)
HOLDINGS_WORDS = re.compile(r"\b(holdings|portfolio|positions|brokerage)\b", re.I)
CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
CAPS = re.compile(r"\b([A-Z]{2,5})\b")
# An unknown ALL-CAPS word counts as a ticker only right after a ticker-introducing word ("on QCOM", "vs NVDA").
INTRO = re.compile(r"\b(?:on|for|about|of|vs|versus|and|ticker|stock|symbol|research|forecast|watch|analy[sz]e|with)\s+([A-Z]{2,5})\b")
NOT_TICKERS = {"AI", "USD", "ETF", "PII", "RSI", "MACD", "CASH", "THE", "AND", "FOR", "TODAY", "ALL", "ANY", "MY", "IRA", "APR", "APY"}
CATEGORY_WORDS = {"electricity": r"electric\w*|power", "gas": r"gas", "mobile": r"mobile|phone|cell\w*",
                  "credit_card": r"credit[ -]?cards?"}
LABEL_OK = re.compile(r"[^A-Za-z0-9 .&'-]")


def _tickers(query: str) -> List[str]:
    """Explicit $TICK, a bundled-universe symbol, or a caps word introduced like a ticker. Bare caps words (CASH, AI) are not."""
    from src.forecaster.screens import SP500_TOP50

    known = set(SP500_TOP50)
    found = CASHTAG.findall(query) + [t for t in CAPS.findall(query) if t in known]
    found += [t for t in INTRO.findall(query) if t not in NOT_TICKERS]
    return list(dict.fromkeys(found))


def _label(text: str, n: int = 30) -> str:
    return LABEL_OK.sub("", str(text))[:n].strip() or "unknown"


def _md(text: Any) -> str:
    """Escape markdown/HTML so untrusted-derived text cannot render links, images or formatting."""
    return re.sub(r"([\\`*_{}\[\]()#+!<>|~$-])", r"\\\1", str(text))


def _category(query: str) -> Optional[str]:
    """One category named in the question -> filter the expense step to it; none/several -> no filter."""
    hit = [c for c, pat in CATEGORY_WORDS.items() if re.search(rf"\b(?:{pat})\b", query, re.I)]
    return hit[0] if len(hit) == 1 else None


def plan_for(query: str, max_symbols: int = 3) -> Plan:
    """Rule-based planner: deterministic and auditable. Market work depends on expense work
    when both are asked, so the budget constraint flows through the bus."""
    symbols = _tickers(query)[:max_symbols]
    wants_bills = bool(BILL_WORDS.search(query))
    wants_holdings = bool(HOLDINGS_WORDS.search(query))
    wants_market = bool(symbols) or bool(MARKET_WORDS.search(query)) or wants_holdings
    steps: List[PlanStep] = []
    if wants_bills:
        steps.append(PlanStep(id="expense", agent="expense", goal="Quarterly bill totals and cash-flow delta",
                              category=_category(query)))
    if EMAIL_WORDS.search(query):
        steps.append(PlanStep(id="email", agent="email", goal="Read-only digest of recent email"))
    if wants_holdings:
        steps.append(PlanStep(id="brokerage", agent="brokerage", goal="Read-only holdings"))
    deps = tuple(d for d, on in (("expense", wants_bills), ("brokerage", wants_holdings)) if on and wants_market)
    for sym in symbols:
        steps.append(PlanStep(id=f"forecast_{sym}", agent="forecaster", goal=f"Research {sym}", symbol=sym))
    if wants_market:
        steps.append(PlanStep(id="market", agent="market", goal="Technical lean and constraints",
                              depends_on=deps, symbol=symbols[0] if symbols else None))
    if not steps:
        raise ValueError("Nothing to delegate: ask about bills/records or name a ticker.")
    return Plan(steps=tuple(steps))


def _quarter(iso: str) -> Optional[str]:
    try:
        d = date.fromisoformat(iso[:10])
    except (TypeError, ValueError):
        return None
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _months(ex: Dict[str, Any]) -> set:
    """(year, month) pairs a statement covers: its whole span when period_start is known, else its end month."""
    try:
        end = date.fromisoformat(str(ex.get("period_end") or ex.get("period_start"))[:10])
        start = date.fromisoformat(str(ex.get("period_start") or ex.get("period_end"))[:10])
    except ValueError:
        return set()
    out, (y, m) = set(), (start.year, start.month)
    while (y, m) <= (end.year, end.month) and len(out) < 24:
        out.add((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def expense_worker(get_records: Callable[[], Sequence[Dict[str, Any]]]) -> Worker:
    """Deterministic aggregation over structured vault records (no LLM arithmetic).

    Filters by the step's category, skips superseded documents, counts each (provider, period) once,
    and marks the cash-flow delta `uncertain` when records conflict, categories are mixed, or a
    quarter is only partly covered."""
    def run(step: PlanStep, inputs) -> List[Finding]:
        records = [r for r in get_records() if (r.get("extraction") or {}).get("total") is not None
                   and not (r.get("metadata") or {}).get("superseded") and not r["extraction"].get("superseded_by")]
        if step.category:  # uncategorised records can't be told apart, so they stay in
            records = [r for r in records if (r.get("metadata") or {}).get("category") in (None, step.category)]
        if not records:
            return [Finding(agent="expense", claim_key="records", summary="No structured statements found.",
                            confidence=0, status="unavailable")]
        cats = {(r.get("metadata") or {}).get("category") for r in records} - {None}
        out: List[Finding] = []
        per_quarter: Dict[str, float] = {}
        covered: Dict[Tuple[str, str], set] = {}  # (provider, quarter) -> months seen
        first: Dict[Tuple[str, str], float] = {}
        totals: Dict[Tuple[str, str], set] = {}
        for r in records:
            ex = r["extraction"]
            q = _quarter(ex.get("period_end") or ex.get("period_start") or "")
            if q is None:
                continue
            provider = _label(r.get("metadata", {}).get("provider") or ex.get("provider") or "unknown")
            ident = (provider, ex.get("period_end") or q)
            total = round(float(ex["total"]), 2)
            totals.setdefault(ident, set()).add(total)
            for y, m in _months(ex):
                covered.setdefault((provider, f"{y}-Q{(m - 1) // 3 + 1}"), set()).add((y, m))
            if ident in first:
                continue  # duplicate re-upload or conflicting copy: counted once (conflict is reported below)
            first[ident] = total
            per_quarter[q] = per_quarter.get(q, 0.0) + total
            out.append(Finding(agent="expense", claim_key=f"bill:{provider}:{ex.get('period_end') or q}",
                               value=total, summary=f"{provider} statement total",
                               evidence_ids=(r["storage_key"],), confidence=0.9))
        for (provider, end), t in totals.items():
            if len(t) > 1:  # surface the disagreement through merge: one finding per distinct value
                for v in sorted(t - {first[(provider, end)]}):
                    out.append(Finding(agent="expense", claim_key=f"bill:{provider}:{end}", value=v,
                                       summary=f"{provider} conflicting statement total",
                                       evidence_ids=(f"conflict:{provider}:{end}",), confidence=0.9))
        qs = sorted(per_quarter)
        providers = {p for p, _ in covered}
        conflicted = {_quarter(end) for (_, end), t in totals.items() if len(t) > 1}
        partial = [q for q in qs[-2:] if any(len(covered.get((p, q), ())) < 3 for p in providers
                                              if (p, q) in covered)]
        if len(qs) >= 2:
            delta = round((per_quarter[qs[-1]] - per_quarter[qs[-2]]) / 3, 2)
            notes = [n for n, bad in (("conflicting records", conflicted & set(qs[-2:])),
                                      ("partial quarter " + ",".join(partial), partial),
                                      ("mixed categories; ask per category", len(cats) > 1 and not step.category)) if bad]
            out.append(Finding(agent="expense", claim_key=f"cashflow_delta:{qs[-1]}", value=delta,
                               summary=f"Monthly cash-flow change {qs[-2]}→{qs[-1]}" + (f" ({'; '.join(notes)})" if notes else ""),
                               evidence_ids=tuple(f"quarter:{q}" for q in qs[-2:]), confidence=0.85,
                               status="uncertain" if notes else "ok"))
        else:
            out.append(Finding(agent="expense", claim_key="cashflow_delta", summary="Need two quarters of statements.",
                               confidence=0, status="uncertain"))
        return out
    return run


def forecaster_worker(research, swarm=None) -> Worker:
    """ResearchPredictionService (+ optional ScenarioSwarmService) -> Findings."""
    def run(step: PlanStep, inputs) -> List[Finding]:
        sym = step.symbol
        check_cancel()
        prediction = asyncio.run(research.research(ResearchRequest(symbol=sym)))
        ev = tuple(c.evidence_id for c in prediction.snapshot.evidence)[:12]
        if prediction.status in ("abstained", "unsupported") or not prediction.horizons:
            return [Finding(agent="forecaster", claim_key=f"outlook:{sym}", evidence_ids=ev, confidence=0,
                            status="unavailable" if not ev else "uncertain",
                            summary=f"No supportable forecast ({prediction.status}).")]
        out = [Finding(agent="forecaster", claim_key=f"outlook:{sym}:{h.horizon_sessions}s", value=h.outlook,
                       summary="; ".join(h.reasoning)[:400] or "heuristic outlook", evidence_ids=ev,
                       confidence=h.coverage, as_of=prediction.snapshot.as_of,
                       status="ok" if prediction.status == "completed" else "uncertain") for h in prediction.horizons]
        if swarm is not None:
            check_cancel()
            report = asyncio.run(swarm.analyze(prediction.snapshot))
            out.append(Finding(agent="forecaster", claim_key=f"specialists:{sym}", value=report.verdict,
                               summary=f"{len(report.specialists)} specialists; disagreement={report.disagreement_score}",
                               evidence_ids=ev, confidence=report.coverage,
                               status="ok" if report.verdict in ("supports", "contradicts") else "uncertain"))
        return out
    return run


def _lean(providers, symbol: str) -> List[Finding]:
    from src.forecaster.recommend import research_lean
    from src.forecaster.technical import technical_snapshot

    check_cancel()
    result = asyncio.run(providers.market(ResearchRequest(symbol=symbol)))
    if result.value is None:
        return [Finding(agent="market", claim_key=f"lean:{symbol}", confidence=0, status="unavailable",
                        summary="Live market data unavailable; no lean produced.")]
    lean = research_lean(technical_snapshot(result.value))
    return [Finding(agent="market", claim_key=f"lean:{symbol}", value=lean.lean,
                    summary=lean.reasoning[:400] if isinstance(lean.reasoning, str) else "; ".join(lean.reasoning)[:400],
                    evidence_ids=tuple(c.evidence_id for c in result.value.evidence)[:6],
                    confidence=lean.confidence,
                    status="ok" if lean.status == "completed" else "uncertain",
                    # data age = last bar's session close, not fetch time
                    as_of=datetime.combine(result.value.bars[-1].session + timedelta(days=1), dtime.min,
                                           tzinfo=timezone.utc))]


def market_worker(providers, bus: MessageBus) -> Worker:
    """Technical lean for the symbol, plus the expense budget constraint — received only via the bus."""
    def run(step: PlanStep, inputs) -> List[Finding]:
        out: List[Finding] = []
        for f in (f for fs in inputs.values() for f in fs if f.claim_key.startswith("cashflow_delta:") and f.value is not None
                  and f.status in ("ok", "uncertain")):
            period = f.claim_key.split(":", 1)[1]
            msg = bus.send("expense", "market", {"period": period, "monthly_cashflow_delta": f.value,
                                                 "direction": "up" if f.value > 0 else "down" if f.value < 0 else "flat"})
            out.append(Finding(agent="market", claim_key=f"budget_constraint:{msg.period}", value=msg.direction,
                               summary=f"Monthly spend moved {msg.direction} by {abs(msg.monthly_cashflow_delta):.2f}",
                               evidence_ids=f.evidence_ids, status=f.status, confidence=f.confidence, trust=f.trust))
        held = next((f for fs in inputs.values() for f in fs if f.claim_key == "holdings" and f.value), None)
        if held is not None:  # holdings reach the market agent only through the typed bus edge
            msg = bus.send("coordinator", "market", {"symbols": [x for x in str(held.value).split(",") if x]})
            out.append(Finding(agent="market", claim_key="watchlist", value=",".join(msg.symbols),
                               summary=f"{len(msg.symbols)} holdings to review", evidence_ids=held.evidence_ids,
                               confidence=held.confidence, status=held.status, trust=held.trust))
            for sym in msg.symbols[:3]:
                out.extend(_lean(providers, sym))
        if step.symbol:
            check_cancel()
            out.extend(_lean(providers, step.symbol))
        return out
    return run


def render_report(report) -> str:
    """Deterministic, source-preserving summary: claims with evidence, conflicts, then what needs the user.
    Every interpolated value is markdown-escaped (claim keys/summaries can derive from untrusted documents)."""
    lines = []
    for key, f in sorted(report.claims.items()):
        val = "UNRESOLVED" if f.status == "unresolved" else ("n/a" if f.value is None else _md(f.value))
        ev = f" [evidence: {_md(', '.join(f.evidence_ids[:3]))}]" if f.evidence_ids else ""
        tag = f" ({f.status}{', untrusted-derived' if f.trust == 'untrusted' else ''})" if f.status != "ok" or f.trust == "untrusted" else ""
        lines.append(f"- **{_md(key)}**: {val}{tag} — {_md(f.summary)}{ev}")
    for c in report.conflicts:
        lines.append(f"- ⚠ Conflict on `{_md(c.claim_key)}`: " + "; ".join(f"{_md(a)} says {_md(v)}" for a, v, _ in c.options))
    if report.escalations:
        lines.append("\n**Needs your decision:**")
        lines += [f"- {e.kind}: {_md(e.detail)}" for e in report.escalations]
    lines.append("\n_Research support only; not financial advice._")
    return "\n".join(lines)


def llm_plan(query: str, client, agents) -> Plan:
    """Optional model planner. The model only proposes a Plan; schema (closed fields, <=8 steps) and
    `validate_plan` (allowlisted agents, no cycles) decide whether it is used. Any failure raises, and the
    caller falls back to the rule-based `plan_for`."""
    import json
    from .coordinator import validate_plan

    reply = client.chat(messages=[
        {"role": "system", "content": "Plan which specialist agents answer the user's request. Agents: "
         + ", ".join(sorted(agents)) + ". Use only these. Symbols are US tickers. Output only the JSON schema."},
        {"role": "user", "content": query[:500]}], format=Plan.model_json_schema())
    plan = Plan.model_validate(json.loads(reply["message"]["content"]))
    validate_plan(plan, tuple(agents))
    return plan
