import asyncio

import pytest

from src.agents.orchestration.bus import MessageBus
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.workers import (expense_worker, market_worker, plan_for, render_report)
from src.forecaster.models import ProviderIssue, ProviderResult

REC = lambda key, total, end, prov="Rocky Mountain Power": {
    "storage_key": key, "metadata": {"provider": prov},
    "extraction": {"total": total, "period_end": end,
                   "period_start": {"06": "2026-04-01", "09": "2026-07-01"}[end[5:7]]}}  # quarterly statement


def test_planner_routes_and_chains():
    p = plan_for("How do my utility bills affect my budget and what should I watch on NVDA today?")
    ids = {s.id: s for s in p.steps}
    assert set(ids) == {"expense", "forecast_NVDA", "market"} and ids["market"].depends_on == ("expense",)
    assert [s.id for s in plan_for("what is my gas bill").steps] == ["expense"]
    with pytest.raises(ValueError):
        plan_for("hello")


def test_expense_worker_deterministic_delta_and_conflict():
    recs = [REC("a", 300, "2026-06-30"), REC("b", 450, "2026-09-30"), REC("c", 480, "2026-09-30")]
    c = Coordinator({"expense": expense_worker(lambda: recs)})
    r = c.run(plan_for("my bills"))
    d = r.claims["cashflow_delta:2026-Q3"]
    assert d.value == round((450 - 300) / 3, 2) and d.status == "uncertain"  # conflicting copy counted once, flagged
    assert any(x.claim_key.startswith("bill:") and x.claim_key.endswith("2026-09-30") for x in r.conflicts)
    assert r.needs_user and "Conflict" in render_report(r)


def test_expense_worker_no_records_is_unavailable_not_invented():
    r = Coordinator({"expense": expense_worker(lambda: [])}).run(plan_for("my bills"))
    assert r.claims["records"].status == "unavailable" and r.needs_user


def test_market_gets_only_scoped_delta_and_handles_outage():
    class P:
        async def market(self, req):
            return ProviderResult(status="unavailable", issues=(ProviderIssue(
                source_id="yahoo-chart", code="unavailable", message="down"),))
    recs = [REC("a", 300, "2026-06-30", "Secret Payee LLC"), REC("b", 450, "2026-09-30", "Secret Payee LLC")]
    bus = MessageBus(canaries=["Secret Payee"])
    c = Coordinator({"expense": expense_worker(lambda: recs), "market": market_worker(P(), bus),
                     "forecaster": lambda s, i: []})
    r = c.run(plan_for("bills vs market: $NVDA"))
    assert r.claims["lean:NVDA"].status == "unavailable"
    assert "budget_constraint:2026-Q3" in r.claims
    assert [m["outcome"] for m in bus.delivered] == ["delivered"]


def test_budget_constraint_carries_evidence_and_upstream_status():
    from datetime import datetime, timezone
    from src.agents.orchestration.schemas import PlanStep
    from src.agents.orchestration.workers import market_worker as mw

    class P:
        async def market(self, req):
            raise AssertionError("no symbol")
    from src.agents.orchestration.schemas import Finding
    up = Finding(agent="expense", claim_key="cashflow_delta:2026-Q3", value=5.0, summary="s",
                 evidence_ids=("quarter:2026-Q3",), confidence=.8, status="uncertain")
    out = mw(P(), MessageBus())(PlanStep(id="market", agent="market", goal="g"), {"expense": (up,)})
    assert out[0].evidence_ids == ("quarter:2026-Q3",) and out[0].status == "uncertain"


def test_expense_dedupes_identical_and_flags_conflicting_statements():
    recs = [REC("a", 300, "2026-06-30"), REC("b", 450, "2026-09-30")]
    dup = Coordinator({"expense": expense_worker(lambda: recs + [REC("b2", 450, "2026-09-30")])}).run(plan_for("my bills"))
    assert dup.claims["cashflow_delta:2026-Q3"].value == 50.0 and dup.claims["cashflow_delta:2026-Q3"].status == "ok"
    bad = Coordinator({"expense": expense_worker(lambda: recs + [REC("c", 500, "2026-09-30")])}).run(plan_for("my bills"))
    assert bad.claims["cashflow_delta:2026-Q3"].status == "uncertain"


def test_market_as_of_is_data_age_not_fetch_time():
    from datetime import timedelta
    from src.agents.orchestration.schemas import PlanStep
    from evals.golden import FakeProviders
    from src.agents.orchestration.workers import market_worker as mw
    f = mw(FakeProviders({"NVDA": "up"}, age_days=30), MessageBus())(
        PlanStep(id="market", agent="market", goal="g", symbol="NVDA"), {})[0]
    from datetime import datetime, timezone
    assert datetime.now(timezone.utc) - f.as_of > timedelta(days=29)


def _monthly(key, total, month, prov="Northwind Gas", cat="gas"):
    return {"storage_key": key, "metadata": {"provider": prov, "category": cat},
            "extraction": {"total": total, "period_end": f"2026-{month}-28", "period_start": f"2026-{month}-01"}}


def _delta(recs, q="my gas bills"):
    return Coordinator({"expense": expense_worker(lambda: recs)}).run(plan_for(q)).claims.get("cashflow_delta:2026-Q3")


def test_partial_quarter_is_uncertain_full_quarter_ok():
    partial = [_monthly("a", 50, "06"), _monthly("b", 60, "09")]
    assert _delta(partial).status == "uncertain" and "partial" in _delta(partial).summary
    full = [_monthly(f"m{m}", 50, f"{m:02d}") for m in range(4, 10)]
    assert _delta(full).status == "ok" and _delta(full).value == 0


def test_category_filter_and_mixed_categories():
    recs = [REC("a", 300, "2026-06-30"), REC("b", 450, "2026-09-30")]
    for r in recs:
        r["metadata"]["category"] = "electricity"
    mobile = [dict(REC("m1", 40, "2026-06-30", "Fabrikam"), metadata={"provider": "Fabrikam", "category": "mobile"}),
              dict(REC("m2", 90, "2026-09-30", "Fabrikam"), metadata={"provider": "Fabrikam", "category": "mobile"})]
    assert plan_for("my electricity bills").steps[0].category == "electricity"
    assert _delta(recs + mobile, "my electricity bills").value == 50  # mobile excluded
    mixed = _delta(recs + mobile, "my bills")
    assert mixed.status == "uncertain" and "mixed categories" in mixed.summary


def test_superseded_records_excluded():
    old = REC("old", 999, "2026-09-30")
    old["metadata"]["superseded"] = True
    assert _delta([REC("a", 300, "2026-06-30"), REC("b", 450, "2026-09-30"), old], "my bills").value == 50


def test_verifier_flags_unsupported_numbers_and_only_downgrades():
    from src.agents.orchestration.schemas import Finding
    from src.agents.orchestration.verifier import records_evidence, verify_claims
    ev = records_evidence([REC("a", 450, "2026-09-30")])
    good = Finding(agent="expense", claim_key="bill:P:1", value=450.0, summary="s", evidence_ids=("a",), confidence=.9)
    bad = good.model_copy(update={"claim_key": "bill:P:2", "value": 999.0})
    text = good.model_copy(update={"claim_key": "x", "value": "constructive"})

    class C:
        def chat(self, messages, **kw):
            return {"message": {"content": '{"verdict": "unsupported"}'}}
    out = verify_claims({"g": good, "b": bad, "t": text}, ev, C())
    assert [e.detail.split(":")[0] for e in out] == ["b", "t"] and all(e.kind == "unsupported" for e in out)
    assert verify_claims({"g": good}, ev) == []


def _research(snapshot, outlook="bullish"):
    from src.forecaster.models import HorizonPrediction, ResearchPrediction

    h = HorizonPrediction(horizon_sessions=21, heuristic_score=.6, downside_score=.3, coverage=1, outlook=outlook,
                          expected_excess_return_pct=1.0, reasoning=("synthetic",))

    class Research:
        async def research(self, request):
            return ResearchPrediction(snapshot=snapshot, status="completed", horizons=(h,), events=(),
                                      theme_exposures=(), source_provenance=(), limitations=())
    return Research()


class _Swarm:
    def __init__(self, scenarios):
        self.scenarios, self.calls = scenarios, 0

    async def analyze(self, snapshot):
        from types import SimpleNamespace
        self.calls += 1
        ids = (snapshot.evidence[0].evidence_id,)
        return SimpleNamespace(verdict="contradicts", coverage=1.0, disagreement_score=0.0, specialists=[
            SimpleNamespace(opinion=SimpleNamespace(scenario=s, confidence=.8, evidence_ids=ids)) for s in self.scenarios])


def _forecast(snapshot, swarm, outlook="bullish"):
    from src.agents.orchestration.schemas import PlanStep
    from src.agents.orchestration.workers import forecaster_worker

    step = PlanStep(id="f", agent="forecaster", goal="g", symbol="SYN")
    return {f.claim_key: f for f in forecaster_worker(_research(snapshot, outlook), swarm)(step, {})}


def test_forecaster_thesis_skips_llm_critics_when_evidence_is_decisive():
    from tests.test_forecaster_tot import UP, card, snap

    swarm = _Swarm(["bearish"])
    out = _forecast(snap(UP, card("macro", "positive"), card("filing", "positive"), card("curated", "positive")), swarm)
    assert swarm.calls == 0 and "specialists:SYN" not in out
    assert out["thesis:SYN"].value == "bullish" and out["thesis:SYN"].status == "ok"


def test_forecaster_thesis_uses_critics_and_flags_clash_with_heuristic():
    from tests.test_forecaster_tot import mixed

    swarm = _Swarm(["bearish", "bearish", "bearish", "base"])
    out = _forecast(mixed(), swarm, outlook="bullish")
    thesis = out["thesis:SYN"]
    assert swarm.calls == 1 and "specialists:SYN" in out
    assert thesis.value == "bearish" and thesis.status == "uncertain"
    assert "disagrees with heuristic outlook" in thesis.summary and thesis.evidence_ids


def test_llm_planner_cannot_choose_its_own_budget():
    import json
    from src.agents.orchestration.workers import llm_plan
    from src.config import FORECAST_STEP_TIMEOUT_SECONDS

    plan = {"steps": [{"id": "f", "agent": "forecaster", "goal": "g", "symbol": "SYN", "timeout_s": 300},
                      {"id": "e", "agent": "expense", "goal": "g", "timeout_s": 300}]}

    class Client:
        def chat(self, **_):
            return {"message": {"content": json.dumps(plan)}}
    steps = {s.agent: s.timeout_s for s in llm_plan("q", Client(), ["forecaster", "expense"]).steps}
    assert steps == {"forecaster": FORECAST_STEP_TIMEOUT_SECONDS, "expense": 30}
