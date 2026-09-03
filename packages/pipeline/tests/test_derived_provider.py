import asyncio
from datetime import date, timedelta

import pytest
from find_next_pipeline.providers.derived import DerivedMetricsProvider

# Same worked series used to pin derivations.rsi() itself — RSI(14) here is 57.92.
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
]


class FakeClosesWarehouse:
    def __init__(self, closes_by_ticker: dict[str, list[float]]) -> None:
        self.closes_by_ticker = closes_by_ticker
        self.requested: list[str] | None = None

    def read_dated_closes_bulk(self, tickers: list[str]) -> dict[str, dict[date, float]]:
        self.requested = tickers
        # Series are dated backwards from a fixed day so every ticker shares a calendar:
        # beta aligns on the dates two series have in common, and unrelated synthetic
        # dates per ticker would leave nothing to intersect.
        anchor = date(2026, 1, 1)
        return {
            ticker: {
                anchor + timedelta(days=offset): close for offset, close in enumerate(closes)
            }
            for ticker, closes in self.closes_by_ticker.items()
            if ticker in tickers
        }


def test_derived_provider_emits_rsi_and_signal_from_stored_closes() -> None:
    warehouse = FakeClosesWarehouse({"GALLANTT": WILDER_CLOSES})
    provider = DerivedMetricsProvider(warehouse)

    result = asyncio.run(provider.fetch(["GALLANTT"]))

    by_field = {item.field: item for item in result.observations}
    assert by_field["rsi_14"].value == 57.92
    assert by_field["rsi_14"].provider == "derived"
    assert by_field["rsi_14_signal"].value == "neutral"
    # Beta is absent because this fake carries no benchmark, which is its own issue;
    # what matters here is that the RSI path reported nothing wrong.
    assert not [i for i in result.issues if i.code != "missing_benchmark_history"]


def test_derived_provider_reports_a_gap_instead_of_a_wrong_zero() -> None:
    """Fewer than period+1 closes means no yahoo history yet — not RSI 0."""
    warehouse = FakeClosesWarehouse({"NEWCO": [10.0, 10.5]})
    provider = DerivedMetricsProvider(warehouse)

    result = asyncio.run(provider.fetch(["NEWCO"]))

    assert result.observations == []
    gaps = [i for i in result.issues if i.code == "insufficient_price_history"]
    assert len(gaps) == 1
    assert gaps[0].raw_value == "NEWCO"


def test_short_history_still_yields_what_it_can() -> None:
    """A recently listed stock has no 200-day average but does have RSI and 52w figures.

    Each derivation declines independently; one missing value must not suppress the rest.
    """
    closes = [100.0 + (i % 5) for i in range(60)]
    provider = DerivedMetricsProvider(FakeClosesWarehouse({"NEWCO": closes}))
    result = asyncio.run(provider.fetch(["NEWCO"]))
    fields = {o.field for o in result.observations}
    assert "rsi_14" in fields
    assert "fiftyDayAverage" in fields
    assert "twoHundredDayAverage" not in fields   # only 60 sessions


def test_price_derivations_are_emitted_for_a_full_history() -> None:
    closes = [100.0 + (i % 7) for i in range(260)]
    provider = DerivedMetricsProvider(FakeClosesWarehouse({"AAA": closes}))
    result = asyncio.run(provider.fetch(["AAA"]))
    by_field = {o.field: o for o in result.observations}
    assert by_field["twoHundredDayAverage"].unit == "INR"
    # 52-week distances are intentionally absent: repository.py and scoring.py both
    # already derive them, the latter with the model's drawdown cap.
    assert "pct_below_52w_high" not in by_field


def test_price_change_is_a_fixed_window_not_since_last_run() -> None:
    """Regression: "since the previous run" gave 0.0% for all 1,353 stocks.

    A run compared its own archived output against itself. A fixed session window has
    no baseline to collapse onto.
    """
    closes = [100.0] * 250 + [110.0]          # flat, then a jump on the last session
    provider = DerivedMetricsProvider(FakeClosesWarehouse({"AAA": closes}))
    by_field = {o.field: o.value for o in asyncio.run(provider.fetch(["AAA"])).observations}
    assert by_field["price_chg_pct"] == 10.0          # 5 sessions back was 100
    assert by_field["fiftyTwoWeekChangePercent"] == 10.0


def _levered_series(index_closes: list[float], leverage: float) -> list[float]:
    """A price series whose every daily return is `leverage` x the index's."""
    out = [100.0]
    for prev, curr in zip(index_closes, index_closes[1:], strict=False):
        out.append(out[-1] * (1.0 + leverage * (curr / prev - 1.0)))
    return out


def test_beta_measures_moves_against_the_benchmark() -> None:
    # A deterministic non-flat index: a constant series has zero variance and beta is
    # undefined, so the test would pass on a broken implementation.
    index = [1000.0]
    for i in range(200):
        index.append(index[-1] * (1.0 + (0.01 if i % 3 else -0.008)))

    warehouse = FakeClosesWarehouse(
        {
            "TWICE": _levered_series(index, 2.0),
            "HALF": _levered_series(index, 0.5),
            "^NSEI": index,
        }
    )
    result = asyncio.run(DerivedMetricsProvider(warehouse).fetch(["TWICE", "HALF"]))
    betas = {o.ticker: o.value for o in result.observations if o.field == "beta"}

    assert betas["TWICE"] == pytest.approx(2.0, abs=0.01)
    assert betas["HALF"] == pytest.approx(0.5, abs=0.01)
    # The benchmark is an input, not a stock: it must not turn up as a ranked row.
    assert "^NSEI" not in {o.ticker for o in result.observations}
    assert "^NSEI" in (warehouse.requested or []), "provider must pull the index itself"


def test_missing_benchmark_reports_a_gap_rather_than_a_silent_zero() -> None:
    warehouse = FakeClosesWarehouse({"AAA": WILDER_CLOSES})   # no ^NSEI series
    result = asyncio.run(DerivedMetricsProvider(warehouse).fetch(["AAA"]))

    assert not [o for o in result.observations if o.field == "beta"]
    assert [i.code for i in result.issues if i.code == "missing_benchmark_history"]
    # RSI and the rest still land — one missing input must not void the whole provider.
    assert "rsi_14" in {o.field for o in result.observations}
