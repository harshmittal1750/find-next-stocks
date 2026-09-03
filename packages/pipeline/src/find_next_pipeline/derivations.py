"""Metrics computed from data already held, with no extra provider call.

Two of these exist because a provider will not give them to us:

* **Trailing PEG.** Yahoo's ``pegRatio`` is built from analyst *forward* growth, so it is
  blank for roughly 94% of this universe — the same names no broker covers. A trailing
  PEG from figures we already hold is a different number, and is recorded as
  ``provider="derived"`` so it is never mistaken for the provider's own.
* **Beta.** One index download serves the whole universe, versus a per-stock call.

The third, :func:`price_change_pct`, exists because getting it wrong is easy and the
failure is silent. It takes **one** close series and two dates rather than two prices,
which makes the mistake structurally impossible — see its docstring.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from statistics import fmean

__all__ = [
    "beta",
    "beta_from_closes",
    "daily_returns",
    "moving_average",
    "pct_above_low",
    "pct_below_high",
    "price_change_pct",
    "rsi",
    "rsi_signal",
    "trailing_peg",
    "window_change_pct",
]


def _finite(value: float | int | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    # NaN and the infinities are all unusable downstream.
    if numeric != numeric or numeric in {float("inf"), float("-inf")}:
        return None
    return numeric


def trailing_peg(
    trailing_pe: float | None,
    earnings_growth: float | None,
    *,
    growth_is_fraction: bool = True,
    cap: float = 100.0,
) -> float | None:
    """P/E divided by earnings growth, on a trailing basis.

    ``growth_is_fraction`` reflects how the value arrives: providers report 0.11 for 11%,
    while a normalized ``*_pct`` field carries 11.0. Passing the wrong one is a 100x
    error, so it is explicit rather than guessed.

    Returns None unless the company is both profitable and growing — a PEG built on a
    negative P/E or shrinking earnings is not a small number, it is meaningless.
    """
    pe = _finite(trailing_pe)
    growth = _finite(earnings_growth)
    if pe is None or growth is None or pe <= 0 or growth <= 0:
        return None
    growth_pct = growth * 100.0 if growth_is_fraction else growth
    if growth_pct <= 0:
        return None
    return round(min(pe / growth_pct, cap), 6)


def daily_returns(closes: Sequence[float]) -> list[float]:
    """Simple period-over-period returns, skipping non-positive bases."""
    out: list[float] = []
    for previous, current in zip(closes, closes[1:], strict=False):
        base = _finite(previous)
        value = _finite(current)
        if base is None or value is None or base <= 0:
            continue
        out.append(value / base - 1.0)
    return out


def beta(stock: Sequence[float], index: Sequence[float]) -> float | None:
    """Covariance of the two return series over the variance of the index."""
    if len(stock) != len(index) or len(stock) < 2:
        return None
    stock_mean = fmean(stock)
    index_mean = fmean(index)
    covariance = fmean(
        [(s - stock_mean) * (i - index_mean) for s, i in zip(stock, index, strict=True)]
    )
    variance = fmean([(i - index_mean) ** 2 for i in index])
    if variance == 0:
        return None
    return round(covariance / variance, 6)


def beta_from_closes(
    stock_closes: Mapping[date, float],
    index_closes: Mapping[date, float],
    *,
    min_sessions: int = 120,
) -> float | None:
    """Beta against an index, aligned on the dates both series share.

    Aligning on dates matters: a stock that was suspended or newly listed has gaps, and
    zipping two unaligned series would silently compare a Tuesday's return against a
    Thursday's. Below ``min_sessions`` overlapping days the estimate is too noisy to
    publish, so None is returned rather than a number nobody should trust.
    """
    shared = sorted(set(stock_closes) & set(index_closes))
    if len(shared) < min_sessions + 1:
        return None
    stock_series = [stock_closes[day] for day in shared]
    index_series = [index_closes[day] for day in shared]

    pairs = [
        (s_prev, s_curr, i_prev, i_curr)
        for s_prev, s_curr, i_prev, i_curr in zip(
            stock_series, stock_series[1:], index_series, index_series[1:], strict=False
        )
        if _finite(s_prev) and _finite(i_prev) and s_prev > 0 and i_prev > 0
    ]
    if len(pairs) < min_sessions:
        return None
    stock_returns = [curr / prev - 1.0 for prev, curr, _, _ in pairs]
    index_returns = [curr / prev - 1.0 for _, _, prev, curr in pairs]
    return beta(stock_returns, index_returns)


def price_change_pct(
    closes: Mapping[date, float],
    since: date,
    until: date | None = None,
) -> float | None:
    """Percentage change between two dates of **one** close series.

    Deliberately not `(new_price - old_price) / old_price` over two snapshots. Snapshot
    prices are as-quoted on their day and are never restated, while a price history is
    rewritten by the provider after a split or bonus. Dividing one by the other turns a
    corporate action into a phantom crash: a 1:5 split reads as -80%, and in the previous
    generation of this pipeline one stock was reported at -79.6% on a day it actually
    rose 1.8%.

    Taking a single series and two dates removes the opportunity to mix bases. Pass the
    adjusted series (``price_bars.adjusted_close``) and both ends are on the same footing.

    Returns None when the two dates resolve to the same observation — a same-day baseline
    yields 0.0% for every stock, which looks like a working column full of real zeros.
    """
    if not closes:
        return None
    end = until or max(closes)

    def _on_or_before(target: date) -> tuple[date, float] | None:
        candidates = [day for day in closes if day <= target]
        if not candidates:
            return None
        day = max(candidates)
        value = _finite(closes[day])
        return None if value is None else (day, value)

    start_point = _on_or_before(since)
    end_point = _on_or_before(end)
    if start_point is None or end_point is None:
        return None

    start_day, start_price = start_point
    end_day, end_price = end_point
    if start_day == end_day or start_price <= 0:
        return None
    return round((end_price / start_price - 1.0) * 100.0, 4)


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder's RSI over the full series.

    Wilder smoothing is recursive, so the result depends on how much history you feed
    it: 15 closes gives a number that will not match any chart. Pass everything you
    have — a year of dailies converges.
    """
    values = [c for c in (_finite(x) for x in closes) if c is not None]
    if len(values) < period + 1:
        return None

    seed = list(zip(values[:period], values[1 : period + 1], strict=True))
    avg_gain = sum(max(b - a, 0.0) for a, b in seed) / period
    avg_loss = sum(max(a - b, 0.0) for a, b in seed) / period

    for a, b in zip(values[period:-1], values[period + 1 :], strict=True):
        change = b - a
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def rsi_signal(value: float | None, low: float = 30.0, high: float = 70.0) -> str | None:
    if value is None:
        return None
    return "oversold" if value <= low else "overbought" if value >= high else "neutral"


def moving_average(closes: Sequence[float], period: int) -> float | None:
    """Mean of the last `period` closes. None if the stock has not traded that long."""
    values = [c for c in (_finite(x) for x in closes) if c is not None]
    if len(values) < period:
        return None
    return round(fmean(values[-period:]), 4)


def pct_below_high(closes: Sequence[float], window: int = 252) -> float | None:
    """How far below its window high the last close sits, as a positive percent.

    Computed on closing prices, so it will read slightly lower than a provider's
    52-week figure, which uses intraday highs. Consistency with `price_bars` matters
    more here than matching Yahoo to the decimal: every price factor in the model reads
    the same series.
    """
    values = [c for c in (_finite(x) for x in closes) if c is not None][-window:]
    if not values:
        return None
    peak = max(values)
    if peak <= 0:
        return None
    return round((peak - values[-1]) / peak * 100.0, 4)


def pct_above_low(closes: Sequence[float], window: int = 252) -> float | None:
    """How far above its window low the last close sits, as a positive percent."""
    values = [c for c in (_finite(x) for x in closes) if c is not None][-window:]
    if not values:
        return None
    trough = min(values)
    if trough <= 0:
        return None
    return round((values[-1] - trough) / trough * 100.0, 4)


def window_change_pct(closes: Sequence[float], window: int = 252) -> float | None:
    """Total return across the last `window` sessions, as a percent.

    Both ends come from the same adjusted series, so a split cannot manufacture a move —
    the same reason price_change_pct takes one series rather than two prices.
    """
    values = [c for c in (_finite(x) for x in closes) if c is not None][-window:]
    if len(values) < 2 or values[0] <= 0:
        return None
    return round((values[-1] / values[0] - 1.0) * 100.0, 4)
