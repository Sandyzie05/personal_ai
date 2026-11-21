"""P5 Core Memory read-path eval: seeded approved facts are recalled in a LATER run (through service.run_query and
render_report), corrections supersede, forgotten items are never returned, untrusted-approved items stay labelled."""

from dataclasses import dataclass
from typing import Tuple

from src.agents.orchestration import memory
from src.agents.orchestration.bus import MessageBus
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.service import run_query
from src.agents.orchestration.workers import (
    _md,
    expense_worker,
    market_worker,
    render_report,
)

from .golden import FakeProviders, FakeVault, forecaster_stub

# (key, value, later query that should recall it)
SEEDED: Tuple[Tuple[str, str, str], ...] = (
    ("cashflow_delta:2026-Q1", "-42.5", "What was my cash flow change in 2026 Q1?"),
    ("cashflow_delta:2026-Q2", "18.25", "Cash flow delta for 2026 Q2"),
    ("budget_baseline:2026-Q3", "310", "What is my budget baseline for 2026 Q3?"),
    (
        "preference:electricity_provider",
        "Aurora Electric",
        "For my bills, which electricity provider do I prefer?",
    ),
    ("preference:report_currency", "USD", "For my spending report, which currency do I use?"),
    ("goal:emergency_fund", "6 months", "Given my expenses, what is my emergency fund goal?"),
    ("note:gas_autopay", "autopay on", "Is gas bill autopay set up?"),
    ("note:internet_contract_end", "2027-03", "For my internet bill, when does the contract end?"),
    ("watch:ticker_interest", "NVDA", "Which ticker interest did I note for my stock watch?"),
    ("goal:monthly_savings", "500", "From my budget, what is my monthly savings goal?"),
)


def _approve(vault, key, value, trust="trusted"):
    pid = memory.propose(vault, key, value, proposer="eval", trust=trust)
    return memory.approve(
        vault,
        pid,
        token=memory.issue_approval_token("eval", pid),
        session_id="eval",
        acknowledge_untrusted=trust == "untrusted",
    )


def _coordinator(vault):
    """Golden fake workers wired like service.build_coordinator (memory box + audit vault)."""
    prov, bus = FakeProviders({}), MessageBus(vault, canaries=())
    box = {"facts": []}
    c = Coordinator(
        {
            "expense": expense_worker(lambda: [], lambda: box["facts"]),
            "market": market_worker(prov, bus),
            "forecaster": forecaster_stub({}, prov.requests),
        }
    )
    c.bus, c.audit_vault, c.memory_box = bus, vault, box
    return c


@dataclass
class MemoryResult:
    seeded: int
    recalled: int
    stale_returned: int  # superseded or forgotten values that came back
    untrusted_unlabelled: int
    baseline_findings: int


def run_memory() -> MemoryResult:
    vault = FakeVault()
    committed = {key: _approve(vault, key, value) for key, value, _ in SEEDED}
    # Corrections supersede; forgotten items are deleted.
    _approve(vault, "goal:monthly_savings", "650")
    stale = {"goal:monthly_savings": "500"}
    forgotten = committed["note:gas_autopay"]
    memory.forget(
        vault,
        forgotten["id"],
        token=memory.issue_approval_token("eval", forgotten["id"], "forget"),
        session_id="eval",
    )
    _approve(vault, "note:portal_hint", "pay before the 5th", trust="untrusted")

    recalled = stale_hits = unlabelled = baselines = 0
    for key, value, query in SEEDED:
        report = run_query(_coordinator(vault), query)
        text = render_report(report)
        keys = {m.key: m for m in report.memory}
        if key == "note:gas_autopay":
            stale_hits += key in keys or "autopay on" in text
            continue
        want = "650" if key in stale else value
        recalled += key in keys and keys[key].value == want and _md(want) in text
        stale_hits += key in stale and stale[key] in [
            m.value for m in report.memory if m.key == key
        ]
        baselines += sum(c.startswith("budget_baseline:") for c in report.claims)
    report = run_query(_coordinator(vault), "Any portal hint about when to pay my bills?")
    hint = [m for m in report.memory if m.key == "note:portal_hint"]
    unlabelled += (
        not hint
        or hint[0].trust != "untrusted"
        or "(untrusted-derived)" not in render_report(report)
    )
    return MemoryResult(len(SEEDED) - 1, recalled, stale_hits, unlabelled, baselines)
