"""Technical indicators implemented in plain, testable Python.

Every function takes a list of floats (or Bars) and returns a list aligned
to the input, with ``None`` where the indicator is not yet defined. Each
value at index ``i`` uses only data at indices ``<= i``, which is what makes
the backtester free of look-ahead bias.
"""

from __future__ import annotations

import math
from typing import Sequence

from ..models import Bar

Series = list[float | None]


def sma(values: Sequence[float], period: int) -> Series:
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """Wilder's RSI."""
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def true_range(bars: Sequence[Bar]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Series:
    """Wilder's Average True Range."""
    tr = true_range(bars)
    out: Series = [None] * len(bars)
    if len(bars) < period:
        return out
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(bars)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def returns(values: Sequence[float], period: int = 1) -> Series:
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        out[i] = (values[i] / base - 1.0) if base else None
    return out


def momentum(values: Sequence[float], period: int = 20) -> Series:
    """Rate of change over ``period`` bars (same as returns, named for clarity)."""
    return returns(values, period)


def rolling_std(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / (period - 1 if period > 1 else 1)
        out[i] = math.sqrt(var)
    return out


def historical_volatility(values: Sequence[float], period: int = 20, periods_per_year: int = 252) -> Series:
    """Annualised standard deviation of log returns."""
    log_rets: list[float] = [0.0]
    for i in range(1, len(values)):
        log_rets.append(math.log(values[i] / values[i - 1]) if values[i - 1] > 0 and values[i] > 0 else 0.0)
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        window = log_rets[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / max(1, period - 1)
        out[i] = math.sqrt(var) * math.sqrt(periods_per_year)
    return out


def rolling_max(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1 : i + 1])
    return out


def rolling_min(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = min(values[i - period + 1 : i + 1])
    return out


def relative_volume(volumes: Sequence[float], period: int = 20) -> Series:
    """Today's volume divided by the average of the previous ``period`` bars."""
    out: Series = [None] * len(volumes)
    for i in range(period, len(volumes)):
        window = volumes[i - period : i]
        avg = sum(window) / period
        out[i] = (volumes[i] / avg) if avg > 0 else None
    return out


def breakout_level(highs: Sequence[float], period: int, idx: int) -> float | None:
    """Highest high of the ``period`` bars strictly before ``idx``."""
    if idx - period < 0:
        return None
    return max(highs[idx - period : idx])


def support_resistance(bars: Sequence[Bar], lookback: int = 60, swing: int = 3) -> tuple[list[float], list[float]]:
    """Swing-point based support (lows) and resistance (highs) within the lookback.

    A swing high is a bar whose high exceeds the highs of ``swing`` bars on
    both sides; swing lows are symmetric. Levels are clustered within 1%.
    Uses only bars up to the end of the provided sequence.
    """
    n = len(bars)
    start = max(0, n - lookback)
    highs: list[float] = []
    lows: list[float] = []
    for i in range(start + swing, n - swing):
        window = bars[i - swing : i + swing + 1]
        if bars[i].high >= max(b.high for b in window):
            highs.append(bars[i].high)
        if bars[i].low <= min(b.low for b in window):
            lows.append(bars[i].low)

    def cluster(levels: list[float]) -> list[float]:
        levels = sorted(levels)
        clustered: list[float] = []
        for lv in levels:
            if clustered and abs(lv - clustered[-1]) / clustered[-1] < 0.01:
                clustered[-1] = (clustered[-1] + lv) / 2
            else:
                clustered.append(lv)
        return clustered

    return cluster(lows), cluster(highs)


def efficiency_ratio(values: Sequence[float], period: int = 20) -> Series:
    """Kaufman efficiency ratio: net move / sum of absolute moves (0..1).

    Close to 1 means a clean directional trend; close to 0 means choppy.
    """
    out: Series = [None] * len(values)
    for i in range(period, len(values)):
        net = abs(values[i] - values[i - period])
        path = sum(abs(values[j] - values[j - 1]) for j in range(i - period + 1, i + 1))
        out[i] = (net / path) if path > 0 else 0.0
    return out


def linear_slope(values: Sequence[float], period: int = 20) -> Series:
    """Least-squares slope of the last ``period`` closes, normalised by price (per bar)."""
    out: Series = [None] * len(values)
    xs = list(range(period))
    x_mean = sum(xs) / period
    denom = sum((x - x_mean) ** 2 for x in xs)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        y_mean = sum(window) / period
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, window))
        slope = num / denom if denom else 0.0
        out[i] = slope / y_mean if y_mean else 0.0
    return out


def trend_strength(values: Sequence[float], period: int = 20) -> Series:
    """Signed trend strength in [-1, 1]: efficiency ratio times trend direction."""
    er = efficiency_ratio(values, period)
    sl = linear_slope(values, period)
    out: Series = [None] * len(values)
    for i in range(len(values)):
        if er[i] is None or sl[i] is None:
            continue
        sign = 1.0 if sl[i] > 0 else (-1.0 if sl[i] < 0 else 0.0)
        out[i] = er[i] * sign
    return out


def percentile_rank(values: Sequence[float | None], period: int = 120) -> Series:
    """Rank of the current value within the trailing window (0..1)."""
    out: Series = [None] * len(values)
    for i in range(len(values)):
        cur = values[i]
        if cur is None:
            continue
        window = [v for v in values[max(0, i - period + 1) : i + 1] if v is not None]
        if len(window) < max(10, period // 3):
            continue
        below = sum(1 for v in window if v < cur)
        equal = sum(1 for v in window if v == cur)
        # Ties count half, so a perfectly constant series ranks in the middle (0.5), not at 0.
        out[i] = (below + 0.5 * equal) / len(window)
    return out


def max_drawdown(equity: Sequence[float]) -> tuple[float, int]:
    """Return (max drawdown as a fraction, length in bars of the longest drawdown)."""
    peak = -math.inf
    mdd = 0.0
    longest = 0
    current_len = 0
    for v in equity:
        if v >= peak:
            peak = v
            current_len = 0
        else:
            current_len += 1
            longest = max(longest, current_len)
            if peak > 0:
                mdd = max(mdd, (peak - v) / peak)
    return mdd, longest
