from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.forecaster.events import EventExtractionService
from src.forecaster.models import EvidenceCard, EvidenceSnapshot, ResearchRequest
from src.forecaster.source_registry import SourceRegistry
from src.forecaster.themes import AIInfrastructureThemeModel

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def card(**kwargs):
    return EvidenceCard(
        source_id="yahoo-chart", source_type="news", title="AI demand",
        summary="GPU inference growth", retrieved_at=NOW, confidence=.8, **kwargs
    )


@pytest.mark.parametrize("symbol", ["AAPL secret", "../AAPL", "https://host", "AAPL\n", "^GSPC", "AAPL?x=y"])
def test_symbols_reject_private_text_and_transport_syntax(symbol):
    with pytest.raises(ValidationError):
        ResearchRequest(symbol=symbol)


def test_request_and_evidence_fail_closed():
    assert ResearchRequest(symbol="brk.b").symbol == "BRK.B"
    for kwargs in ({"horizons": (63,)}, {"horizons": ()}, {"prompt": "private"},
                   {"as_of": datetime(2026, 1, 1)}):
        with pytest.raises(ValidationError):
            ResearchRequest(symbol="AAPL", **kwargs)
    with pytest.raises(ValidationError):
        card(sentiment="invented")
    with pytest.raises(ValidationError):
        card(point_in_time=True)


def test_snapshot_identity_is_immutable_content_based():
    evidence = card()
    snapshot = EvidenceSnapshot(symbol="NVDA", as_of=NOW, created_at=NOW, evidence=(evidence,))
    assert snapshot.snapshot_id == EvidenceSnapshot.model_validate_json(
        snapshot.model_dump_json()
    ).snapshot_id
    assert snapshot.snapshot_id != EvidenceSnapshot(
        symbol="NVDA", as_of=NOW, created_at=NOW,
        evidence=(card(sentiment="negative"),),
    ).snapshot_id
    with pytest.raises(ValidationError):
        snapshot.symbol = "AMD"


@pytest.mark.parametrize("sentiment,summary", [
    ("negative", "GPU inference growth"),
    ("neutral", "GPU demand cut after export control ban"),
])
def test_adverse_events_never_add_positive_strength(sentiment, summary):
    evidence = EvidenceCard(
        source_id="yahoo-chart", source_type="news", title="AI demand",
        summary=summary, retrieved_at=NOW, confidence=.8, sentiment=sentiment,
    )
    event, = EventExtractionService().extract((evidence,))
    assert event.signed_strength < 0
    assert event.evidence_id == evidence.evidence_id


def test_theme_mapping_and_provenance_are_honest():
    theme = AIInfrastructureThemeModel()
    assert len(theme.layers) == 11
    assert len(theme.get_exposures("AVGO")) == 2
    assert theme.get_exposures("NVDA")[0].score == 1
    assert theme.get_exposures("ZZZZ") == ()
    evidence = theme.evidence_for(theme.get_exposures("NVDA")[0], NOW)
    assert EventExtractionService().extract((evidence,)) == ()
    registry = SourceRegistry()
    assert "not yfinance" in registry.get("yahoo-chart").caveats
    assert registry.get("fred").point_in_time is False
    with pytest.raises(ValueError):
        registry.provenance_for(("made-up-provider",))
