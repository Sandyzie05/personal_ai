import asyncio
import json
import threading
import time
from datetime import datetime, timezone

import pytest

from src.forecaster.models import EvidenceCard, EvidenceSnapshot
from src.config import DEFAULT_CONTEXT_WINDOW_TOKENS
from src.forecaster.providers import ForecastSettings
from src.forecaster.scenarios import ScenarioSwarmService


def snapshot():
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    card = EvidenceCard(source_id="yahoo-chart", source_type="market", symbol="NVDA",
                        title="Prices", summary="Completed closes", retrieved_at=now, confidence=.8)
    return EvidenceSnapshot(symbol="NVDA", as_of=now, created_at=now, evidence=(card,))


class Client:
    max_tokens = None
    model = "offline-test-double"
    context_window_tokens = DEFAULT_CONTEXT_WINDOW_TOKENS

    def __init__(self, behavior=None):
        self.behavior = behavior

    def chat(self, *, messages, **kwargs):
        role = messages[1]["content"].split("\n")[0].split(":")[1]
        packet = json.loads(messages[1]["content"].split("PUBLIC_SNAPSHOT:")[1])
        opinion = dict(
            snapshot_id=packet["snapshot_id"], role=role, stance="supports", confidence=.8,
            scenario="bullish", evidence_ids=[packet["evidence"][0]["evidence_id"]],
            source_ids=["yahoo-chart"], key_reason="Positive supplied evidence.",
            what_changes_my_view="Adverse evidence.", risks=["Limited coverage."],
        )
        if self.behavior:
            self.behavior(role, packet, opinion)
        return {"message": {"content": json.dumps(opinion)}}


def test_roles_really_overlap_and_receive_identical_snapshot():
    barrier = threading.Barrier(4)
    packets = []

    def behavior(role, packet, opinion):
        packets.append(packet)
        barrier.wait(timeout=2)

    report = asyncio.run(ScenarioSwarmService(lambda remaining_seconds: Client(behavior)).analyze(snapshot()))
    assert report.status == "completed"
    assert len(packets) == 4 and all(p == packets[0] for p in packets)
    assert report.coverage == 1
    assert report.support_score == 1
    assert report.disagreement_score == 0
    assert report.fragility_score == pytest.approx(.18)
    assert report.model_calls == 4
    assert all(result.model_id == "offline-test-double" for result in report.specialists)
    assert report.created_at.tzinfo is not None


@pytest.mark.parametrize("bad", [
    {"confidence": "0.8"}, {"confidence": float("nan")}, {"confidence": 2},
    {"evidence_ids": ["invented"]}, {"source_ids": ["fred"]},
    {"snapshot_id": "0" * 64}, {"role": "macro"},
    {"extra_field": "unexpected"},
    {"scenario": "bearish"},
])
def test_invalid_role_remains_visible_without_erasing_successes(bad):
    def behavior(role, packet, opinion):
        if role == "downside":
            opinion.update(bad)
    report = asyncio.run(ScenarioSwarmService(lambda remaining_seconds: Client(behavior)).analyze(snapshot()))
    assert report.status == "partial"
    assert report.coverage == .75
    assert report.specialists[-1].status == "invalid"
    assert report.model_calls == 4


def test_one_transient_retry_consumes_shared_budget():
    counts = {}
    lock = threading.Lock()

    def behavior(role, packet, opinion):
        with lock:
            counts[role] = counts.get(role, 0) + 1
        if role == "macro" and counts[role] == 1:
            raise ConnectionError("offline transient")

    report = asyncio.run(ScenarioSwarmService(lambda remaining_seconds: Client(behavior)).analyze(snapshot()))
    assert report.status == "completed"
    assert report.model_calls == 5
    assert counts["macro"] == 2


def test_deadline_returns_without_claiming_thread_cancel_or_waiting_for_shutdown():
    release = threading.Event()
    finished = threading.Event()
    count = [0]
    lock = threading.Lock()

    def behavior(*args):
        release.wait(2)
        with lock:
            count[0] += 1
            if count[0] == 4:
                finished.set()
    settings = ForecastSettings(scenario_deadline_seconds=.02)
    service = ScenarioSwarmService(lambda remaining_seconds: Client(behavior), settings=settings)
    started = time.monotonic()
    try:
        report = asyncio.run(service.analyze(snapshot()))
        assert time.monotonic() - started < 1
        assert report.status == "failed"
        assert all(role.status == "timeout" for role in report.specialists)
        assert any("cannot be force-cancelled" in note for note in report.limitations)
        second = asyncio.run(service.analyze(snapshot()))
        assert all(role.status == "budget" for role in second.specialists)
        assert second.model_calls == 0
    finally:
        release.set()
        assert finished.wait(2)


def test_empty_snapshot_abstains_without_model_calls():
    packet = snapshot().model_copy(update={"evidence": ()})
    def factory(remaining_seconds):
        pytest.fail("empty snapshot must not call model")
    report = asyncio.run(ScenarioSwarmService(factory).analyze(packet))
    assert report.status == "failed"
    assert report.support_score is None
    assert report.model_calls == 0


def test_unbounded_zeroarg_factory_rejected_without_model_call():
    with pytest.raises(ValueError, match="budget"):
        ScenarioSwarmService(lambda: Client())


def test_remaining_budget_reaches_native_transport_and_stalled_workers_release_slots(monkeypatch):
    monkeypatch.setattr("src.ai_engine.engine.OLLAMA_ENABLED", True)
    import httpx
    import ollama

    from src.ai_engine.engine import build_chat_client

    original = ollama.Client
    stalled = [True]
    timeouts = []
    released = threading.Event()
    returned = [0]
    lock = threading.Lock()

    def handler(request):
        if stalled[0]:
            timeout = request.extensions["timeout"]["read"]
            with lock:
                timeouts.append(timeout)
            try:
                time.sleep(timeout + .02)
                raise httpx.ReadTimeout("offline stalled transport", request=request)
            finally:
                with lock:
                    returned[0] += 1
                    if returned[0] == 4:
                        released.set()
        messages = json.loads(request.content)["messages"]
        response = Client().chat(messages=messages)
        response["message"]["role"] = "assistant"
        return httpx.Response(200, json=response)

    monkeypatch.setattr(ollama, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    budgets = []

    def factory(remaining_seconds):
        budgets.append(remaining_seconds)
        return build_chat_client(engine="ollama", request_timeout_seconds=remaining_seconds,
                                 safe_local_transport=True)

    service = ScenarioSwarmService(factory, ForecastSettings(scenario_deadline_seconds=.1))
    report = asyncio.run(service.analyze(snapshot()))
    assert all(result.status == "timeout" for result in report.specialists)
    assert released.wait(2)
    # Worker cleanup follows the native request's timeout rather than waiting
    # forever for an external release event.
    limit = time.monotonic() + 1
    while time.monotonic() < limit:
        available = 0
        for _ in range(4):
            if not service._slots.acquire(blocking=False):
                break
            available += 1
        for _ in range(available):
            service._slots.release()
        if available == 4:
            break
        time.sleep(.005)
    else:
        pytest.fail("native transport timeouts did not release all worker slots")
    stalled[0] = False
    next_report = asyncio.run(ScenarioSwarmService(
        factory, ForecastSettings(scenario_deadline_seconds=2),
    ).analyze(snapshot()))
    assert next_report.status == "completed"
    assert len(timeouts) == 4
    assert all(0 < timeout <= .1 for timeout in timeouts)
    assert all(0 < budget <= .1 for budget in budgets[:4])
