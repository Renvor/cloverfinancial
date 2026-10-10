"""Explainable market-regime classifier.

Rules (evaluated on the bars available up to the analysed index only):

1. Need at least ``min_bars`` (default 120) bars, else UNCLEAR.
2. Volatility regime: ATR(14) as % of price, ranked against its trailing
   120-bar history. Rank >= 0.85 -> HIGH_VOLATILITY, rank <= 0.15 ->
   LOW_VOLATILITY. Volatility extremes take priority because they change
   how every strategy should behave.
3. Trend regime: price vs 50-bar SMA, 50-bar SMA slope over 10 bars, and the
   20-bar efficiency ratio. Price above a rising SMA (slope > +0.2%) with
   ER >= 0.3 -> TRENDING_UP; the mirror image -> TRENDING_DOWN.
4. Otherwise, if the 20-bar efficiency ratio is below 0.3 -> RANGE_BOUND.
5. Anything else -> UNCLEAR (mixed evidence is reported honestly).

All inputs are causal (each indicator value at index i depends only on bars
<= i), so ``regime_series`` can be computed once per dataset in O(n) and
used safely inside backtests without look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..models import Bar, Regime
from . import indicators as ind

REGIME_LABELS = {
    Regime.TREND_UP: "Trending up",
    Regime.TREND_DOWN: "Trending down",
    Regime.RANGE: "Range-bound",
    Regime.HIGH_VOL: "High volatility",
    Regime.LOW_VOL: "Low volatility",
    Regime.UNCLEAR: "Unclear / insufficient data",
}

REGIME_RULES_TEXT = __doc__ or ""


@dataclass
class RegimeResult:
    regime: Regime
    confidence: str  # "low" | "medium" | "high" (qualitative, not a probability)
    evidence: list[str] = field(default_factory=list)
    metrics: dict[str, float | None] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "label": REGIME_LABELS[self.regime],
            "confidence": self.confidence,
            "evidence": self.evidence,
            "metrics": self.metrics,
        }


def _classify_point(
    i: int, closes: Sequence[float], sma50: Sequence[float | None], atr_pct: Sequence[float | None],
    vol_rank: Sequence[float | None], er20: Sequence[float | None], min_bars: int,
) -> RegimeResult:
    if i + 1 < min_bars:
        return RegimeResult(
            Regime.UNCLEAR, "low",
            [f"Only {i + 1} bars available; at least {min_bars} are needed for a regime call."],
        )
    price = closes[i]
    s50 = sma50[i]
    s50_prev = sma50[i - 10] if i - 10 >= 0 else None
    slope = ((s50 / s50_prev) - 1) if (s50 and s50_prev) else None
    er = er20[i]
    vr = vol_rank[i]
    metrics = {
        "price": price, "sma50": s50, "sma50_slope_10bar": slope, "efficiency_ratio_20": er,
        "atr_pct": atr_pct[i], "atr_pct_rank_120": vr,
    }
    evidence: list[str] = []
    if vr is not None:
        evidence.append(f"Volatility (ATR as % of price) ranks at the {vr:.0%} level of the last 120 bars.")
        if vr >= 0.85:
            evidence.append("That is extreme, so volatility overrides the trend reading.")
            return RegimeResult(Regime.HIGH_VOL, "high" if vr >= 0.95 else "medium", evidence, metrics)
        if vr <= 0.15:
            evidence.append("That is unusually quiet; breakouts from low volatility are worth watching but trends are weak.")
            return RegimeResult(Regime.LOW_VOL, "high" if vr <= 0.05 else "medium", evidence, metrics)
    if s50 is None or slope is None or er is None:
        evidence.append("Trend indicators are not fully defined yet.")
        return RegimeResult(Regime.UNCLEAR, "low", evidence, metrics)
    above = price > s50
    rising = slope > 0.002
    falling = slope < -0.002
    evidence.append(
        f"Price is {'above' if above else 'below'} the 50-bar average, which is "
        f"{'rising' if rising else 'falling' if falling else 'flat'} ({slope:+.2%} over 10 bars)."
    )
    evidence.append(f"20-bar efficiency ratio is {er:.2f} (1 = clean trend, 0 = choppy).")
    if above and rising and er >= 0.3:
        return RegimeResult(Regime.TREND_UP, "high" if er >= 0.5 else "medium", evidence, metrics)
    if (not above) and falling and er >= 0.3:
        return RegimeResult(Regime.TREND_DOWN, "high" if er >= 0.5 else "medium", evidence, metrics)
    if er < 0.3:
        evidence.append("Directional movement is inefficient, consistent with a trading range.")
        return RegimeResult(Regime.RANGE, "medium" if er < 0.2 else "low", evidence, metrics)
    evidence.append("Trend direction and moving-average signals disagree, so no confident regime is assigned.")
    return RegimeResult(Regime.UNCLEAR, "low", evidence, metrics)


def _series_inputs(bars: Sequence[Bar]):
    closes = [b.close for b in bars]
    sma50 = ind.sma(closes, 50)
    atr14 = ind.atr(bars, 14)
    er20 = ind.efficiency_ratio(closes, 20)
    atr_pct = [(a / c) if (a is not None and c) else None for a, c in zip(atr14, closes)]
    vol_rank = ind.percentile_rank(atr_pct, 120)
    return closes, sma50, atr_pct, vol_rank, er20


def classify_regime(bars: Sequence[Bar], idx: int | None = None, min_bars: int = 120) -> RegimeResult:
    """Classify the regime at bar ``idx`` (default: last bar) using only bars <= idx."""
    if idx is None:
        idx = len(bars) - 1
    window = list(bars[: idx + 1])
    if len(window) < min_bars:
        return RegimeResult(
            Regime.UNCLEAR, "low",
            [f"Only {len(window)} bars available; at least {min_bars} are needed for a regime call."],
        )
    closes, sma50, atr_pct, vol_rank, er20 = _series_inputs(window)
    return _classify_point(len(window) - 1, closes, sma50, atr_pct, vol_rank, er20, min_bars)


def regime_series(bars: Sequence[Bar], min_bars: int = 120) -> list[Regime]:
    """Regime at every bar, computed once in O(n). Equivalent to calling
    ``classify_regime(bars, i)`` for every i, because every input is causal."""
    if not bars:
        return []
    closes, sma50, atr_pct, vol_rank, er20 = _series_inputs(bars)
    return [
        _classify_point(i, closes, sma50, atr_pct, vol_rank, er20, min_bars).regime
        for i in range(len(bars))
    ]
