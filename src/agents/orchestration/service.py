"""Wires the real services into a Coordinator for the chat/UI layer."""

from typing import Any, Callable, Dict, List, Optional, Sequence

from src.agents import store
from src.config import PERSONAL_AI_LLM_PLANNER

from . import memory, schedule
from .bus import MessageBus
from .coordinator import Coordinator, RunReport
from .schemas import Plan
from .policy import RequestRefused, screen
from .ingest import ReadOnlyMcp, brokerage_worker, email_worker, gmail_fetcher, holdings_fetcher
from .verifier import records_evidence, verify_claims
from .workers import expense_worker, forecaster_worker, llm_plan, market_worker, plan_for


def _canaries(records: Sequence[Dict[str, Any]]) -> List[str]:
    """Account identifiers and provider names from the user's own records: if any of these ever shows up
    in an agent-to-agent message the bus blocks it."""
    out = set()
    for r in records:
        ex = r.get("extraction") or {}
        for v in (ex.get("account_identifier"), (r.get("metadata") or {}).get("provider"), ex.get("provider")):
            if isinstance(v, str) and len(v.strip()) >= 4:
                out.add(v.strip())
    return sorted(out)


def find_server(vault: Any, pattern: str) -> Optional[str]:
    """Id of the first enabled MCP server whose name matches (the user binds Gmail/brokerage in Agents -> MCP Servers)."""
    import re
    for s in store.list_mcp_servers(vault):
        if s.get("enabled") and re.search(pattern, str(s.get("name", "")), re.I):
            return s["id"]
    return None


GMAIL_PATTERN, BROKERAGE_PATTERN = r"gmail|google mail", r"robinhood|brokerage|schwab|fidelity|alpaca|ibkr|etrade|vanguard"


def build_coordinator(vault: Any, get_records: Callable[[], Sequence[Dict[str, Any]]],
                      scenario_factory: Optional[Callable] = None, extra_workers: Optional[Dict[str, Any]] = None,
                      mcp: Any = None, quarantine_client: Any = None) -> Coordinator:
    from src.forecaster.providers import PublicDataProviders
    from src.forecaster.research import ResearchPredictionService
    from src.forecaster.scenarios import ScenarioSwarmService

    # Vault (sqlite) is single-threaded: read on THIS thread, hand workers an immutable snapshot, and
    # write audit entries from this thread after the run (see run_query).
    snapshot = list(get_records())
    bus = MessageBus(None, canaries=_canaries(snapshot))
    providers = PublicDataProviders()
    swarm = ScenarioSwarmService(scenario_factory) if scenario_factory else None
    workers = {
        "expense": expense_worker(lambda: snapshot),
        "forecaster": forecaster_worker(ResearchPredictionService(providers=providers), swarm),
        "market": market_worker(providers, bus),
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
    if gmail and quarantine_client is not None:
        prefetch["email"] = gmail_fetcher(ReadOnlyMcp(mcp, gmail))
        workers["email"] = email_worker(cached("email"), quarantine_client)
    if broker:
        prefetch["brokerage"] = holdings_fetcher(ReadOnlyMcp(mcp, broker))
        workers["brokerage"] = brokerage_worker(cached("brokerage"))
    coordinator = Coordinator(workers, deadline_s=90)
    coordinator.bus, coordinator.audit_vault = bus, vault
    coordinator.prefetch, coordinator.fetched = prefetch, fetched
    coordinator.evidence = records_evidence(snapshot)
    coordinator.verifier_client = quarantine_client
    return coordinator


SPECIALISTS = ("expense", "forecaster", "market")  # selectable in chat as builtin-<role>


def _restrict(plan: Plan, role: str) -> Plan:
    steps = tuple(s.model_copy(update={"depends_on": ()}) for s in plan.steps if s.agent == role)
    if not steps:
        raise ValueError(f"The {role} specialist can't answer that on its own; try the Coordinator.")
    return Plan(steps=steps)


def run_query(coordinator: Coordinator, query: str, only: Optional[str] = None,
              planner_client: Any = None) -> RunReport:
    """`only`: run just one specialist's steps. `planner_client`: use the LLM planner (fallback: rules)."""
    refusal = screen(query)
    if refusal:
        raise RequestRefused(refusal.kind)
    plan = None
    if planner_client is not None and PERSONAL_AI_LLM_PLANNER:
        try:
            plan = llm_plan(query, planner_client, tuple(coordinator.workers))
        except Exception:
            plan = None  # invalid/hostile model output is simply not used
    plan = plan or plan_for(query)
    if only:
        plan = _restrict(plan, only)
    prefetch, fetched = getattr(coordinator, "prefetch", {}), getattr(coordinator, "fetched", {})
    for step in plan.steps:
        if step.agent in ("email", "brokerage") and step.agent not in coordinator.workers:
            raise ValueError(f"Connect a {'Gmail' if step.agent == 'email' else 'brokerage'} MCP server "
                             "(Agents → MCP Servers) first; it is used read-only.")
        if step.agent in prefetch:
            try:
                fetched[step.agent] = prefetch[step.agent]()  # calling thread: vault/OAuth access is safe here
            except Exception as exc:
                fetched[step.agent] = exc
    report = coordinator.run(plan)
    if getattr(coordinator, "evidence", None):  # verifier may only add escalations, never clear one
        report.escalations.extend(verify_claims(report.claims, coordinator.evidence, getattr(coordinator, "verifier_client", None)))
    bus, vault = getattr(coordinator, "bus", None), getattr(coordinator, "audit_vault", None)
    if vault is not None:  # audit from the calling thread, never a worker thread
        for entry in (bus.delivered if bus is not None else []):
            store.log_activity(vault, entry)
        for key, f in report.claims.items():
            if "suspicious content ignored" in f.summary:
                store.log_activity(vault, {"kind": "injection_flagged", "claim": key[:80]})
    return report


def propose_from_report(vault: Any, report: RunReport) -> List[str]:
    """Queue (never commit) trusted, resolved cash-flow baselines for user approval."""
    return [memory.propose(vault, key, f"{f.value}", proposer="coordinator", evidence_ids=f.evidence_ids, trust=f.trust)
            for key, f in report.claims.items()
            if key.startswith("cashflow_delta:") and f.status == "ok" and f.value is not None]


def run_scheduled(vault: Any, get_records: Callable[[], Sequence[Dict[str, Any]]],
                  scenario_factory: Optional[Callable] = None, mcp: Any = None, quarantine_client: Any = None) -> List[str]:
    """Run due scheduled jobs (app open, or a cron/launchd caller). Results become Core Memory *proposals* and a
    templated one-line status per job; nothing is committed. Returns the status lines."""
    if not schedule.due_jobs(vault):
        return []
    coordinator = build_coordinator(vault, get_records, scenario_factory, mcp=mcp, quarantine_client=quarantine_client)
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
        store.log_activity(vault, {"kind": "scheduled_run", "job": job["id"], "outcome": status.split(":")[0]})
        lines.append(f"{job['name']}: {status}")
    return lines
