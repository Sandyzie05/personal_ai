"""Track R: read-only Robinhood research through the user's Robinhood MCP server (docs/roadmap.md Track R).

Rules enforced here (R-S1..R-S6):
- R-S1 Code calls Robinhood with a fixed tool and code-built arguments (a ticker, ids/dates the broker returned). The model
  never builds arguments; critics stay tool-less.
- R-S2 Exact-name allowlists (`ROBINHOOD_RESEARCH_TOOLS`, `ROBINHOOD_IMPORT_TOOLS`, `ROBINHOOD_ACCOUNT_TOOLS`) on top of
  `ReadOnlyMcp`; banking/credit tools are never callable and write tools are refused by the sensitive-prefix check too.
- R-S3 Market data becomes EvidenceCards with code-written summaries built from numbers/dates only. Broker free text
  (company profile, filing prose, names) is dropped, so it can never reach a prompt.
- R-S4/R-S6 Account data (positions, portfolio, P&L) is returned only by `portfolio_fit` and never enters an
  EvidenceSnapshot. `account_number` may only be sent to `ROBINHOOD_ACCOUNT_TOOLS`; every other call is checked for it.
- R-S5 No writes: nothing here places, cancels, creates, updates, follows, declines or sets anything.

Parameter names are the ones documented in the Robinhood MCP tool catalog where it names them; the rest are assumed and
live in `PARAMS` so a live mismatch is a one-line fix. Response parsing is tolerant (several key spellings) and fails
closed to "unavailable".
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import Field

from src.agents.orchestration.ingest import ReadOnlyMcp
from src.observability import otel

from .models import (
    Contract,
    EvidenceCard,
    ProviderIssue,
    ProviderResult,
    canonical_symbol,
)

SERVER_PATTERN = r"robinhood"
MAX_PAYLOAD_CHARS = 2_000_000
ROBINHOOD_RESEARCH_TOOLS = frozenset(
    {
        "get_equity_fundamentals",
        "get_financials",
        "get_equity_analyst_ratings",
        "get_earnings_results",
        "get_earnings_calendar",
        "get_sec_filing_index",
        "get_sec_filing_facts",
        "get_option_chains",
        "get_option_instruments",
        "get_option_quotes",
        "get_equity_technical_indicators",
        "get_equity_historicals",
        "get_equity_quotes",
    }
)
# run_scan does not match the ReadOnlyMcp get_/list_ regex, so imports are an explicit, separate allowlist.
ROBINHOOD_IMPORT_TOOLS = frozenset(
    {"get_watchlists", "get_watchlist_items", "get_scans", "run_scan"}
)
ROBINHOOD_ACCOUNT_TOOLS = frozenset(
    {"get_accounts", "get_equity_positions", "get_portfolio", "get_realized_pnl"}
)
# Read-shaped names that must never be callable from Forecast (banking/credit data, or "read" tools that change state).
ROBINHOOD_NEVER = frozenset(
    {
        "get_bank_accounts",
        "get_banking_transactions",
        "get_credit_card_account",
        "get_credit_transactions",
        "decline_trade_approval",
        "set_legend_chart_settings",
        "get_trade_approvals",
        "get_pnl_trade_history",
        "get_equity_tax_lots",
        "get_crypto_positions",
        "get_crypto_orders",
        "get_equity_orders",
        "get_option_orders",
    }
)
ALLOWED = ROBINHOOD_RESEARCH_TOOLS | ROBINHOOD_IMPORT_TOOLS | ROBINHOOD_ACCOUNT_TOOLS

TICKER = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")
ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?$")
ENUM = re.compile(r"^[a-z0-9_]{1,24}$")
CONCEPT = re.compile(r"^[A-Za-z]{2,60}$")
# Argument name -> validator. Anything else is refused: no free text can be smuggled out in an argument.
_ARG = {
    "symbols": lambda v: isinstance(v, list)
    and 0 < len(v) <= 20
    and all(isinstance(s, str) and TICKER.match(s) for s in v),
    "symbol": lambda v: isinstance(v, str) and bool(TICKER.match(v)),
    "underlying_symbol": lambda v: isinstance(v, str) and bool(TICKER.match(v)),
    "chain_symbol": lambda v: isinstance(v, str) and bool(TICKER.match(v)),
    "instrument_ids": lambda v: isinstance(v, list)
    and 0 < len(v) <= 8
    and all(isinstance(s, str) and ID.match(s) for s in v),
    "filing_ids": lambda v: isinstance(v, list)
    and 0 < len(v) <= 2
    and all(isinstance(s, str) and ID.match(s) for s in v),
    "concepts": lambda v: isinstance(v, list)
    and 0 < len(v) <= 6
    and all(isinstance(s, str) and CONCEPT.match(s) for s in v),
    "expiration_dates": lambda v: isinstance(v, list)
    and 0 < len(v) <= 2
    and all(isinstance(s, str) and ISO.match(s) for s in v),
    "start_time": lambda v: isinstance(v, str) and bool(ISO.match(v)),
    "end_time": lambda v: isinstance(v, str) and bool(ISO.match(v)),
    "start_date": lambda v: isinstance(v, str) and bool(ISO.match(v)),
    "end_date": lambda v: isinstance(v, str) and bool(ISO.match(v)),
    "interval": lambda v: isinstance(v, str) and bool(ENUM.match(v)),
    "type": lambda v: isinstance(v, str) and bool(ENUM.match(v)),
    "period": lambda v: (isinstance(v, int) and 1 <= v <= 200)
    or (isinstance(v, str) and bool(ENUM.match(v))),
    "output": lambda v: isinstance(v, str) and bool(ENUM.match(v)),
    "form_type": lambda v: v in ("10-Q", "10-K", "8-K"),
    "high_market_cap": lambda v: isinstance(v, bool),
    "list_id": lambda v: isinstance(v, str) and bool(ID.match(v)),
    "scan_id": lambda v: isinstance(v, str) and bool(ID.match(v)),
    "account_number": lambda v: isinstance(v, str) and bool(ID.match(v)),
    "adjustment_type": lambda v: v in ("split", "none", "all"),
}


# Our argument name -> names a server schema may use instead (argument names were inferred from the catalog, Track R).
ALIASES = {
    "start_date": ("start", "from_date", "date_from", "start_time"),
    "end_date": ("end", "to_date", "date_to", "end_time"),
    "symbols": ("symbol", "tickers"),
    "symbol": ("ticker", "symbols"),
}
_NAME = re.compile(r"^[a-z_]{1,40}$")


class RobinhoodClient(ReadOnlyMcp):
    """Exact-name allowlist + argument guard over one Robinhood MCP server. `requests` records every outbound call
    (tool, args) for the egress eval; it is in-memory only and never logged."""

    def __init__(self, mcp: Any, server_id: str):
        super().__init__(mcp, server_id)
        self.requests: List[Tuple[str, Dict[str, Any]]] = []
        self._schemas: Optional[Dict[str, dict]] = None

    def _fit(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Match code-built args to the live tool schema: rename via ALIASES, drop what the tool doesn't declare,
        fail clearly on a missing required one. No schema available (fixtures, older servers) -> args unchanged."""
        if self._schemas is None:
            try:
                self._schemas = {t.name: t.input_schema or {} for t in self.mcp.list_tools(self.server_id)}
            except Exception:
                self._schemas = {}
        schema = self._schemas.get(tool) or {}
        props = schema.get("properties") or {}
        if not props:
            return args
        out = {}
        for key, value in args.items():
            name = key if key in props else next((a for a in ALIASES.get(key, ()) if a in props), None)
            if name is None:
                continue
            if name in ("symbol", "ticker") and isinstance(value, list):
                value = value[0]
            elif name in ("symbols", "tickers") and isinstance(value, str):
                value = [value]
            out[name] = value
        missing = [r for r in schema.get("required") or () if r not in out and isinstance(r, str) and _NAME.match(r)]
        if missing:
            raise ValueError(f"{tool} needs argument(s) {', '.join(missing[:4])}; update robinhood.py builders")
        return out

    def call(self, tool: str, args: Dict[str, Any]) -> Any:  # type: ignore[override]
        if (
            tool in ROBINHOOD_NEVER
            or tool not in ALLOWED
            or self.mcp.is_sensitive_tool(tool)
        ):
            raise PermissionError(f"tool '{tool}' is not allowed for Forecast")
        for key, value in args.items():
            if key not in _ARG or not _ARG[key](value):
                raise PermissionError(f"argument '{key}' refused")
            if key == "account_number" and tool not in ROBINHOOD_ACCOUNT_TOOLS:
                raise PermissionError(
                    "account_number may only go to account tools (R-S6)"
                )
        args = self._fit(tool, args)
        self.requests.append((tool, dict(args)))
        # Code parses these payloads (never the model), so the 8k chat-context cap would only corrupt the JSON.
        result = self.mcp.call_tool(self.server_id, tool, args, max_chars=MAX_PAYLOAD_CHARS)
        if not result.ok:
            raise ConnectionError(
                f"{tool} returned an error"
            )  # server text is not echoed
        try:
            data = json.loads(result.text)
        except ValueError:
            raise ValueError(f"{tool} returned non-JSON or oversized data") from None
        if not isinstance(data, (dict, list)):
            raise ValueError(f"{tool} returned an unexpected shape")
        return data


def connect(vault: Any, mcp: Any) -> Optional[RobinhoodClient]:
    """Client for the first enabled MCP server named like Robinhood, else None (features show 'not configured')."""
    if vault is None or mcp is None:
        return None
    from src.agents.orchestration.service import find_server

    server_id = find_server(vault, SERVER_PATTERN)
    return RobinhoodClient(mcp, server_id) if server_id else None


# -- tolerant parsing (numbers, dates, tickers only) --------------------------------------------------------------


def _rows(data: Any, *keys: str) -> List[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in keys + ("results", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
            if isinstance(value, dict):
                return _rows(value, *keys)
        return [data]
    return []


def _num(row: dict, *names: str) -> Optional[float]:
    for name in names:
        value = row.get(name)
        if isinstance(value, dict):
            value = value.get("amount", value.get("value"))
        try:
            out = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(out):
            return out
    return None


def _day(row: dict, *names: str) -> Optional[date]:
    for name in names:
        value = row.get(name)
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def _sym(value: Any) -> Optional[str]:
    try:
        return canonical_symbol(str(value).strip())
    except ValueError:
        return None


def _for(rows: List[dict], symbol: str) -> List[dict]:
    hits = [r for r in rows if _sym(r.get("symbol")) == symbol]
    return hits or (rows if len(rows) == 1 and "symbol" not in rows[0] else [])


def _fmt(x: float) -> str:
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(x) >= size:
            return f"{x / size:.2f}{unit}"
    return f"{x:.2f}"


def _card(
    source_type,
    title,
    summary,
    now,
    symbol,
    sentiment="neutral",
    confidence=0.7,
    observed=None,
):
    return EvidenceCard(
        source_id="robinhood",
        source_type=source_type,
        title=title,
        summary=summary,
        retrieved_at=now,
        observed_at=observed,
        symbol=symbol,
        sentiment=sentiment,
        confidence=confidence,
    )


# -- R1-R4 public market evidence ----------------------------------------------------------------------------------


def fundamentals_card(client, symbol, now) -> Optional[EvidenceCard]:
    rows = _for(
        _rows(
            client.call("get_equity_fundamentals", {"symbols": [symbol]}),
            "fundamentals",
        ),
        symbol,
    )
    if not rows:
        return None
    r = rows[0]
    pe, pb = (
        _num(r, "pe_ratio", "pe", "price_earnings"),
        _num(r, "pb_ratio", "price_to_book", "pb"),
    )
    cap = _num(r, "market_cap", "marketcap")
    hi, lo = (
        _num(r, "high_52_weeks", "fifty_two_week_high", "week_52_high"),
        _num(r, "low_52_weeks", "fifty_two_week_low", "week_52_low"),
    )
    last = _num(r, "close", "last_trade_price", "price", "open")
    parts = [
        f"P/E {pe:.2f}" if pe is not None else "P/E n/a",
        f"P/B {pb:.2f}" if pb is not None else "P/B n/a",
    ]
    if cap is not None:
        parts.append(f"market cap {_fmt(cap)}")
    sentiment = "neutral"
    if hi and lo and hi > lo:
        parts.append(f"52-week range {lo:.2f}-{hi:.2f}")
        if last is not None:
            pos = (last - lo) / (hi - lo)
            parts.append(f"price {last:.2f} at {pos:.0%} of the range")
    if pe is None and pb is None and cap is None:
        return None
    return _card(
        "fundamental",
        f"{symbol} fundamentals (Robinhood)",
        "; ".join(parts) + ".",
        now,
        symbol,
        sentiment,
    )


def ratings_card(client, symbol, now, price: Optional[float]) -> Optional[EvidenceCard]:
    rows = _for(
        _rows(
            client.call("get_equity_analyst_ratings", {"symbols": [symbol]}), "ratings"
        ),
        symbol,
    )
    if not rows:
        return None
    r = rows[0]
    buy, hold, sell = (
        _num(r, f"num_{k}_ratings", f"{k}_count", k) for k in ("buy", "hold", "sell")
    )
    low, avg, high = (
        _num(r, "price_target_low", "target_low", "low"),
        _num(r, "price_target_average", "target_avg", "average", "avg"),
        _num(r, "price_target_high", "target_high", "high"),
    )
    if avg is None and buy is None:
        return None
    total = sum(x or 0 for x in (buy, hold, sell))
    parts, sentiment = [], "neutral"
    if total:
        parts.append(
            f"analysts buy {int(buy or 0)} / hold {int(hold or 0)} / sell {int(sell or 0)}"
        )
    if avg is not None:
        parts.append(
            f"price target low {low or 0:.2f} / average {avg:.2f} / high {high or 0:.2f}"
        )
        if price:
            upside = (avg - price) / price
            parts.append(f"average target is {upside:+.1%} vs price {price:.2f}")
            sentiment = (
                "positive"
                if upside > 0.10
                else "negative"
                if upside < -0.05
                else "neutral"
            )
    if total and (sell or 0) / total >= 0.3:
        sentiment = "negative"
    return _card(
        "fundamental",
        f"{symbol} analyst ratings (Robinhood)",
        "; ".join(parts) + ".",
        now,
        symbol,
        sentiment,
        0.6,
    )


def financials_card(client, symbol, now) -> Optional[EvidenceCard]:
    rows = _rows(
        client.call("get_financials", {"symbols": [symbol], "period": "quarterly"}),
        "financials",
        "periods",
    )
    rows = [r for r in rows if _num(r, "revenue", "total_revenue") is not None]
    rows.sort(
        key=lambda r: _day(r, "period_end", "end_date", "fiscal_period_end") or date.min
    )
    if not rows:
        return None
    last = rows[-1]
    rev, margin = (
        _num(last, "revenue", "total_revenue"),
        _num(last, "net_margin", "net_profit_margin"),
    )
    parts = [f"latest quarter revenue {_fmt(rev)}"]
    if margin is not None:
        parts.append(f"net margin {margin * 100 if abs(margin) <= 1 else margin:.1f}%")
    sentiment = "neutral"
    base = rows[-5] if len(rows) >= 5 else rows[0] if len(rows) >= 2 else None
    if base is not None and _num(base, "revenue", "total_revenue"):
        growth = rev / _num(base, "revenue", "total_revenue") - 1
        parts.append(
            f"revenue {growth:+.1%} vs {'the same quarter a year earlier' if len(rows) >= 5 else 'the earliest quarter returned'}"
        )
        sentiment = (
            "positive" if growth > 0.05 else "negative" if growth < -0.05 else "neutral"
        )
    return _card(
        "fundamental",
        f"{symbol} reported financials (Robinhood)",
        "; ".join(parts) + ".",
        now,
        symbol,
        sentiment,
        observed=_dt(_day(last, "period_end", "end_date", "fiscal_period_end")),
    )


def _dt(d: Optional[date]) -> Optional[datetime]:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) if d else None


def earnings_card(
    client, symbol, now, horizon_sessions: int = 21
) -> Optional[EvidenceCard]:
    rows = _rows(client.call("get_earnings_results", {"symbol": symbol}), "earnings")
    today = now.date()
    past = sorted(
        (r for r in rows if _num(r, "actual_eps", "eps_actual") is not None),
        key=lambda r: _day(r, "report_date", "date") or date.min,
    )
    upcoming = sorted(
        (
            d
            for d in (
                _day(r, "report_date", "date")
                for r in rows
                if _num(r, "actual_eps", "eps_actual") is None
            )
            if d and d >= today
        )
    )
    surprises = []
    for r in past[-4:]:
        actual, est = (
            _num(r, "actual_eps", "eps_actual"),
            _num(r, "estimated_eps", "eps_estimate"),
        )
        if est not in (None, 0):
            surprises.append((actual - est) / abs(est))
    if not surprises and not upcoming:
        return None
    parts, sentiment = [], "neutral"
    if surprises:
        avg = sum(surprises) / len(surprises)
        parts.append(f"last {len(surprises)} EPS surprises average {avg:+.1%}")
        sentiment = (
            "positive" if avg > 0.02 else "negative" if avg < -0.02 else "neutral"
        )
    if upcoming:
        nxt = upcoming[0]
        sessions = sum(
            1
            for i in range(1, (nxt - today).days + 1)
            if (today + timedelta(days=i)).weekday() < 5
        )
        inside = sessions <= horizon_sessions
        parts.append(
            f"next report {nxt.isoformat()} (about {sessions} sessions away"
            + ("; inside the forecast window: event risk)" if inside else ")")
        )
    return _card(
        "earnings",
        f"{symbol} earnings (Robinhood)",
        "; ".join(parts) + ".",
        now,
        symbol,
        sentiment,
        0.65,
    )


def sec_facts_card(client, symbol, now) -> Optional[EvidenceCard]:
    filings = _rows(
        client.call("get_sec_filing_index", {"symbol": symbol, "form_type": "10-Q"}),
        "filings",
    )
    filings = [f for f in filings if ID.match(str(f.get("filing_id", f.get("id", ""))))]
    filings.sort(key=lambda f: _day(f, "filed_at", "filing_date", "date") or date.min)
    if not filings:
        return None
    latest = filings[-1]
    fid = str(latest.get("filing_id", latest.get("id")))
    facts = _rows(
        client.call(
            "get_sec_filing_facts",
            {"filing_ids": [fid], "concepts": ["Revenues", "NetIncomeLoss"]},
        ),
        "facts",
    )
    values = {}
    for f in facts:
        concept, value = (
            str(f.get("concept", f.get("name", ""))),
            _num(f, "value", "amount"),
        )
        if (
            concept in ("Revenues", "NetIncomeLoss")
            and value is not None
            and concept not in values
        ):
            values[concept] = value
    filed = _day(latest, "filed_at", "filing_date", "date")
    parts = [f"latest 10-Q filed {filed.isoformat() if filed else 'n/a'}"]
    parts += [
        f"{'revenue' if k == 'Revenues' else 'net income'} {_fmt(v)}"
        for k, v in values.items()
    ]
    sentiment = "negative" if values.get("NetIncomeLoss", 0) < 0 else "neutral"
    return _card(
        "filing",
        f"{symbol} SEC 10-Q facts (Robinhood)",
        "; ".join(parts) + ".",
        now,
        symbol,
        sentiment,
        observed=_dt(filed),
    )


def quote_price(client, symbol) -> Optional[float]:
    rows = _for(
        _rows(client.call("get_equity_quotes", {"symbols": [symbol]}), "quotes"), symbol
    )
    return (
        _num(rows[0], "last_trade_price", "mark_price", "price", "previous_close")
        if rows
        else None
    )


def implied_move_card(
    client, symbol, now, price: Optional[float], horizon_sessions: int = 21
) -> Optional[EvidenceCard]:
    if not price:
        return None
    chains = _rows(
        client.call("get_option_chains", {"underlying_symbol": symbol}), "chains"
    )
    target = now.date() + timedelta(days=int(horizon_sessions * 7 / 5) + 1)
    dates = sorted(
        {
            d
            for c in chains
            for d in (
                date.fromisoformat(x[:10])
                for x in (c.get("expiration_dates") or [])
                if isinstance(x, str) and ISO.match(x[:10])
            )
            if d >= target
        }
    )
    if not dates:
        return None
    exp = dates[0].isoformat()
    contracts = _rows(
        client.call(
            "get_option_instruments",
            {"chain_symbol": symbol, "expiration_dates": [exp]},
        ),
        "instruments",
    )
    strikes = sorted(
        {s for s in (_num(c, "strike_price", "strike") for c in contracts) if s}
    )
    if not strikes:
        return None
    atm = min(strikes, key=lambda s: abs(s - price))
    legs = {
        str(c.get("type", "")).lower(): str(c.get("id", ""))
        for c in contracts
        if _num(c, "strike_price", "strike") == atm
    }
    ids = [legs.get("call"), legs.get("put")]
    if not all(i and ID.match(i) for i in ids):
        return None
    quotes = _rows(client.call("get_option_quotes", {"instrument_ids": ids}), "quotes")
    marks = [
        _num(q, "mark_price", "adjusted_mark_price", "last_trade_price") for q in quotes
    ]
    if len(marks) != 2 or not all(marks):
        return None
    move = sum(marks) / price
    return _card(
        "options",
        f"{symbol} options-implied move (Robinhood)",
        f"At-the-money straddle for {exp} (strike {atm:.2f}) prices a move of about ±{move:.1%} vs price "
        f"{price:.2f}.",
        now,
        symbol,
        "negative" if move > 0.15 else "neutral",
        0.6,
    )


def technicals_card(client, symbol, now) -> Optional[EvidenceCard]:
    end = now.date().isoformat()
    start = (now.date() - timedelta(days=120)).isoformat()
    values = {}
    for kind in ("rsi", "atr", "adx"):
        data = client.call(
            "get_equity_technical_indicators",
            {
                "symbols": [symbol],
                "type": kind,
                "interval": "day",
                "start_time": start,
                "end_time": end,
                "output": "latest",
            },
        )
        rows = _for(_rows(data, "indicators", "values"), symbol) or _rows(
            data, "values"
        )
        value = _num(rows[-1], "value", kind) if rows else None
        if value is not None:
            values[kind] = value
    if not values:
        return None
    sentiment = (
        "negative"
        if values.get("rsi", 50) > 70
        else "positive"
        if values.get("rsi", 50) < 30
        else "neutral"
    )
    return _card(
        "market",
        f"{symbol} technical indicators (Robinhood)",
        "Daily " + ", ".join(f"{k.upper()} {v:.2f}" for k, v in values.items()) + ".",
        now,
        symbol,
        sentiment,
        0.6,
    )


CARD_BUILDERS = (
    "fundamentals",
    "ratings",
    "financials",
    "earnings",
    "sec_facts",
    "implied_move",
    "technicals",
)


def research_evidence(
    client: Optional[RobinhoodClient],
    symbol: str,
    now: datetime,
    horizon_sessions: int = 21,
) -> ProviderResult:
    """Up to 7 public-market cards. Each builder fails independently; nothing account-related is called."""
    if client is None:
        return ProviderResult(
            status="unavailable",
            issues=(
                ProviderIssue(
                    source_id="robinhood",
                    code="not_configured",
                    message="No enabled MCP server named Robinhood (Agents -> MCP Servers).",
                ),
            ),
        )
    symbol = canonical_symbol(symbol)
    cards, failed = [], []
    price = None
    try:
        price = quote_price(client, symbol)
    except Exception:
        failed.append("quotes")
    steps = {
        "fundamentals": lambda: fundamentals_card(client, symbol, now),
        "ratings": lambda: ratings_card(client, symbol, now, price),
        "financials": lambda: financials_card(client, symbol, now),
        "earnings": lambda: earnings_card(client, symbol, now, horizon_sessions),
        "sec_facts": lambda: sec_facts_card(client, symbol, now),
        "implied_move": lambda: implied_move_card(
            client, symbol, now, price, horizon_sessions
        ),
        "technicals": lambda: technicals_card(client, symbol, now),
    }
    for name in CARD_BUILDERS:
        try:
            card = steps[name]()
        except (
            PermissionError,
            ConnectionError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OSError,
        ):
            failed.append(name)
            continue
        except Exception:  # McpError etc.: one family failing must not sink the rest
            failed.append(name)
            continue
        if card is not None:
            cards.append(card)
    issues = tuple(
        ProviderIssue(
            source_id="robinhood",
            code="unavailable",
            message=f"Robinhood {n} unavailable.",
        )
        for n in failed
    )
    if not cards:
        return ProviderResult(
            status="unavailable",
            issues=issues
            or (
                ProviderIssue(
                    source_id="robinhood",
                    code="empty",
                    message="Robinhood returned no usable market data.",
                ),
            ),
        )
    return ProviderResult(
        status="partial" if issues else "completed", value=tuple(cards), issues=issues
    )


# -- R2 events: earnings calendar -----------------------------------------------------------------------------------


class EarningsEvent(Contract):
    symbol: str
    report_date: date
    timing: str = Field(pattern=r"^(am|pm|unknown)$")
    estimated_eps: float | None = None


def earnings_calendar(
    client: RobinhoodClient, start: date, days: int = 7, high_market_cap: bool = True
):
    data = client.call(
        "get_earnings_calendar",
        {
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=min(days, 31))).isoformat(),
            "high_market_cap": high_market_cap,
        },
    )
    out = []
    for r in _rows(data, "earnings", "events"):
        sym, day = _sym(r.get("symbol")), _day(r, "report_date", "date")
        if sym and day:
            timing = str(r.get("timing", r.get("report_time", ""))).lower()
            out.append(
                EarningsEvent(
                    symbol=sym,
                    report_date=day,
                    timing=timing if timing in ("am", "pm") else "unknown",
                    estimated_eps=_num(r, "estimated_eps", "eps_estimate"),
                )
            )
    return tuple(sorted(out, key=lambda e: (e.report_date, e.symbol)))[:100]


# -- R5 outcome tracking: session closes from historicals -----------------------------------------------------------


def session_closes(
    client: RobinhoodClient, symbols: Iterable[str], start: date, end: date, adjustment: str = "split"
) -> Dict[str, Dict[date, float]]:
    syms = [canonical_symbol(s) for s in symbols]
    data = client.call(
        "get_equity_historicals",
        {
            "symbols": syms,
            "interval": "day",
            "start_time": f"{start.isoformat()}T00:00:00Z",
            "end_time": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
            "adjustment_type": adjustment,
        },
    )
    out: Dict[str, Dict[date, float]] = {s: {} for s in syms}
    groups = _rows(data, "historicals", "symbols")
    for g in groups:
        sym = _sym(g.get("symbol"))
        bars = (
            g.get("bars")
            or g.get("historicals")
            or ([g] if "close_price" in g or "close" in g else [])
        )
        for b in bars if isinstance(bars, list) else []:
            if not isinstance(b, dict) or b.get("interpolated"):
                continue  # synthesized gap-filler bars carry no information
            day, close = (
                _day(b, "begins_at", "date", "session"),
                _num(b, "close_price", "close"),
            )
            s = sym or _sym(b.get("symbol"))
            if s in out and day and close and close > 0:
                out[s][day] = close
    return out


# -- R6 imports (tickers only) ---------------------------------------------------------------------------------------


def list_sources(client: RobinhoodClient) -> List[Tuple[str, str, str]]:
    """(kind, id, label) for watchlists and saved scans. Labels are the user's own list names, shown via st.text only."""
    out = []
    for r in _rows(client.call("get_watchlists", {}), "watchlists", "lists"):
        lid = str(r.get("id", r.get("list_id", "")))
        if ID.match(lid):
            out.append(
                (
                    "watchlist",
                    lid,
                    str(r.get("display_name", r.get("name", "watchlist")))[:40],
                )
            )
    for r in _rows(client.call("get_scans", {}), "scans"):
        sid = str(r.get("id", r.get("scan_id", "")))
        if ID.match(sid):
            out.append(("scan", sid, str(r.get("title", "scan"))[:40]))
    return out


def import_symbols(
    client: RobinhoodClient, kind: str, source_id: str, limit: int = 15
) -> List[str]:
    if kind == "watchlist":
        rows = _rows(
            client.call("get_watchlist_items", {"list_id": source_id}),
            "items",
            "instruments",
        )
    elif kind == "scan":
        rows = _rows(
            client.call("run_scan", {"scan_id": source_id}), "rows", "instruments"
        )
    else:
        raise ValueError("unknown import source")
    syms = (
        _sym(r.get("symbol", r.get("ticker")))
        for r in rows
        if str(r.get("object_type", "instrument")).lower()
        in ("instrument", "equity", "stock", "etf")
    )
    return [s for s in dict.fromkeys(syms) if s][:limit]


# -- R7 portfolio fit (private; never in a snapshot or an outbound request to another provider) -----------------------


class Holding(Contract):
    symbol: str
    quantity: float
    average_cost: float | None = None
    market_value: float | None = None


class PortfolioFit(Contract):
    symbol: str
    held: bool
    quantity: float = 0
    weight_pct: float | None = None
    unrealized_pct: float | None = None
    average_cost: float | None = None
    positions: int = 0
    top_weight_pct: float | None = None
    theme_overlap: tuple[str, ...] = ()
    realized_pnl_window: float | None = None
    trust: str = "private"


def _account_numbers(client: RobinhoodClient) -> List[str]:
    """Every readable account: holdings are often outside the one account flagged for agents."""
    rows = _rows(client.call("get_accounts", {}), "accounts")
    nums = [str(r.get("account_number", "")) for r in rows]
    nums = [n for n in dict.fromkeys(nums) if ID.match(n)]
    if not nums:
        raise ValueError("no readable account")
    return nums[:10]


def holdings(client: RobinhoodClient) -> Tuple[List[str], List[Holding]]:
    merged: Dict[str, Holding] = {}
    accounts = _account_numbers(client)
    out: List[Holding] = []
    for account in accounts:
        seen = dict(merged)  # fold into earlier accounts only, never within one account
        for r in _rows(
            client.call("get_equity_positions", {"account_number": account}),
            "positions",
        ):
            sym, qty = _sym(r.get("symbol")), _num(r, "quantity", "shares")
            if not (sym and qty and qty > 0):
                continue
            cost, value = (
                _num(r, "average_buy_price", "average_cost"),
                _num(r, "market_value", "equity"),
            )
            old = seen.get(sym)
            if old:
                total = old.quantity + qty
                cost = (
                    ((old.average_cost or 0) * old.quantity + (cost or 0) * qty) / total
                    if old.average_cost is not None and cost is not None
                    else None
                )
                value = (
                    (old.market_value or 0) + value
                    if old.market_value is not None and value is not None
                    else None
                )
                qty = total
            h = Holding(symbol=sym, quantity=qty, average_cost=cost, market_value=value)
            if old:
                out[out.index(old)] = h
            else:
                out.append(h)
            merged[sym] = h
    return accounts, out


def portfolio_fit(
    client: RobinhoodClient, symbol: str, price: Optional[float] = None
) -> PortfolioFit:
    from .themes import AIInfrastructureThemeModel

    symbol = canonical_symbol(symbol)
    accounts, held = holdings(client)
    total = 0.0
    for account in accounts:
        try:
            portfolio = client.call("get_portfolio", {"account_number": account})
        except Exception:
            continue
        total += (
            _num(
                portfolio if isinstance(portfolio, dict) else {},
                "equity_market_value",
                "market_value",
                "total_equity",
            )
            or 0
        )
    for h in held:
        if h.market_value is None and price and h.symbol == symbol:
            held[held.index(h)] = h.model_copy(
                update={"market_value": h.quantity * price}
            )
    total = total or sum(h.market_value or 0 for h in held) or None
    mine = next((h for h in held if h.symbol == symbol), None)
    weights = [(h.market_value or 0) / total * 100 for h in held] if total else []
    themes = AIInfrastructureThemeModel()
    my_themes = (
        {e.theme_slug for e in themes.get_exposures(symbol)}
        if hasattr(themes, "get_exposures")
        else set()
    )
    overlap = tuple(
        h.symbol
        for h in held
        if h.symbol != symbol
        and my_themes
        and my_themes & {e.theme_slug for e in themes.get_exposures(h.symbol)}
    )
    realized = None
    for account in accounts:
        try:
            pnl = client.call(
                "get_realized_pnl", {"account_number": account, "period": "3m"}
            )
            part = _num(
                pnl if isinstance(pnl, dict) else {},
                "total_realized_gain",
                "realized_gain",
                "total",
            )
        except Exception:
            continue
        if part is not None:
            realized = (realized or 0) + part
    unreal = None
    if mine and mine.average_cost and price:
        unreal = (price - mine.average_cost) / mine.average_cost * 100
    return PortfolioFit(
        symbol=symbol,
        held=mine is not None,
        quantity=mine.quantity if mine else 0,
        weight_pct=round((mine.market_value or 0) / total * 100, 2)
        if mine and total
        else (0.0 if total else None),
        unrealized_pct=round(unreal, 2) if unreal is not None else None,
        average_cost=mine.average_cost if mine else None,
        positions=len(held),
        top_weight_pct=round(max(weights), 2) if weights else None,
        theme_overlap=overlap[:10],
        realized_pnl_window=realized,
    )


class PortfolioNote(Contract):
    summary: str = Field(min_length=1, max_length=400)


@otel.traced("forecast.portfolio_fit")
def explain_fit(client: Any, agent: Dict[str, Any], fit: PortfolioFit):
    """Local, tool-less explanation of the fit figures (R-D1: the local model may see them; R-S6: it has no tools and
    its output only goes to st.text). Returns (note | None, status, invalid_reason)."""
    from .explain import _call
    from .scenarios import _Invalid

    payload = {k: v for k, v in fit.model_dump(mode="json").items() if k != "trust"}
    try:
        out = _call(
            client, agent, {"portfolio_fit": payload}, PortfolioNote, max_tokens=300
        )
        if re.search(r"\b(buy|sell|add to|trim|short)\b", out.summary, re.I):
            raise _Invalid("schema")  # context, not advice
        return out, "completed", None
    except _Invalid as exc:
        return None, "invalid", exc.reason
    except Exception:
        return None, "failed", None
