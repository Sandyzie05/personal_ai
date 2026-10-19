"""Selected upstream source registry; only migrated adapters are advertised."""

from .models import DataSourceProfile, SourceId

_SOURCES = (
    DataSourceProfile(
        source_id="yahoo-chart", name="Yahoo Finance chart API",
        categories=("market",),
        caveats="Unofficial personal-research endpoint, not yfinance. USD US equities/ETFs only; no redistribution assurance.",
    ),
    DataSourceProfile(
        source_id="alpha-vantage", name="Alpha Vantage",
        categories=("market",),
        caveats="Key and daily-adjusted endpoint entitlement required. Constrained rate limits; verify provider terms.",
    ),
    DataSourceProfile(
        source_id="sec-edgar", name="SEC EDGAR",
        categories=("filings",),
        caveats="Latest submissions, not historical vintages. Filing recency is not earnings quality; no TTM inference from cumulative facts.",
    ),
    DataSourceProfile(
        source_id="fred", name="FRED latest observations",
        categories=("macro",),
        caveats="Latest revised observations, NOT ALFRED vintages. Unsupported for point-in-time historical research.",
    ),
    DataSourceProfile(
        source_id="curated-ai-infrastructure-theme", name="Curated AI infrastructure map",
        categories=("curated",),
        caveats="Static upstream analyst mapping, not verified live evidence or point-in-time company revenue exposure.",
    ),
)


class SourceRegistry:
    def list_sources(self) -> tuple[DataSourceProfile, ...]:
        return _SOURCES

    def get(self, source_id: SourceId) -> DataSourceProfile:
        return next(item for item in _SOURCES if item.source_id == source_id)

    def provenance_for(self, source_ids) -> tuple[DataSourceProfile, ...]:
        requested = set(source_ids)
        if requested - {item.source_id for item in _SOURCES}:
            raise ValueError("unknown provenance source")
        return tuple(item for item in _SOURCES if item.source_id in requested)
