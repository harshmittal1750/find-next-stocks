from __future__ import annotations

import asyncio

from find_next_pipeline.models import MetricObservation, ProviderResult
from find_next_pipeline.normalization import select_canonical_metrics
from find_next_pipeline.providers.base import MarketDataProvider


class IngestionRunner:
    """Fetch every enabled provider; provider failure does not erase other observations."""

    def __init__(self, providers: list[MarketDataProvider]) -> None:
        self.providers = providers

    async def run(
        self, tickers: list[str]
    ) -> tuple[list[ProviderResult], list[MetricObservation]]:
        results = await asyncio.gather(*(provider.fetch(tickers) for provider in self.providers))
        observations = [item for result in results for item in result.observations]
        _, normalized = select_canonical_metrics(observations)
        return results, normalized
