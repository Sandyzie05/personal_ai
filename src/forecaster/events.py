"""Keyword event extraction migrated from event_extractor, with signed adversity."""

import re

from .models import EvidenceCard, MarketEvent


class EventExtractionService:
    keyword_rules = (
        ("regulation", "risk_up", .70, ("export control", "tariff", "sanction", "regulation", "antitrust")),
        ("supply_chain", "risk_up", .68, ("shortage", "supply", "lead time", "packaging", "foundry")),
        ("macro", "risk_up", .58, ("fed", "inflation", "rates", "yield", "recession", "cpi")),
        ("ai_demand", "demand_up", .82, ("ai", "gpu", "inference", "agent", "datacenter", "data center", "hbm")),
        ("capex", "demand_up", .72, ("capex", "capital expenditure", "infrastructure spending", "capacity expansion")),
        ("earnings", "margin_up", .66, ("earnings", "revenue", "beat", "guidance", "margin")),
        ("product", "demand_up", .56, ("launch", "partnership", "platform", "service", "product")),
    )

    def extract(self, evidence_cards: tuple[EvidenceCard, ...]) -> tuple[MarketEvent, ...]:
        events = []
        for evidence in evidence_cards:
            # Static theme membership is not a market catalyst.
            if evidence.source_type == "curated":
                continue
            text = f"{evidence.title} {evidence.summary}".lower()
            event_type, direction, magnitude = "general", "neutral", .45
            for kind, candidate, strength, keywords in self.keyword_rules:
                if any(re.search(r"\b" + re.escape(word) + r"\b", text) for word in keywords):
                    event_type, direction, magnitude = kind, candidate, strength
                    break
            adverse = evidence.sentiment == "negative" or bool(
                re.search(r"\b(miss(?:es)?|cut(?:s)?|decline|slump|weak|ban|loss|delay(?:s)?)\b", text)
            )
            if adverse:
                direction = {"demand_up": "demand_down", "margin_up": "margin_down"}.get(
                    direction, "risk_up"
                )
            events.append(MarketEvent(
                event_type=event_type, direction=direction,
                magnitude=magnitude * evidence.confidence,
                evidence_id=evidence.evidence_id, source_id=evidence.source_id,
            ))
        return tuple(events)
