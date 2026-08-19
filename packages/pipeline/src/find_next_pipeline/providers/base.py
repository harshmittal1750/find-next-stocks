from __future__ import annotations

from typing import Protocol

from find_next_pipeline.models import ProviderResult


class MarketDataProvider(Protocol):
    name: str

    async def fetch(self, tickers: list[str]) -> ProviderResult: ...
