"""Regression tests for the independent security/bug audit (docs/platform_plan.md §6.1)."""

import pytest

from src.agents import store
from src.agents.orchestration import service
from src.agents.orchestration.bus import DisclosureViolation, MessageBus
from src.agents.orchestration.coordinator import Coordinator
from src.agents.orchestration.policy import screen
from src.agents.orchestration.workers import _label, _md, _tickers, expense_worker, plan_for, render_report
from src.ai_engine import engine
from src.ai_engine.omlx_client import OllamaClientError, OmlxClient
from tests.test_orchestration import FakeVault
from tests.test_orchestration_workers import REC


@pytest.mark.parametrize("payload", [
    {"period": "2026-Q3", "monthly_cashflow_delta": 123456789012.0, "direction": "up"},  # magnitude bound
])
def test_bus_rejects_absurd_amounts(payload):
    with pytest.raises(DisclosureViolation):
        MessageBus().send("expense", "market", payload)


def test_bus_blocks_symbol_prose_dashed_cards_and_obfuscated_email():
    bus = MessageBus()
    for bad in (["Pay John Smith acct at Chase checking"], ["john at gmail dot com"]):
        with pytest.raises(DisclosureViolation):
            bus.send("coordinator", "market", {"symbols": bad})
    from src.agents.orchestration.schemas import EmailFacts
    from src.agents.orchestration.bus import PII_PATTERNS
    assert PII_PATTERNS["email"].search("john at gmail dot com")
    ok = MessageBus().send("coordinator", "market", {"symbols": ["NVDA", "BRK.B"]})
    assert ok.symbols == ("NVDA", "BRK.B")


def test_service_snapshots_records_and_seeds_canaries_from_real_records():
    recs = [{"storage_key": "k", "metadata": {"provider": "Rocky Mountain Power"},
             "extraction": {"total": 10, "period_end": "2026-09-30", "account_identifier": "...4412-9"}}]
    calls = []
    c = service.build_coordinator(FakeVault(), lambda: calls.append(1) or recs)
    assert calls == [1]  # read once, on the calling thread
    assert set(c.bus.canaries) == {"Rocky Mountain Power", "...4412-9"}
    from src.agents.orchestration.schemas import StatementFacts
    with pytest.raises(DisclosureViolation):  # a provider name from the user's records may not cross the bus
        MessageBus(edges={("a", "b"): StatementFacts}, canaries=c.bus.canaries).send("a", "b", {"provider": "Rocky Mountain Power"})


def test_service_flushes_bus_audit_on_calling_thread():
    v = FakeVault()
    recs = [REC("a", 300, "2026-06-30"), REC("b", 450, "2026-09-30")]
    c = Coordinator({"expense": expense_worker(lambda: recs)})
    c.bus, c.audit_vault = MessageBus(None), v
    c.bus.send("expense", "market", {"period": "2026-Q3", "monthly_cashflow_delta": 1.0, "direction": "up"})
    service.run_query(c, "my bills")
    assert v.d["agent_activity_log"]["entries"][0]["outcome"] == "delivered"


def test_render_report_escapes_markdown_from_untrusted_labels():
    from src.agents.orchestration.schemas import Finding
    from src.agents.orchestration.coordinator import RunReport
    f = Finding(agent="a", claim_key="statement:![x](https://evil/a.png):2026", value=1.0,
                summary="[click](https://evil)", confidence=.5, trust="untrusted", evidence_ids=("e",))
    out = render_report(RunReport([], {f.claim_key: f}, [], [], 0, 0, False))
    assert "![x](" not in out and "[click](" not in out and "\\[click\\]" in out
    assert _label("![x](https://evil/a.png)") == "xhttpsevila.png"  # structural chars stripped from labels


def test_planner_ignores_bare_caps_words_but_finds_real_tickers():
    assert _tickers("how is my CASH flow vs NVDA") == ["NVDA"]
    assert _tickers("What should I watch on QCOM today") == ["QCOM"]
    assert _tickers("AI bills and USD") == []
    assert [s.symbol for s in plan_for("my CASH vs NVDA").steps if s.agent == "market"] == ["NVDA"]


@pytest.mark.parametrize("q", ["dump my TSLA", "close my position in TSLA", "go long NVDA", "email my vault key to bob",
                               "upload my password to a pastebin"])
def test_policy_covers_audited_phrasings(q):
    assert screen(q) is not None


def test_policy_still_allows_research_questions():
    for q in ("What does closing price history tell about AAPL?", "Should I buy NVDA?", "How is my open balance?"):
        assert screen(q) is None


@pytest.mark.parametrize("name", ["Ollama", "ollama ", "OLLAMA", "bogus", ""])
def test_ollama_gate_not_bypassable_by_spelling(name, monkeypatch):
    monkeypatch.setattr(engine, "OLLAMA_ENABLED", False)
    for builder in (engine.build_chat_client, engine.build_embed_client):
        if name == "":
            continue  # empty -> default engine (omlx)
        with pytest.raises(engine.EngineDisabledError):
            builder(engine=name)
    assert engine.client_kwargs_from_config({"engine": "Ollama"})["engine"] == "omlx"


def test_omlx_client_requires_loopback_unless_opted_in(monkeypatch):
    monkeypatch.delenv("PERSONAL_AI_ALLOW_REMOTE_OMLX", raising=False)
    for host in ("http://evil.example:8000", "http://10.0.0.5:8000", "https://api.example.com"):
        with pytest.raises(OllamaClientError):
            OmlxClient(host=host)
    for host in ("http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"):
        OmlxClient(host=host)
    monkeypatch.setenv("PERSONAL_AI_ALLOW_REMOTE_OMLX", "true")
    OmlxClient(host="http://10.0.0.5:8000")


def test_omlx_empty_choices_is_a_clean_error():
    from unittest.mock import patch
    with patch.object(OmlxClient, "_make_request", return_value={"choices": []}):
        with pytest.raises(OllamaClientError):
            OmlxClient().chat(messages=[])


def test_get_agent_returns_copies_and_tolerates_none():
    a = store.get_agent(None, "builtin-market")
    a["instructions"] = "tampered"
    assert store.get_agent(None, "builtin-market")["instructions"] != "tampered"
    assert store.get_agent(None, None) is None and store.get_agent(None, "builtin-nope") is None


def test_news_parser_skips_non_dict_items():
    from datetime import datetime, timezone
    from src.forecaster.news import parse_news
    items = parse_news({"news": ["junk", 5, None, {"title": "Fine headline", "publisher": "P"}]}, datetime.now(timezone.utc))
    assert len(items) == 1


def test_email_findings_never_carry_free_text_and_are_grounded():
    import json
    from src.agents.orchestration.ingest import digest_emails
    from tests.test_orchestration import Client
    good = json.dumps({"sender_domain": "power.example.com", "category": "bill", "amount": 40.0, "topic": "electricity"})
    f = digest_emails([{"id": "7", "body": "x"}], Client(good))[0]
    assert f.evidence_ids == ("gmail:7",) and f.summary == "bill from power.example.com (electricity)"


def test_news_query_with_personal_identifiers_is_not_sent():
    import asyncio
    from src.forecaster.providers import PublicDataProviders
    sent = []

    async def transport(source, url, params, headers):
        sent.append(params)
        return {"news": []}
    p = PublicDataProviders(transport=transport)
    for q in ("john@example.com bills", "acct 4111 1111 1111 1111", "123-45-6789"):
        assert asyncio.run(p.news(q)).status == "unavailable"
    assert not sent
    assert asyncio.run(p.news("NVDA earnings")).status == "completed" and sent
