from src.agents.orchestration import memory, service
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.workers import expense_worker
from tests.test_orchestration import FakeVault
from tests.test_orchestration_workers import REC


def test_report_proposes_but_never_commits():
    v = FakeVault()
    recs = [REC("a", 300, "2026-06-30"), REC("b", 450, "2026-09-30")]
    rep = service.run_query(Coordinator({"expense": expense_worker(lambda: recs)}), "my bills")
    ids = service.propose_from_report(v, rep)
    assert len(ids) == 1 and memory.list_core(v) == [] and len(memory.list_pending(v)) == 1


def test_out_of_scope_requests_refused_before_planning():
    import pytest
    from src.agents.orchestration.policy import RequestRefused
    called = []
    c = Coordinator({"expense": lambda s, i: called.append(1) or [], "forecaster": lambda s, i: [], "market": lambda s, i: []})
    for q in ("Buy 10 shares of NVDA", "pay my electricity bill", "print my vault key"):
        with pytest.raises(RequestRefused):
            service.run_query(c, q)
    assert not called


def test_skill_pack_installs_without_clobbering(tmp_path, monkeypatch):
    import src.agents.skills as sk
    from src.agents.orchestration.skill_pack import PACK_DIR, install_builtin_skills
    monkeypatch.setattr(sk, "SKILLS_DIR", str(tmp_path))
    first = install_builtin_skills()
    assert len(first) == len(list(PACK_DIR.glob("*.md"))) >= 6
    (tmp_path / "conflict-escalation.md").write_text("mine")
    assert install_builtin_skills() == [] and (tmp_path / "conflict-escalation.md").read_text() == "mine"


def test_every_template_skill_exists_and_safety_skills_attached():
    from src.agents.orchestration.skill_pack import PACK_DIR
    from src.agents.orchestration.templates import BUILTIN_AGENTS
    slugs = {p.stem for p in PACK_DIR.glob("*.md")}
    for a in BUILTIN_AGENTS.values():
        assert set(a["skills"]) <= slugs
    for role in ("email", "portal", "market"):
        assert "untrusted-content-handling" in BUILTIN_AGENTS[f"builtin-{role}"]["skills"]


def test_specialist_restriction_and_llm_planner_falls_back(monkeypatch):
    import json
    import pytest
    from src.agents.orchestration.coordinator import Coordinator
    from src.agents.orchestration.workers import llm_plan, plan_for

    calls = []
    c = Coordinator({"expense": lambda s, i: calls.append("e") or [], "forecaster": lambda s, i: calls.append("f") or [],
                     "market": lambda s, i: calls.append("m") or []})
    service.run_query(c, "my bills and what to watch on NVDA", only="forecaster")
    assert calls == ["f"]
    with pytest.raises(ValueError):
        service.run_query(c, "my bills", only="market")

    class Bad:
        def chat(self, messages, **kw):
            return {"message": {"content": json.dumps({"steps": [{"id": "x", "agent": "shell", "goal": "rm -rf"}]})}}
    with pytest.raises(Exception):
        llm_plan("my bills", Bad(), ("expense",))  # non-allowlisted agent rejected by validate_plan
    monkeypatch.setattr(service, "PERSONAL_AI_LLM_PLANNER", True)
    calls.clear()
    service.run_query(c, "my bills", planner_client=Bad())  # falls back to the rule planner
    assert calls == ["e"]

    class Good:
        def chat(self, messages, **kw):
            return {"message": {"content": plan_for("my bills").model_dump_json()}}
    assert llm_plan("my bills", Good(), ("expense",)) == plan_for("my bills")
