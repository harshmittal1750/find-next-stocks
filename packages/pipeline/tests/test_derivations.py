from __future__ import annotations

from datetime import date, timedelta

import pytest
from find_next_pipeline.derivations import (
    beta,
    beta_from_closes,
    daily_returns,
    moving_average,
    pct_above_low,
    pct_below_high,
    price_change_pct,
    rsi,
    rsi_signal,
    trailing_peg,
)


def _series(values: list[float], start: date = date(2026, 1, 1)) -> dict[date, float]:
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def test_trailing_peg_divides_pe_by_growth_percent() -> None:
    # P/E 22 with 11% growth is a PEG of 2.0.
    assert trailing_peg(22.0, 0.11) == 2.0
    # The same growth already expressed as a percent must give the same answer.
    assert trailing_peg(22.0, 11.0, growth_is_fraction=False) == 2.0


def test_trailing_peg_is_undefined_without_profit_and_growth() -> None:
    assert trailing_peg(-5.0, 0.11) is None      # loss-making
    assert trailing_peg(22.0, -0.04) is None     # shrinking earnings
    assert trailing_peg(22.0, 0.0) is None       # flat
    assert trailing_peg(None, 0.11) is None
    assert trailing_peg(22.0, None) is None
    assert trailing_peg(float("nan"), 0.11) is None


def test_trailing_peg_is_capped() -> None:
    # Near-zero growth would otherwise produce an absurd number.
    assert trailing_peg(50.0, 0.000001) == 100.0


def test_daily_returns_skips_unusable_bases() -> None:
    assert daily_returns([100.0, 110.0]) == [0.10000000000000009]
    # A zero or missing base cannot produce a return.
    assert daily_returns([0.0, 110.0]) == []
    assert daily_returns([100.0]) == []


def test_beta_of_a_series_against_itself_is_one() -> None:
    returns = [0.01, -0.02, 0.03, 0.005, -0.01]
    assert beta(returns, returns) == 1.0


def test_beta_scales_with_amplitude() -> None:
    index = [0.01, -0.02, 0.03, 0.005, -0.01]
    assert beta([2 * r for r in index], index) == 2.0
    assert beta([-1 * r for r in index], index) == -1.0


def test_beta_rejects_a_flat_index() -> None:
    assert beta([0.01, 0.02], [0.0, 0.0]) is None
    assert beta([0.01], [0.01]) is None  # too few points


def test_beta_from_closes_needs_enough_overlapping_sessions() -> None:
    prices = [100.0 + i for i in range(200)]
    stock = _series(prices)
    index = _series(prices)
    assert beta_from_closes(stock, index) is not None
    # A newly listed stock with only a few sessions must not get a published beta.
    assert beta_from_closes(_series(prices[:10]), index) is None


def test_beta_from_closes_aligns_on_shared_dates() -> None:
    """A gap in one series must not shift the other's returns against it."""
    prices = [100.0 + i for i in range(200)]
    index = _series(prices)
    stock = _series(prices)
    # Remove a mid-series day from the stock only; the estimate should still work
    # because both sides are aligned on the dates they share.
    del stock[date(2026, 1, 1) + timedelta(days=100)]
    assert beta_from_closes(stock, index) is not None


def test_price_change_uses_one_series_and_two_dates() -> None:
    closes = _series([100.0, 105.0, 110.0])
    start = date(2026, 1, 1)
    assert price_change_pct(closes, start) == 10.0
    assert price_change_pct(closes, start, start + timedelta(days=1)) == 5.0


def test_price_change_falls_back_to_the_last_close_on_or_before() -> None:
    # A non-trading day resolves backwards to the previous session, not to nothing.
    closes = {date(2026, 1, 1): 100.0, date(2026, 1, 5): 120.0}
    assert price_change_pct(closes, date(2026, 1, 3), date(2026, 1, 5)) == 20.0


def test_same_day_baseline_returns_none_not_zero() -> None:
    """Regression: the shipped snapshot has price_chg_pct == 0.0 for all 1,353 stocks.

    A run compared its own archived output against itself, so every stock reported a
    0.0% move — a column that looks populated and is entirely meaningless. Refusing to
    answer is the honest result.
    """
    closes = _series([100.0, 105.0])
    same_day = date(2026, 1, 2)
    assert price_change_pct(closes, same_day, same_day) is None


def test_split_cannot_produce_a_phantom_crash() -> None:
    """Regression: JLHL was reported at -79.6% on a day it actually rose 1.8%.

    A 1:5 split leaves the old snapshot holding a pre-split price while the history is
    restated post-split. Because this function takes a single adjusted series, the two
    bases can never be mixed.
    """
    # Adjusted history across a 1:5 split: the level is continuous, the change is real.
    adjusted = {
        date(2026, 7, 22): 312.0,
        date(2026, 8, 20): 317.7,
    }
    change = price_change_pct(adjusted, date(2026, 7, 22))
    assert change is not None
    assert 1.0 < change < 2.5          # ~+1.8%, the truth
    assert change > -50                # emphatically not -79.6%


def test_price_change_handles_missing_and_empty_input() -> None:
    assert price_change_pct({}, date(2026, 1, 1)) is None
    # A baseline earlier than every observation cannot be resolved.
    closes = _series([100.0, 105.0])
    assert price_change_pct(closes, date(2025, 1, 1), date(2025, 6, 1)) is None


# Wilder's worked series from New Concepts in Technical Trading Systems.
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
]


def test_rsi_uses_the_sma_seeded_wilder_convention() -> None:
    """Pins which RSI we compute — there are two in the wild and they disagree badly.

    Seeding with a 14-period SMA then smoothing (what charts do) gives 57.92 here.
    Running an EWM from the first change instead — the naive pandas one-liner — gives
    43.2 on the same closes. A 15-point gap straddles the 30/70 lines, so picking the
    wrong one would mislabel stocks as oversold.
    """
    assert rsi(WILDER_CLOSES) == pytest.approx(57.92, abs=0.02)


def test_rsi_needs_more_than_period_closes() -> None:
    assert rsi([1.0] * 14) is None
    assert rsi([]) is None


def test_monotonic_series_pin_the_bounds() -> None:
    assert rsi([float(i) for i in range(1, 40)]) == 100.0
    assert rsi([float(i) for i in range(40, 1, -1)]) == 0.0


def test_flat_series_is_neutral_not_a_divide_by_zero() -> None:
    assert rsi([50.0] * 40) == 50.0


def test_warmup_length_changes_the_answer() -> None:
    """Why the full series is passed: a short window gives a different number."""
    long_series = [float(50 + (i % 7) - 3) for i in range(200)]
    assert rsi(long_series) != rsi(long_series[-15:])


def test_signal_thresholds() -> None:
    assert rsi_signal(22.0) == "oversold"
    assert rsi_signal(30.0) == "oversold"
    assert rsi_signal(55.0) == "neutral"
    assert rsi_signal(70.0) == "overbought"
    assert rsi_signal(None) is None


def test_moving_average_uses_the_most_recent_window() -> None:
    closes = [float(i) for i in range(1, 11)]      # 1..10
    assert moving_average(closes, 3) == 9.0        # mean of 8, 9, 10
    assert moving_average(closes, 10) == 5.5
    # A stock that has not traded long enough gets no average, not a short one.
    assert moving_average(closes, 11) is None


def test_pct_below_high_and_above_low() -> None:
    closes = [100.0, 150.0, 120.0]                 # peak 150, trough 100, last 120
    assert pct_below_high(closes) == 20.0
    assert pct_above_low(closes) == 20.0


def test_at_the_high_is_zero_below_it() -> None:
    closes = [50.0, 80.0, 100.0]
    assert pct_below_high(closes) == 0.0
    assert pct_above_low(closes) == 100.0


def test_window_limits_the_lookback() -> None:
    """An all-time high outside the window must not count as the 52-week high."""
    closes = [999.0] + [100.0] * 300
    assert pct_below_high(closes, window=252) == 0.0


def test_price_extremes_handle_empty_input() -> None:
    assert pct_below_high([]) is None
    assert pct_above_low([]) is None
    assert moving_average([], 5) is None
