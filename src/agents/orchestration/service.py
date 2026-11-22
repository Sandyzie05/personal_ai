"""Wires the real services into a Coordinator for the chat/UI layer."""

from typing import Any, Callable, Dict, List, Optional, Sequence

from src.agents import store
from src.config import EMAIL_DIGEST_STEP_TIMEOUT_SECONDS, FORECAST_STEP_TIMEOUT_SECONDS, PERSONAL_AI_LLM_PLANNER

from . import memory, schedule
from .bus import MessageBus
from .coordinator import Coordinator, Escalation, RunReport
from .schemas import Plan, PlanStep
from .policy import RequestRefused, screen
from .ingest import (
    ReadOnlyMcp,
    brokerage_worker,
    email_worker,
    gmail_fetcher,
    holdings_fetcher,
)
from .verifier import records_evidence, verify_claims
from .workers import (
    ambiguity,
    clarify_question,
    expense_worker,
    forecaster_worker,
    trader_worker,
    llm_plan,
    market_worker,
    named_account,
    plan_for,
)


def _canaries(records: Sequence[Dict[str, Any]]) -> List[str]:
    """Account identifiers and provider names from the user's own records: if any of these ever shows up
    in an agent-to-agent message the bus blocks it."""
    out = set()
    for r in records:
        ex = r.get("extraction") or {}
        for v in (
            ex.get("account_identifier"),
            (r.get("metadata") or {}).get("provider"),
            ex.get("provider"),
        ):
            if isinstance(v, str) and len(v.strip()) >= 4:
                out.add(v.strip())
    return sorted(out)


def _overdue(vault: Any) -> List[Dict[str, Any]]:
    """Expected-but-missing bills from the ingest ledger; a vault without a ledger simply has none."""
    try:
        from src.data_ingestion.pipeline import overdue

        return list(overdue(vault)) if vault is not None else []
    except Exception:
        return []


def find_server(vault: Any, pattern: str) -> Optional[str]:
    """Id of the first enabled MCP server whose name matches (the user binds Gmail/brokerage in Agents -> MCP Servers)."""
    import re

    for s in store.list_mcp_servers(vault):
        if s.get("enabled") and re.search(pattern, str(s.get("name", "")), re.I):
            return s["id"]
    return None


GMAIL_PATTERN, BROKERAGE_PATTERN = (
    r"gmail|google mail",
    r"robinhood|brokerage|schwab|fidelity|alpaca|ibkr|etrade|vanguard",
)


def build_coordinator(
    vault: Any,
    get_records: Callable[[], Sequence[Dict[str, Any]]],
    scenario_factory: Optional[Callable] = None,
    extra_workers: Optional[Dict[str, Any]] = None,
    mcp: Any = None,
    quarantine_client: Any = None,
) -> Coordinator:
    from src.forecaster.providers import PublicDataProviders
    from src.forecaster.research import ResearchPredictionService
    from src.forecaster.scenarios import ScenarioSwarmService

    # Vault (sqlite) is single-threaded: read on THIS thread, hand workers an immutable snapshot, and
    # write audit entries from this thread after the run (see run_query).
    snapshot = list(get_records())
    overdue = _overdue(
        vault
    )  # P12: ledger read here too (S15); workers get the immutable list
    bus = MessageBus(None, canaries=_canaries(snapshot))
    providers = PublicDataProviders()
    from src.agents.slots import critic_agents

    # Critic agents (tweakable in the Agents tab) are read here, on the calling thread, like the records.
    swarm = ScenarioSwarmService(scenario_factory, agents=critic_agents(vault)) if scenario_factory else None
    # P5: recalled Core Memory facts are set per query in run_query (calling thread) and read by the expense step
    # only; no bus edge carries them (S18).
    memory_box: Dict[str, list] = {"facts": []}
    workers = {
        "expense": expense_worker(lambda: snapshot, get_overdue=lambda: overdue, get_memory=lambda: memory_box["facts"]),
        "forecaster": forecaster_worker(ResearchPredictionService(providers=providers), swarm),
        "market": market_worker(providers, bus),
        "trader": trader_worker(providers),
        **(extra_workers or {}),
    }
    # MCP reads touch the vault (OAuth tokens), so they are PREFETCHED on this thread in run_query; pool-thread
    # workers only see the fetched, read-only result (S15). Email also goes through quarantine in the worker.
    prefetch: Dict[str, Callable[[], list]] = {}
    fetched: Dict[str, Any] = {}

    def cached(name):
        def get():
            if isinstance(fetched.get(name), Exception):
                raise ConnectionError(type(fetched[name]).__name__)
            return fetched.get(name, [])

        return get

    gmail = find_server(vault, GMAIL_PATTERN) if mcp else None
    broker = find_server(vault, BROKERAGE_PATTERN) if mcp else None
    from src.data_ingestion import gmail_api

    # Direct read-only Gmail API first (personal accounts); Google's hosted Gmail MCP needs a Developer Preview project.
    gmail_ro = (gmail_api.GmailApi(vault) if vault is not None and gmail_api.connected(vault)
                else ReadOnlyMcp(mcp, gmail) if gmail else None)
    if gmail_ro is not None and quarantine_client is not None:
        prefetch["email"] = gmail_fetcher(gmail_ro)
        workers["email"] = email_worker(cached("email"), quarantine_client)
    if broker:
        prefetch["brokerage"] = holdings_fetcher(ReadOnlyMcp(mcp, broker))
        workers["brokerage"] = brokerage_worker(cached("brokerage"))
    coordinator = Coordinator(
        workers, deadline_s=max(FORECAST_STEP_TIMEOUT_SECONDS, EMAIL_DIGEST_STEP_TIMEOUT_SECONDS) + 30
    )  # forecaster + one dependent wave
    coordinator.bus, coordinator.audit_vault = bus, vault
    coordinator.prefetch, coordinator.fetched = prefetch, fetched
    coordinator.memory_box = memory_box
    coordinator.evidence = records_evidence(snapshot)
    coordinator.records = snapshot  # read-only, for account naming / ambiguity (P2, P3)
    coordinator.verifier_client = quarantine_client
    return coordinator


SPECIALISTS = (
    "expense",
    "forecaster",
    "market",
)  # selectable in chat as builtin-<role>


_EMAIL_ONLY = Plan(steps=(PlanStep(id="email", agent="email", goal="Read-only digest of recent email",
                                    timeout_s=EMAIL_DIGEST_STEP_TIMEOUT_SECONDS),))


def _restrict(plan: Plan, role: str) -> Plan:
    steps = tuple(
        s.model_copy(update={"depends_on": ()}) for s in plan.steps if s.agent == role
    )
    if not steps:
        raise ValueError(
            f"The {role} specialist can't answer that on its own; try the Coordinator."
        )
    return Plan(steps=steps)


def run_query(
    coordinator: Coordinator,
    query: str,
    only: Optional[str] = None,
    planner_client: Any = None,
) -> RunReport:
    """`only`: run just one specialist's steps. `planner_client`: use the LLM planner (fallback: rules)."""
    refusal = screen(query)
    if refusal:
        raise RequestRefused(refusal.kind)
    records = getattr(coordinator, "records", None) or []
    # P5 recall on the calling thread, before the clarify check: an approved fact can answer (or inform) a question
    # that is ambiguous for the record workers.
    vault = getattr(coordinator, "audit_vault", None)
    box = getattr(coordinator, "memory_box", None)
    facts = memory.recall(vault, query) if vault is not None and box is not None else []
    unclear = ambiguity(query, records)
    if unclear:  # P3: one templated question, no steps run, nothing guessed
        return RunReport(
            steps=[],
            claims={},
            conflicts=[],
            wall_s=0.0,
            sum_step_s=0.0,
            parallel_overlap=False,
            escalations=[
                Escalation("clarify", clarify_question(unclear, query, records))
            ],
            memory=facts,
        )
    plan = _EMAIL_ONLY if only == "email" else None  # the Email Digest agent always reads mail, whatever the wording
    if plan is None and planner_client is not None and PERSONAL_AI_LLM_PLANNER:
        try:
            plan = llm_plan(query, planner_client, tuple(coordinator.workers))
        except Exception:
            plan = None  # invalid/hostile model output is simply not used
    plan = plan or plan_for(query)
    if only:
        plan = _restrict(plan, only)
    plan = Plan(
        steps=tuple(
            s.model_copy(
                update={
                    "account": named_account(query, records, s.category)
                    if s.agent == "expense"
                    else None
                }
            )
            for s in plan.steps
        )
    )
    expense = next((s for s in plan.steps if s.agent == "expense"), None)
    prefetch, fetched = (
        getattr(coordinator, "prefetch", {}),
        getattr(coordinator, "fetched", {}),
    )
    for step in plan.steps:
        if (
            step.agent in ("email", "brokerage")
            and step.agent not in coordinator.workers
        ):
            raise ValueError(
                f"Connect a {'Gmail' if step.agent == 'email' else 'brokerage'} MCP server "
                "(Agents → MCP Servers) first; it is used read-only."
            )
        if step.agent in prefetch:
            try:
                fetched[step.agent] = prefetch[
                    step.agent
                ]()  # calling thread: vault/OAuth access is safe here
            except Exception as exc:
                if getattr(exc, "needs_auth", False):  # say what to do instead of a bare "unavailable"
                    raise ValueError(
                        f"{'Gmail' if step.agent == 'email' else 'The brokerage'} needs you to sign in again: "
                        "Upload → Sync → Gmail connection → Connect Gmail (or Agents → MCP Servers → Test read access)."
                    ) from exc
                fetched[step.agent] = exc
    if box is not None:
        box["facts"] = facts  # read on this thread; workers only see this immutable list
    report = coordinator.run(plan)
    report.memory = facts
    if getattr(coordinator, "evidence", None):  # verifier may only add escalations, never clear one
        report.escalations.extend(verify_claims(
            report.claims, coordinator.evidence, getattr(coordinator, "verifier_client", None),
            category=expense.category if expense else None, account=expense.account if expense else None))
    bus, vault = getattr(coordinator, "bus", None), getattr(coordinator, "audit_vault", None)
    if vault is not None:  # audit from the calling thread, never a worker thread
        for entry in bus.delivered if bus is not None else []:
            store.log_activity(vault, entry)
        for key, f in report.claims.items():
            if "suspicious content ignored" in f.summary:
                store.log_activity(
                    vault, {"kind": "injection_flagged", "claim": key[:80]}
                )
    return report


def propose_from_report(vault: Any, report: RunReport) -> List[str]:
    """Queue (never commit) trusted, resolved cash-flow baselines for user approval."""
    return [
        memory.propose(
            vault,
            key,
            f"{f.value}",
            proposer="coordinator",
            evidence_ids=f.evidence_ids,
            trust=f.trust,
        )
        for key, f in report.claims.items()
        if key.startswith("cashflow_delta:")
        and f.status == "ok"
        and f.value is not None
    ]


def run_scheduled(
    vault: Any,
    get_records: Callable[[], Sequence[Dict[str, Any]]],
    scenario_factory: Optional[Callable] = None,
    mcp: Any = None,
    quarantine_client: Any = None,
) -> List[str]:
    """Run due scheduled jobs (app open, or a cron/launchd caller). Results become Core Memory *proposals* and a
    templated one-line status per job; nothing is committed. Returns the status lines."""
    if not schedule.due_jobs(vault):
        return []
    coordinator = build_coordinator(
        vault,
        get_records,
        scenario_factory,
        mcp=mcp,
        quarantine_client=quarantine_client,
    )
    lines: List[str] = []
    for job, outcome in schedule.run_due(vault, lambda q: run_query(coordinator, q)):
        if isinstance(outcome, RunReport):
            n = len(propose_from_report(vault, outcome))
            status = f"ok: {len(outcome.claims)} claims, {len(outcome.escalations)} need you, {n} proposed"
        elif isinstance(outcome, RequestRefused):
            status = "refused (out of scope)"
        else:
            status = f"failed: {type(outcome).__name__}"
        schedule.record_result(vault, job["id"], status)
        store.log_activity(
            vault,
            {
                "kind": "scheduled_run",
                "job": job["id"],
                "outcome": status.split(":")[0],
            },
        )
        lines.append(f"{job['name']}: {status}")
    return lines
