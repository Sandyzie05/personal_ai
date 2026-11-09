"""Pure scoreboard/daily report over ForecastRecord + OutcomeEvaluation pairs.

Replaces stock_predictor's DB-backed daily_prediction_report. No storage or
network. Only evaluated outcomes that match the forecast and whose target close
elapsed (and were evaluated by `as_of`) are scored; everything else is pending
or unresolved, never silently counted.
"""

import csv
import io
from collections import defaultdict
from datetime import datetime
from statistics import fmean
from typing import Iterable, Optional, Sequence

from .models import utc
from .outcomes import ForecastRecord, OutcomeEvaluation

Pair = tuple[ForecastRecord, Optional[OutcomeEvaluation]]

MIN_SAMPLE = 10  # below this a hit rate is noise; report no percentage
MIN_WINDOW = 5
TREND_WINDOW = 7
CSV_COLUMNS = (
    "forecast_id",
    "symbol",
    "as_of",
    "horizon_sessions",
    "target_session",
    "status",
    "expected_excess_return_pct",
    "realized_return_pct",
    "benchmark_return_pct",
    "realized_excess_return_pct",
    "return_estimate_error_pct",
    "directional_match",
    "notes",
)
METRIC_COLUMNS = CSV_COLUMNS[7:11]


def _is_scorable(forecast, outcome, cutoff):
    return not (
        outcome is None
        or outcome.status != "evaluated"
        or forecast.target is None
        or outcome.forecast_id != forecast.forecast_id
        or outcome.evaluated_at < forecast.target.closes_at
        or (cutoff and outcome.evaluated_at > cutoff)
    )


def scorable(pairs: Iterable[Pair], *, as_of: datetime | None = None) -> list[Pair]:
    """Evaluated, identity-matched, horizon-elapsed pairs, oldest forecast first."""
    cutoff = utc(as_of) if as_of else None
    out = [(f, o) for f, o in pairs if _is_scorable(f, o, cutoff)]
    return sorted(out, key=lambda pair: pair[0].as_of)


def _rate(hits: int, n: int):
    """(pct, label); no percentage claim under MIN_SAMPLE."""
    if n < MIN_SAMPLE:
        return None, f"insufficient sample (n={n}, need {MIN_SAMPLE})"
    pct = round(hits / n * 100, 2)
    return pct, f"{pct:.2f}%"


def _mean(values):
    values = list(values)
    return round(fmean(values), 4) if values else None


def _baselines(scored: Sequence[Pair]) -> dict:
    """Naive comparators over outcomes with non-zero realized excess.

    always_up: predicts outperformance every time. persistence: predicts the sign
    of the latest earlier outcome whose target had already closed at this
    forecast's cutoff (no look-ahead).
    """
    up_hits = up_n = per_hits = per_n = 0
    for forecast, outcome in scored:
        excess = outcome.realized_excess_return_pct
        if excess == 0:
            continue
        up_n += 1
        up_hits += excess > 0
        prior = [
            o.realized_excess_return_pct
            for f, o in scored
            if f.target.closes_at <= forecast.as_of
            and o.realized_excess_return_pct != 0
        ]
        if prior:
            per_n += 1
            per_hits += (prior[-1] > 0) == (excess > 0)
    up_pct, up_label = _rate(up_hits, up_n)
    per_pct, per_label = _rate(per_hits, per_n)
    return {
        "always_up": {"n": up_n, "accuracy_pct": up_pct, "accuracy_label": up_label},
        "persistence": {
            "n": per_n,
            "accuracy_pct": per_pct,
            "accuracy_label": per_label,
        },
        "zero_forecast_mean_abs_error_pct": _mean(
            abs(o.realized_excess_return_pct) for _, o in scored
        ),
    }


def summarize(pairs: Sequence[Pair], *, as_of: datetime | None = None) -> dict:
    pairs = list(pairs)
    scored = scorable(pairs, as_of=as_of)
    directional = [(f, o) for f, o in scored if o.directional_match is not None]
    pct, label = _rate(
        sum(o.directional_match for _, o in directional), len(directional)
    )
    n = len(scored)
    scored_ids = {f.forecast_id for f, _ in scored}
    pending = sum(
        1
        for f, o in pairs
        if f.forecast_id not in scored_ids and (o is None or o.status == "immature")
    )
    coverage_pct, coverage_label = _rate(len(directional), n)
    enough = n >= MIN_SAMPLE
    return {
        "total_forecasts": len(pairs),
        "evaluated": n,
        "pending": pending,
        "unresolved": len(pairs) - n - pending,
        "directional_calls": len(directional),
        "abstained": n - len(directional),
        "coverage_pct": coverage_pct,
        "coverage_label": coverage_label,
        "directional_accuracy_pct": pct,
        "directional_accuracy_label": label,
        "mean_abs_return_error_pct": _mean(
            abs(o.return_estimate_error_pct) for _, o in scored
        )
        if enough
        else None,
        "mean_realized_excess_return_pct": _mean(
            o.realized_excess_return_pct for _, o in scored
        )
        if enough
        else None,
        "baselines": _baselines(scored),
    }


def daily_breakdown(
    pairs: Sequence[Pair], *, as_of: datetime | None = None
) -> list[dict]:
    """Per forecast-date (UTC) rows, newest first; daily n is small so labels stay honest."""
    groups = defaultdict(list)
    for forecast, outcome in scorable(pairs, as_of=as_of):
        groups[forecast.as_of.date().isoformat()].append(outcome)
    rows = []
    for day in sorted(groups, reverse=True):
        outcomes = groups[day]
        directional = [o for o in outcomes if o.directional_match is not None]
        pct, label = _rate(
            sum(o.directional_match for o in directional), len(directional)
        )
        rows.append(
            {
                "report_date": day,
                "evaluated": len(outcomes),
                "directional_calls": len(directional),
                "directional_accuracy_pct": pct,
                "directional_accuracy_label": label,
                "mean_realized_excess_return_pct": _mean(
                    o.realized_excess_return_pct for o in outcomes
                ),
            }
        )
    return rows


def _window_accuracy(window: Sequence[Pair]):
    directional = [o for _, o in window if o.directional_match is not None]
    if len(directional) < MIN_WINDOW:
        return None
    return sum(o.directional_match for o in directional) / len(directional) * 100


def trend_summary(pairs: Sequence[Pair], *, as_of: datetime | None = None) -> dict:
    scored = scorable(pairs, as_of=as_of)[::-1]  # newest first
    recent, prior = scored[:TREND_WINDOW], scored[TREND_WINDOW : 2 * TREND_WINDOW]
    r_acc, p_acc = _window_accuracy(recent), _window_accuracy(prior)
    counts = {"recent_evaluated": len(recent), "prior_evaluated": len(prior)}
    if r_acc is None or p_acc is None:
        return {
            "status": "insufficient-history",
            **counts,
            "message": f"Need {MIN_WINDOW}+ directional calls in each of two windows to call a trend.",
        }
    status = "improving" if r_acc > p_acc else "slipping" if r_acc < p_acc else "stable"
    return {
        "status": status,
        **counts,
        "recent_accuracy_pct": round(r_acc, 2),
        "prior_accuracy_pct": round(p_acc, 2),
    }


def narrative(summary: dict, trend: dict) -> list[str]:
    lines = []
    if summary["directional_accuracy_pct"] is None:
        lines.append(
            f"Directional accuracy: {summary['directional_accuracy_label']}; "
            f"{summary['pending']} forecast(s) still pending."
        )
    else:
        lines.append(
            f"Directional accuracy {summary['directional_accuracy_label']} over "
            f"{summary['directional_calls']} calls ({summary['evaluated']} evaluated)."
        )
        base = summary["baselines"]["always_up"]
        if base["accuracy_pct"] is not None:
            delta = summary["directional_accuracy_pct"] - base["accuracy_pct"]
            lines.append(
                f"Versus always-up baseline {base['accuracy_label']}: {delta:+.2f} pts."
            )
    if summary["mean_abs_return_error_pct"] is not None:
        zero = summary["baselines"]["zero_forecast_mean_abs_error_pct"]
        lines.append(
            f"Mean absolute return error {summary['mean_abs_return_error_pct']:.2f}% "
            f"(zero-forecast baseline {zero:.2f}%)."
        )
    if summary["abstained"]:
        lines.append(
            f"{summary['abstained']} evaluated forecast(s) made no directional call."
        )
    lines.append(trend.get("message") or f"Recent trend: {trend['status']}.")
    lines.append(
        "Heuristic research output, not investment advice; past accuracy does not imply future results."
    )
    return lines


def report(pairs: Sequence[Pair], *, as_of: datetime | None = None) -> dict:
    summary, trend = summarize(pairs, as_of=as_of), trend_summary(pairs, as_of=as_of)
    return {
        "summary": summary,
        "daily": daily_breakdown(pairs, as_of=as_of),
        "trend": trend,
        "narrative": narrative(summary, trend),
    }


def csv_safe(value):
    """Neutralize spreadsheet formula injection in text cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_rows(pairs: Sequence[Pair], *, as_of: datetime | None = None) -> list[dict]:
    """One row per forecast (all statuses); metrics only populated when scorable."""
    cutoff = utc(as_of) if as_of else None
    rows = []
    for forecast, outcome in pairs:
        matched = outcome is not None and outcome.forecast_id == forecast.forecast_id
        scored = _is_scorable(forecast, outcome, cutoff)
        row = {
            "forecast_id": forecast.forecast_id,
            "symbol": forecast.symbol,
            "as_of": forecast.as_of.isoformat(),
            "horizon_sessions": forecast.horizon_sessions,
            "target_session": forecast.target.session.isoformat()
            if forecast.target
            else "",
            "status": outcome.status if matched else "pending",
            "expected_excess_return_pct": forecast.expected_excess_return_pct,
            "notes": " | ".join(outcome.notes) if matched else "",
        }
        for key in METRIC_COLUMNS:
            row[key] = getattr(outcome, key) if scored else ""
        match = outcome.directional_match if scored else None
        row["directional_match"] = "" if match is None else str(match).lower()
        rows.append({key: csv_safe(row[key]) for key in CSV_COLUMNS})
    return rows


def to_csv(pairs: Sequence[Pair], *, as_of: datetime | None = None) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(export_rows(pairs, as_of=as_of))
    return out.getvalue()
