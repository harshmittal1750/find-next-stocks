from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

import typer

from find_next_pipeline.providers import AlphaVantageProvider, YahooChartProvider
from find_next_pipeline.providers.http import ArchivedHttpClient
from find_next_pipeline.raw_store import RawJsonStore
from find_next_pipeline.runner import IngestionRunner
from find_next_pipeline.warehouse import DuckDbWarehouse


def fetch(
    ticker: Annotated[
        list[str], typer.Option("--ticker", "-t", help="Exchange ticker to fetch")
    ],
    include_alpha_vantage: Annotated[
        bool, typer.Option(help="Use the configured fundamentals API")
    ] = False,
) -> None:
    """Fetch every enabled provider, archive raw JSON, and write normalized observations."""
    store = RawJsonStore()
    client = ArchivedHttpClient(store)
    providers = [YahooChartProvider(client)]
    if include_alpha_vantage:
        providers.append(AlphaVantageProvider(client, os.getenv("ALPHA_VANTAGE_API_KEY", "")))

    results, observations = asyncio.run(IngestionRunner(providers).run(ticker))
    warehouse = DuckDbWarehouse()
    warehouse.initialize()
    count = warehouse.write_observations(observations)
    summary = {
        "providers": [result.provider for result in results],
        "observations_written": count,
        "provider_issues": {
            result.provider: [issue.model_dump(mode="json") for issue in result.issues]
            for result in results
            if result.issues
        },
    }
    print(json.dumps(summary, indent=2))
