"""Selected SEC filing recency logic, without upstream's invalid cumulative TTM."""

from datetime import date, datetime, timezone

from .models import ContextSignal, EvidenceCard, ProviderIssue, ProviderResult
from .providers import ProviderFailure, unavailable


class FilingSignalService:
    def __init__(self, providers):
        self.providers = providers

    async def get_filing_snapshot(self, request):
        p, symbol = self.providers, request.symbol
        if request.as_of is not None:
            return unavailable("sec-edgar", "unsupported_as_of",
                               "Latest SEC submissions do not establish historical availability.", "unsupported")
        if not p.settings.sec_user_agent:
            return unavailable("sec-edgar", "not_configured", "SEC contact User-Agent is not configured.")
        cached = p.cache.get(("filings", symbol))
        if cached is not None:
            return cached
        try:
            mapping = p.cache.get("sec-tickers")
            if mapping is None:
                mapping = await p._json("sec-edgar", "tickers")
                if not isinstance(mapping, dict):
                    raise ValueError("invalid ticker mapping")
                p.cache.set("sec-tickers", mapping)
            cik = next((item["cik_str"] for item in mapping.values()
                        if item["ticker"].replace("-", ".").upper() == symbol), None)
            if cik is None:
                return unavailable("sec-edgar", "unavailable", "No SEC issuer mapping for requested symbol.")
            cik = int(cik)
            if not 0 < cik < 10**10:
                raise ValueError("invalid CIK")
            submissions = await p._json("sec-edgar", f"{cik:010d}")
            result = self.build_signal(symbol, submissions, p.clock())
            return p.cache.set(("filings", symbol), result)
        except ProviderFailure as exc:
            return ProviderResult(status="unavailable", issues=(exc.issue,))
        except (KeyError, TypeError, ValueError, OverflowError):
            return unavailable("sec-edgar", "schema", "Invalid SEC filing metadata.")

    @staticmethod
    def build_signal(symbol, submissions, now):
        tickers = {ticker.replace("-", ".").upper() for ticker in submissions["tickers"]}
        if symbol not in tickers:
            raise ValueError("SEC issuer identity mismatch")
        recent = submissions["filings"]["recent"]
        forms, dates, accessions = (recent[key] for key in ("form", "filingDate", "accessionNumber"))
        if not (len(forms) == len(dates) == len(accessions)):
            raise ValueError("misaligned filings")
        filings = []
        for form, filed, accession in zip(forms, dates, accessions):
            day = date.fromisoformat(filed)
            if day > now.date():
                raise ValueError("future filing")
            if form in {"10-Q", "10-K", "8-K", "10-Q/A", "10-K/A"}:
                filings.append((day, form, accession))
        filings.sort(reverse=True)
        if not filings:
            return unavailable("sec-edgar", "incomplete", "No supported periodic or current filings.")
        periodic = next((f for f in filings if f[1] in {"10-Q", "10-K"}), None)
        score = .08 if periodic and (now.date() - periodic[0]).days <= 120 else 0.0
        if (now.date() - filings[0][0]).days > 180:
            score -= .08
        cards = tuple(EvidenceCard(
            source_id="sec-edgar", source_type="filing", symbol=symbol,
            title=f"SEC {form} filing", summary=f"{symbol} filed {form} on {day}; accession {accession}.",
            published_at=datetime.combine(day, datetime.min.time(), timezone.utc),
            retrieved_at=now, confidence=.85,
        ) for day, form, accession in filings[:4])
        return ProviderResult[ContextSignal](status="completed", value=ContextSignal(
            source_id="sec-edgar", score=score, evidence=cards,
            notes=("Recency is not earnings quality; cumulative XBRL values are not treated as quarters.",
                   "Latest submissions only; no point-in-time historical claim."),
        ))
