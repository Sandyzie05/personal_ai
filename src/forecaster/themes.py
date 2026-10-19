"""Curated upstream AI infrastructure exposures; not live market observations."""

from datetime import datetime

from .models import EvidenceCard, ThemeExposure, canonical_symbol


class AIInfrastructureThemeModel:
    theme_slug = "ai-infrastructure"
    layers = {
        "gpu-accelerator": (("NVDA", "AMD", "AVGO", "MRVL"), ("AI training demand", "inference acceleration", "accelerator attach rates")),
        "foundry-manufacturing": (("TSM", "INTC", "GFS"), ("advanced-node demand", "chip supply resilience", "regional subsidies")),
        "equipment-process-control": (("ASML", "AMAT", "LRCX", "KLAC"), ("EUV demand", "capacity expansion", "process control complexity")),
        "eda-ip": (("SNPS", "CDNS", "ARM"), ("chip design starts", "custom silicon", "IP licensing")),
        "memory-storage": (("MU", "WDC", "STX"), ("HBM demand", "AI server memory intensity", "storage growth")),
        "networking": (("ANET", "AVGO", "MRVL", "CSCO"), ("cluster scale-out", "ethernet AI fabrics", "optical networking")),
        "servers-integration": (("SMCI", "DELL", "HPE"), ("AI server demand", "rack-scale deployments", "enterprise refresh")),
        "datacenter-power-cooling": (("VRT", "ETN", "PWR", "GNRC"), ("power density", "liquid cooling", "grid interconnect demand")),
        "datacenter-real-estate": (("EQIX", "DLR"), ("datacenter capacity", "cloud colocation", "interconnect demand")),
        "cloud-ai-platforms": (("MSFT", "AMZN", "GOOGL", "ORCL", "META"), ("hyperscaler capex", "AI platform services", "inference consumption")),
        "agentic-inference-software": (("NOW", "CRM", "PLTR", "SNOW", "DDOG", "NET", "MDB"), ("enterprise AI adoption", "agent workflows", "observability and data infrastructure")),
    }
    primary_scores = {
        "NVDA": 1.0, "TSM": .95, "ASML": .90, "AVGO": .86, "ANET": .84,
        "VRT": .82, "MSFT": .78, "AMZN": .75, "GOOGL": .74, "META": .72,
    }
    bottlenecks = {
        "gpu-accelerator": ("HBM supply", "advanced packaging capacity", "export controls"),
        "foundry-manufacturing": ("Taiwan concentration", "advanced-node capacity", "geopolitical risk"),
        "equipment-process-control": ("fab capex cyclicality", "export controls", "long lead times"),
        "memory-storage": ("HBM yield", "memory cycle volatility", "customer concentration"),
        "networking": ("cluster architecture shifts", "optical component supply", "hyperscaler capex timing"),
        "datacenter-power-cooling": ("grid interconnect delays", "power equipment lead times", "cooling density"),
        "agentic-inference-software": ("AI monetization proof", "valuation risk", "platform competition"),
    }

    def get_exposures(self, symbol: str) -> tuple[ThemeExposure, ...]:
        symbol = canonical_symbol(symbol)
        exposures = []
        for layer, (symbols, drivers) in self.layers.items():
            if symbol not in symbols:
                continue
            fallback = .74 if layer in {
                "gpu-accelerator", "datacenter-power-cooling", "networking"
            } else .66 if layer in {
                "agentic-inference-software", "servers-integration"
            } else .62
            exposures.append(ThemeExposure(
                theme_slug=self.theme_slug, symbol=symbol, layer=layer,
                score=self.primary_scores.get(symbol, fallback), drivers=drivers,
                bottlenecks=self.bottlenecks.get(
                    layer, ("HBM supply", "advanced packaging capacity", "datacenter power availability")
                ),
            ))
        return tuple(exposures)

    def evidence_for(self, exposure: ThemeExposure, retrieved_at: datetime) -> EvidenceCard:
        return EvidenceCard(
            source_id="curated-ai-infrastructure-theme", source_type="curated",
            title=f"Curated AI exposure: {exposure.layer}",
            summary=f"{exposure.symbol}: " + ", ".join(exposure.drivers),
            symbol=exposure.symbol, retrieved_at=retrieved_at,
            sentiment="neutral", confidence=exposure.score,
        )
