"""Workers: adapt the real services (vault records, forecaster, market tools) to Finding streams.

Numbers come from deterministic code; models only interpret. Every worker degrades to an
explicit `unavailable`/`uncertain` Finding rather than raising or guessing.
"""

import asyncio
import calendar
import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.config import EMAIL_DIGEST_STEP_TIMEOUT_SECONDS, FORECAST_STEP_TIMEOUT_SECONDS
from src.forecaster.models import ResearchRequest
from .bus import MessageBus
from .coordinator import Worker, check_cancel
from .schemas import Finding, Plan, PlanStep

BILL_WORDS = re.compile(
    r"\b(bill|bills|expense|expenses|spend|spending|statement|budget|utilit\w*|cash ?flow)\b",
    re.I,
)
MARKET_WORDS = re.compile(
    r"\b(stock|stocks|market|forecast|trading|ticker|invest\w*|watch)\b", re.I
)
EMAIL_WORDS = re.compile(r"\b(emails?|inbox|gmail)\b", re.I)
HOLDINGS_WORDS = re.compile(r"\b(holdings|portfolio|positions|brokerage)\b", re.I)
CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")
CAPS = re.compile(r"\b([A-Z]{2,5})\b")
# An unknown ALL-CAPS word counts as a ticker only right after a ticker-introducing word ("on QCOM", "vs NVDA").
INTRO = re.compile(
    r"\b(?:on|for|about|of|vs|versus|and|ticker|stock|symbol|research|forecast|watch|analy[sz]e|with)\s+([A-Z]{2,5})\b"
)
NOT_TICKERS = {
    "AI",
    "USD",
    "ETF",
    "PII",
    "RSI",
    "MACD",
    "CASH",
    "THE",
    "AND",
    "FOR",
    "TODAY",
    "ALL",
    "ANY",
    "MY",
    "IRA",
    "APR",
    "APY",
}
CATEGORY_WORDS = {
    "electricity": r"electric\w*|power",
    "gas": r"gas",
    "mobile": r"mobile|phone|cell\w*",
    "credit_card": r"credit[ -]?cards?",
}
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
    hit = [
        c
        for c, pat in CATEGORY_WORDS.items()
        if re.search(rf"\b(?:{pat})\b", query, re.I)
    ]
    return hit[0] if len(hit) == 1 else None


def _account(record: Dict[str, Any]) -> Optional[str]:
    """The record's account label (user-entered nickname / last digits), normalised; None when unlabelled."""
    label = (record.get("metadata") or {}).get("account_label") or (
        record.get("extraction") or {}
    ).get("account_identifier")
    return _label(label).lower() if label else None


def account_labels(
    records: Sequence[Dict[str, Any]], category: Optional[str]
) -> List[str]:
    """Distinct account labels among records of one category (sorted, normalised)."""
    return sorted(
        {
            a
            for r in records
            if (r.get("metadata") or {}).get("category") == category
            for a in [_account(r)]
            if a
        }
    )


def named_account(
    query: str, records: Sequence[Dict[str, Any]], category: Optional[str]
) -> Optional[str]:
    """The one account label the question names (whole label, or its trailing 4+ digits), else None."""
    q = query.lower()
    hits = [
        a
        for a in account_labels(records, category)
        if a in q
        or ((m := re.search(r"(\d{4,})$", a)) and re.search(rf"\b{m.group(1)}\b", q))
    ]
    return hits[0] if len(hits) == 1 else None


PERIOD_WORDS = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|q[1-4]|quarters?|quarterly|20\d\d|this|last|latest|recent\w*|current|"
    r"previous|past|months?|monthly|weeks?|years?|today|ytd)\b",
    re.I,
)
AMOUNT_WORDS = re.compile(
    r"\b(?:how much|what (?:was|is|were|are) my|total|amount|owe|due|cost)\b", re.I
)
CLARIFY = {  # fixed templates (S16): only counts and allowlisted labels are interpolated
    "period": 'Which period do you mean? Name a month or quarter, e.g. "September 2026" or "Q3 2026".',
    "account": "Which account do you mean? You have {n} {category} accounts on file: {labels}. "
    "Name one (its nickname or last 4 digits).",
    "ticker": 'Which ticker do you mean? Name it, e.g. "$NVDA" or "research AAPL".',
}


def ambiguity(query: str, records: Sequence[Dict[str, Any]]) -> Optional[str]:
    """P3: `period` | `account` | `ticker` when the question cannot be answered without guessing, else None.
    Pure and rule-based; at most one type per turn (checked in this order)."""
    bills = bool(BILL_WORDS.search(query))
    if bills and AMOUNT_WORDS.search(query) and not PERIOD_WORDS.search(query):
        return "period"
    category = _category(query)
    if (
        bills
        and category
        and len(account_labels(records, category)) >= 2
        and not named_account(query, records, category)
    ):
        return "account"
    if (
        MARKET_WORDS.search(query)
        and not _tickers(query)
        and not bills
        and not HOLDINGS_WORDS.search(query)
    ):
        return "ticker"
    return None


def clarify_question(kind: str, query: str, records: Sequence[Dict[str, Any]]) -> str:
    if kind != "account":
        return CLARIFY[kind]
    category = _category(query)
    labels = account_labels(records, category)
    return CLARIFY["account"].format(
        n=len(labels), category=category.replace("_", " "), labels=", ".join(labels[:6])
    )


def plan_for(query: str, max_symbols: int = 3) -> Plan:
    """Rule-based planner: deterministic and auditable. Market work depends on expense work
    when both are asked, so the budget constraint flows through the bus."""
    symbols = _tickers(query)[:max_symbols]
    wants_bills = bool(BILL_WORDS.search(query))
    wants_holdings = bool(HOLDINGS_WORDS.search(query))
    wants_market = bool(symbols) or bool(MARKET_WORDS.search(query)) or wants_holdings
    steps: List[PlanStep] = []
    if wants_bills:
        steps.append(
            PlanStep(
                id="expense",
                agent="expense",
                goal="Quarterly bill totals and cash-flow delta",
                category=_category(query),
            )
        )
    if EMAIL_WORDS.search(query):
        steps.append(
            PlanStep(id="email", agent="email", goal="Read-only digest of recent email",
                     timeout_s=EMAIL_DIGEST_STEP_TIMEOUT_SECONDS)
        )
    if wants_holdings:
        steps.append(
            PlanStep(id="brokerage", agent="brokerage", goal="Read-only holdings")
        )
    deps = tuple(
        d
        for d, on in (("expense", wants_bills), ("brokerage", wants_holdings))
        if on and wants_market
    )
    for sym in symbols:
        steps.append(
            PlanStep(
                id=f"forecast_{sym}",
                agent="forecaster",
                goal=f"Research {sym}",
                symbol=sym,
                timeout_s=FORECAST_STEP_TIMEOUT_SECONDS,
            )
        )
    if wants_market:
        steps.append(
            PlanStep(
                id="market",
                agent="market",
                goal="Technical lean and constraints",
                depends_on=deps,
                symbol=symbols[0] if symbols else None,
            )
        )
    if not steps:
        raise ValueError(
            "Nothing to delegate: ask about bills/records or name a ticker."
        )
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
        end = date.fromisoformat(
            str(ex.get("period_end") or ex.get("period_start"))[:10]
        )
        start = date.fromisoformat(
            str(ex.get("period_start") or ex.get("period_end"))[:10]
        )
    except ValueError:
        return set()
    out, (y, m) = set(), (start.year, start.month)
    while (y, m) <= (end.year, end.month) and len(out) < 24:
        out.add((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _baselines(get_memory: Optional[Callable[[], Sequence[Any]]]) -> List[Finding]:
    """P5: approved, trusted Core Memory cash-flow facts as an optional baseline (never sent on a bus edge, S18)."""
    out: List[Finding] = []
    for fact in (get_memory() if get_memory else ()):
        if fact.trust != "trusted" or not fact.key.startswith(("cashflow_delta:", "budget_baseline:")):
            continue
        try:
            value = round(float(str(fact.value).replace("$", "").replace(",", "")), 2)
        except ValueError:
            continue
        period = fact.key.split(":", 1)[1][:20]
        out.append(Finding(agent="expense", claim_key=f"budget_baseline:{period}", value=value,
                           summary="Approved baseline from your Core Memory", evidence_ids=(f"memory:{fact.key[:60]}",),
                           confidence=0.9))
    return out


def expense_worker(get_records: Callable[[], Sequence[Dict[str, Any]]],
                   get_overdue: Optional[Callable[[], Sequence[Dict[str, Any]]]] = None,
                   get_memory: Optional[Callable[[], Sequence[Any]]] = None) -> Worker:
    """Deterministic aggregation over structured vault records (no LLM arithmetic).

    Filters by the step's category, skips superseded documents, counts each (provider, period) once,
    and marks the cash-flow delta `uncertain` when records conflict, categories are mixed, or a
    quarter is only partly covered."""

    def run(step: PlanStep, inputs) -> List[Finding]:
        all_records = [
            r
            for r in get_records()
            if (r.get("extraction") or {}).get("total") is not None
        ]
        corrected = {
            (
                _label(
                    (r.get("metadata") or {}).get("provider")
                    or r["extraction"].get("provider")
                    or "unknown"
                ),
                r["extraction"].get("period_end"),
            )
            for r in all_records
            if (r.get("metadata") or {}).get("superseded")
            or r["extraction"].get("superseded_by")
        }
        records = [
            r
            for r in all_records
            if not (r.get("metadata") or {}).get("superseded")
            and not r["extraction"].get("superseded_by")
        ]
        if step.category:  # uncategorised records can't be told apart, so they stay in
            records = [
                r
                for r in records
                if (r.get("metadata") or {}).get("category") in (None, step.category)
            ]
        if getattr(step, "account", None):  # the question named one account: only its records (unlabelled ones can't be told apart)
            records = [
                r for r in records if _account(r) in (None, step.account.lower())
            ]
        missing = _missing(
            get_overdue() if get_overdue else (), all_records, step.category
        )
        if not records:
            return [Finding(agent="expense", claim_key="records", summary="No structured statements found.",
                            confidence=0, status="unavailable")] + missing + _baselines(get_memory)
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
            provider = _label(
                r.get("metadata", {}).get("provider") or ex.get("provider") or "unknown"
            )
            ident = (provider, ex.get("period_end") or q)
            total = round(float(ex["total"]), 2)
            totals.setdefault(ident, set()).add(total)
            for y, m in _months(ex):
                covered.setdefault((provider, f"{y}-Q{(m - 1) // 3 + 1}"), set()).add(
                    (y, m)
                )
            if ident in first:
                continue  # duplicate re-upload or conflicting copy: counted once (conflict is reported below)
            first[ident] = total
            per_quarter[q] = per_quarter.get(q, 0.0) + total
            out.append(
                Finding(
                    agent="expense",
                    claim_key=f"bill:{provider}:{ex.get('period_end') or q}",
                    value=total,
                    summary=f"{provider} statement total",
                    evidence_ids=(r["storage_key"],),
                    confidence=0.9,
                )
            )
        for (provider, end), t in totals.items():
            if (
                len(t) > 1
            ):  # surface the disagreement through merge: one finding per distinct value
                for v in sorted(t - {first[(provider, end)]}):
                    out.append(
                        Finding(
                            agent="expense",
                            claim_key=f"bill:{provider}:{end}",
                            value=v,
                            summary=f"{provider} conflicting statement total",
                            evidence_ids=(f"conflict:{provider}:{end}",),
                            confidence=0.9,
                        )
                    )
        qs = sorted(per_quarter)
        # Single-source caveat (W6): a corrected bill now rests on one document (the replacement); say so.
        for provider, end in sorted(k for k in first if k in corrected):
            out.append(
                Finding(
                    agent="expense",
                    claim_key=f"single_source:bill:{provider}:{end}",
                    status="uncertain",
                    summary=f"{provider} bill for {end} was corrected; one document backs the figure",
                    confidence=0.5,
                )
            )
        providers = {p for p, _ in covered}
        conflicted = {_quarter(end) for (_, end), t in totals.items() if len(t) > 1}
        partial = [
            q
            for q in qs[-2:]
            if any(
                len(covered.get((p, q), ())) < 3 for p in providers if (p, q) in covered
            )
        ]
        if len(qs) >= 2:
            delta = round((per_quarter[qs[-1]] - per_quarter[qs[-2]]) / 3, 2)
            notes = [
                n
                for n, bad in (
                    ("conflicting records", conflicted & set(qs[-2:])),
                    ("partial quarter " + ",".join(partial), partial),
                    (
                        "missing statement "
                        + ",".join(
                            sorted(
                                {
                                    f.claim_key.split(":")[1]
                                    for f in missing
                                    if _quarter(f.claim_key.split(":")[2]) in qs[-2:]
                                }
                            )
                        ),
                        any(
                            _quarter(f.claim_key.split(":")[2]) in qs[-2:]
                            for f in missing
                        ),
                    ),
                    (
                        "mixed categories; ask per category",
                        len(cats) > 1 and not step.category,
                    ),
                )
                if bad
            ]
            out.append(
                Finding(
                    agent="expense",
                    claim_key=f"cashflow_delta:{qs[-1]}",
                    value=delta,
                    summary=f"Monthly cash-flow change {qs[-2]}→{qs[-1]}"
                    + (f" ({'; '.join(notes)})" if notes else ""),
                    evidence_ids=tuple(f"quarter:{q}" for q in qs[-2:]),
                    confidence=0.85,
                    status="uncertain" if notes else "ok",
                )
            )
        else:
            out.append(Finding(agent="expense", claim_key="cashflow_delta", summary="Need two quarters of statements.",
                               confidence=0, status="uncertain"))
        return out + missing + _baselines(get_memory)

    return run


def _next_period_end(last: date, cadence: str) -> date:
    months = 3 if cadence == "quarterly" else 1
    y, m = (
        last.year + (last.month - 1 + months) // 12,
        (last.month - 1 + months) % 12 + 1,
    )
    return date(y, m, calendar.monthrange(y, m)[1])


def _missing(
    overdue: Sequence[Dict[str, Any]],
    records: Sequence[Dict[str, Any]],
    category: Optional[str],
) -> List[Finding]:
    """P12: each overdue ledger entry -> `missing:<provider>:<period_end>` (unavailable, no value). Never a guess.
    A provider whose records belong to another category than the one asked is skipped."""
    cats: Dict[str, set] = {}
    for r in records:
        cats.setdefault(
            _label((r.get("metadata") or {}).get("provider") or "unknown"), set()
        ).add((r.get("metadata") or {}).get("category"))
    out = []
    for o in overdue:
        prov = _label(o.get("provider") or "unknown")
        if category and cats.get(prov, set()) - {None} and category not in cats[prov]:
            continue
        try:
            end = _next_period_end(
                date.fromisoformat(str(o["last_period_end"])[:10]),
                o.get("cadence") or "monthly",
            ).isoformat()
        except (KeyError, TypeError, ValueError):
            end = "unknown"
        out.append(
            Finding(
                agent="expense",
                claim_key=f"missing:{prov}:{end}",
                status="unavailable",
                confidence=0,
                summary=f"No {prov} statement on file for the period ending {end}; the ledger expects one",
            )
        )
    return out


def trader_worker(providers) -> Worker:
    """Trader plan as templated findings (S16, S21): numbers and fixed phrases only, no model or feed text."""
    from src.trader.pipeline import analyze

    def run(step: PlanStep, inputs) -> List[Finding]:
        sym = step.symbol
        check_cancel()
        plan = analyze(sym, asyncio.run(providers.intraday(sym)))
        key = f"trader:{sym}"
        if plan.state != "triggered" or plan.qty is None:
            return [
                Finding(
                    agent="trader", claim_key=f"{key}:entry", value=plan.state, confidence=0.5,
                    status="uncertain" if plan.state != "no_data" else "unavailable",
                    summary=f"Opening-range breakout: {plan.state}."
                    + (f" Range {plan.or_low:.2f}-{plan.or_high:.2f}." if plan.or_high else ""),
                )
            ]
        o = plan.odds or {}
        return [
            Finding(
                agent="trader", claim_key=f"{key}:plan", value=plan.verdict, confidence=0.5,
                summary=f"Plan: entry {plan.entry:.2f}, stop {plan.stop:.2f}, target {plan.target:.2f}, {plan.qty} shares "
                f"(sized by {plan.sized_by}), max loss ${plan.max_loss:.2f}, reward:risk {plan.reward_risk:.2f}. Risk check {plan.verdict}.",
            ),
            Finding(
                agent="trader", claim_key=f"{key}:odds", value=plan.lean, confidence=0.4,
                status="ok" if o else "unavailable",
                summary=(f"Simulation ({o['draws']} draws, seed {o['seed']}): target first {o['target_first']:.0%}, stop first "
                         f"{o['stop_first']:.0%}, neither {o['neither']:.0%}. A simulation, not a prediction.") if o else "No odds: too little history.",
            ),
        ]

    return run


def forecaster_worker(research, swarm=None) -> Worker:
    """ResearchPredictionService -> heuristic outlooks, then a Tree-of-Thought thesis search that calls the
    specialist swarm only when depth 1 is not decisive (docs/forecaster.md Part 2)."""
    from src.forecaster.tot import search

    def run(step: PlanStep, inputs) -> List[Finding]:
        sym = step.symbol
        check_cancel()
        prediction = asyncio.run(research.research(ResearchRequest(symbol=sym)))
        ev = tuple(c.evidence_id for c in prediction.snapshot.evidence)[:12]
        if prediction.status in ("abstained", "unsupported") or not prediction.horizons:
            return [
                Finding(
                    agent="forecaster",
                    claim_key=f"outlook:{sym}",
                    evidence_ids=ev,
                    confidence=0,
                    status="unavailable" if not ev else "uncertain",
                    summary=f"No supportable forecast ({prediction.status}).",
                )
            ]
        out = [
            Finding(
                agent="forecaster",
                claim_key=f"outlook:{sym}:{h.horizon_sessions}s",
                value=h.outlook,
                summary="; ".join(h.reasoning)[:400] or "heuristic outlook",
                evidence_ids=ev,
                confidence=h.coverage,
                as_of=prediction.snapshot.as_of,
                status="ok" if prediction.status == "completed" else "uncertain",
            )
            for h in prediction.horizons
        ]
        reviews = []

        def critique():
            check_cancel()
            reviews.append(asyncio.run(swarm.analyze(prediction.snapshot)))
            return reviews[-1]

        tree = search(prediction.snapshot, critique if swarm is not None else None)
        if reviews:
            report = reviews[-1]
            out.append(
                Finding(
                    agent="forecaster",
                    claim_key=f"specialists:{sym}",
                    value=report.verdict,
                    summary=f"{len(report.specialists)} specialists; disagreement={report.disagreement_score}",
                    evidence_ids=ev,
                    confidence=report.coverage,
                    status="ok"
                    if report.verdict in ("supports", "contradicts")
                    else "uncertain",
                )
            )
        out.append(_thesis_finding(sym, tree, prediction, ev))
        families = {c.source_type for c in prediction.snapshot.evidence}
        if (
            len(families) < 2
        ):  # W6 single-source caveat: the outlook rests on one evidence family
            out.append(
                Finding(
                    agent="forecaster",
                    claim_key=f"single_source:outlook:{sym}",
                    status="uncertain",
                    summary=f"{sym} outlook rests on {len(families)} evidence family",
                    evidence_ids=ev,
                    confidence=0.5,
                )
            )
        return out

    return run


def _thesis_finding(sym: str, tree, prediction, ev) -> Finding:
    """Templated summary only: scenario/driver are closed enums, numbers are computed here."""
    from src import config

    pick = tree.node(tree.selected_id) if tree.selected_id else None
    runner = tree.node(tree.runner_up_id) if tree.runner_up_id else None
    pruned = sum(n.status == "pruned" for n in tree.nodes)
    if pick is None:
        return Finding(
            agent="forecaster",
            claim_key=f"thesis:{sym}",
            confidence=0,
            evidence_ids=ev,
            status="uncertain" if tree.nodes else "unavailable",
            summary=f"Thesis search abstained ({tree.termination}); {len(tree.nodes)} branches, {pruned} pruned.",
        )
    heuristic = next(
        (h.outlook for h in prediction.horizons if h.horizon_sessions == 21), None
    )
    clash = {heuristic, pick.thought.scenario} == {"bullish", "bearish"}
    critic_missing = pick.depth == 2 and "critic" not in pick.components
    close = tree.margin is not None and tree.margin < config.TOT_UNCERTAIN_MARGIN
    summary = (
        f"{pick.thought.scenario} via {pick.thought.driver} (score {pick.score:.2f}, depth {pick.depth}, "
        f"{tree.termination}); runner-up "
        + (f"{runner.thought.scenario} {runner.score:.2f}" if runner else "none")
        + f"; {pruned} of {len(tree.nodes)} branches pruned"
        + ("; disagrees with heuristic outlook" if clash else "")
        + ("; specialists unavailable" if critic_missing else "")
    )
    return Finding(
        agent="forecaster",
        claim_key=f"thesis:{sym}",
        value=pick.thought.scenario,
        summary=summary,
        evidence_ids=pick.thought.evidence_ids,
        confidence=pick.score,
        as_of=prediction.snapshot.as_of,
        status="uncertain" if clash or close or critic_missing else "ok",
    )


def _lean(providers, symbol: str) -> List[Finding]:
    from src.forecaster.recommend import research_lean
    from src.forecaster.technical import technical_snapshot

    check_cancel()
    result = asyncio.run(providers.market(ResearchRequest(symbol=symbol)))
    if result.value is None:
        return [
            Finding(
                agent="market",
                claim_key=f"lean:{symbol}",
                confidence=0,
                status="unavailable",
                summary="Live market data unavailable; no lean produced.",
            )
        ]
    lean = research_lean(technical_snapshot(result.value))
    return [
        Finding(
            agent="market",
            claim_key=f"lean:{symbol}",
            value=lean.lean,
            summary=lean.reasoning[:400]
            if isinstance(lean.reasoning, str)
            else "; ".join(lean.reasoning)[:400],
            evidence_ids=tuple(c.evidence_id for c in result.value.evidence)[:6],
            confidence=lean.confidence,
            status="ok" if lean.status == "completed" else "uncertain",
            # data age = last bar's session close, not fetch time
            as_of=datetime.combine(
                result.value.bars[-1].session + timedelta(days=1),
                dtime.min,
                tzinfo=timezone.utc,
            ),
        )
    ]


def market_worker(providers, bus: MessageBus) -> Worker:
    """Technical lean for the symbol, plus the expense budget constraint — received only via the bus."""

    def run(step: PlanStep, inputs) -> List[Finding]:
        out: List[Finding] = []
        for f in (
            f
            for fs in inputs.values()
            for f in fs
            if f.claim_key.startswith("cashflow_delta:")
            and f.value is not None
            and f.status in ("ok", "uncertain")
        ):
            period = f.claim_key.split(":", 1)[1]
            msg = bus.send(
                "expense",
                "market",
                {
                    "period": period,
                    "monthly_cashflow_delta": f.value,
                    "direction": "up"
                    if f.value > 0
                    else "down"
                    if f.value < 0
                    else "flat",
                },
            )
            out.append(
                Finding(
                    agent="market",
                    claim_key=f"budget_constraint:{msg.period}",
                    value=msg.direction,
                    summary=f"Monthly spend moved {msg.direction} by {abs(msg.monthly_cashflow_delta):.2f}",
                    evidence_ids=f.evidence_ids,
                    status=f.status,
                    confidence=f.confidence,
                    trust=f.trust,
                )
            )
        held = next(
            (
                f
                for fs in inputs.values()
                for f in fs
                if f.claim_key == "holdings" and f.value
            ),
            None,
        )
        if (
            held is not None
        ):  # holdings reach the market agent only through the typed bus edge
            msg = bus.send(
                "coordinator",
                "market",
                {"symbols": [x for x in str(held.value).split(",") if x]},
            )
            out.append(
                Finding(
                    agent="market",
                    claim_key="watchlist",
                    value=",".join(msg.symbols),
                    summary=f"{len(msg.symbols)} holdings to review",
                    evidence_ids=held.evidence_ids,
                    confidence=held.confidence,
                    status=held.status,
                    trust=held.trust,
                )
            )
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
        val = (
            "UNRESOLVED"
            if f.status == "unresolved"
            else ("n/a" if f.value is None else _md(f.value))
        )
        ev = (
            f" [evidence: {_md(', '.join(f.evidence_ids[:3]))}]"
            if f.evidence_ids
            else ""
        )
        tag = (
            f" ({f.status}{', untrusted-derived' if f.trust == 'untrusted' else ''})"
            if f.status != "ok" or f.trust == "untrusted"
            else ""
        )
        lines.append(f"- **{_md(key)}**: {val}{tag} — {_md(f.summary)}{ev}")
    for c in report.conflicts:
        lines.append(
            f"- ⚠ Conflict on `{_md(c.claim_key)}`: "
            + "; ".join(f"{_md(a)} says {_md(v)}" for a, v, _ in c.options)
        )
    if report.escalations:
        lines.append("\n**Needs your decision:**")
        lines += [f"- {e.kind}: {_md(e.detail)}" for e in report.escalations]
    facts = getattr(report, "memory", None) or []
    if facts:  # P5: separate section; approved by you, but still labelled when derived from untrusted content
        lines.append("\n**From your approved memory:**")
        lines += [f"- {_md(m.key)}: {_md(m.value)}" + (" (untrusted-derived)" if m.trust == "untrusted" else "")
                  for m in facts]
    lines.append("\n_Research support only; not financial advice._")
    return "\n".join(lines)


def llm_plan(query: str, client, agents) -> Plan:
    """Optional model planner. The model only proposes a Plan; schema (closed fields, <=8 steps) and
    `validate_plan` (allowlisted agents, no cycles) decide whether it is used. Any failure raises, and the
    caller falls back to the rule-based `plan_for`."""
    import json
    from .coordinator import validate_plan

    reply = client.chat(
        messages=[
            {
                "role": "system",
                "content": "Plan which specialist agents answer the user's request. Agents: "
                + ", ".join(sorted(agents))
                + ". Use only these. Symbols are US tickers. Output only the JSON schema.",
            },
            {"role": "user", "content": query[:500]},
        ],
        format=Plan.model_json_schema(),
    )
    plan = Plan.model_validate(json.loads(reply["message"]["content"]))
    validate_plan(plan, tuple(agents))
    # Budgets come from code, never from the model.
    default = PlanStep.model_fields["timeout_s"].default
    return Plan(
        steps=tuple(
            s.model_copy(
                update={
                    "timeout_s": FORECAST_STEP_TIMEOUT_SECONDS
                    if s.agent == "forecaster"
                    else default,
                    "account": None,  # code sets the account from the user's own labels, never the model
                }
            )
            for s in plan.steps
        )
    )
