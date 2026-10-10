"""Strategy 3: Pullback Trading."""

from __future__ import annotations

from typing import Any, Sequence

from ..analysis import indicators as ind
from ..models import Bar, Direction, Signal
from .base import MarketView, OpenPositionState, ParamSpec, Strategy, common_indicators


class PullbackStrategy(Strategy):
    key = "pullback"
    name = "Pullback in Trend"
    description = (
        "Waits for a healthy uptrend to pause and dip toward its moving average, then buys only when "
        "the price shows evidence of resuming the trend."
    )
    entry_rules = [
        "Trend quality: close above the 50-bar SMA, the SMA is rising, and the 50-bar trend strength is at least `min_trend_strength`.",
        "Pullback depth: price is between `min_pullback_pct` and `max_pullback_pct` below its 20-bar high.",
        "Price is within `ema_distance_atr` ATRs of the 20-bar EMA (the dip reached the average).",
        "RSI(14) is between `rsi_low` and `rsi_high` (pulled back, not collapsing).",
        "Resumption evidence: today's close is above yesterday's high, or the bar closes in its top 40% range with a higher close.",
    ]
    exit_rules = [
        "Protective stop: below the lowest low of the pullback (last 5 bars) minus 0.25 ATR, but at most 2 ATRs below entry.",
        "Profit target: the prior 20-bar high plus 1 ATR (measured continuation), or at least `min_target_r` times the stop distance.",
        "Trend failure: close below the 50-bar SMA exits at the next open.",
        "Time stop: exit after `max_holding_bars` bars.",
    ]
    unfavourable_regimes = ["trending_down", "high_volatility"]
    param_specs = [
        ParamSpec("min_trend_strength", "Min trend strength (50-bar)", "float", 0.25, 0.0, 1.0, 0.05, "Quality of the broader trend."),
        ParamSpec("min_pullback_pct", "Min pullback (%)", "float", 1.5, 0.5, 20.0, 0.5, "Minimum dip from the 20-bar high."),
        ParamSpec("max_pullback_pct", "Max pullback (%)", "float", 10.0, 2.0, 40.0, 0.5, "Deeper dips are treated as trend damage."),
        ParamSpec("ema_distance_atr", "Max distance to EMA20 (ATR)", "float", 1.5, 0.1, 3.0, 0.1, "How close to the average the dip must get."),
        ParamSpec("rsi_low", "RSI lower bound", "float", 35.0, 10.0, 60.0, 1.0, "Below this the dip looks like a collapse."),
        ParamSpec("rsi_high", "RSI upper bound", "float", 60.0, 40.0, 90.0, 1.0, "Above this it is not really a pullback."),
        ParamSpec("min_target_r", "Min target (x stop distance)", "float", 2.5, 1.0, 6.0, 0.25,
                  "The target is at least this many stop-distances above the entry."),
        ParamSpec("max_holding_bars", "Max holding bars", "int", 25, 5, 200, 1, "Time stop."),
    ]

    def warmup_bars(self, params: dict[str, Any]) -> int:
        return 130

    def prepare(self, bars: Sequence[Bar], params: dict[str, Any]) -> dict[str, Sequence[Any]]:
        out = common_indicators(bars)
        closes = out["close"]
        out["sma50"] = ind.sma(closes, 50)
        out["ema20"] = ind.ema(closes, 20)
        out["high20"] = ind.rolling_max(out["high"], 20)
        out["low5"] = ind.rolling_min(out["low"], 5)
        out["trend_strength50"] = ind.trend_strength(closes, 50)
        return out

    def entry_signal(self, view: MarketView, params: dict[str, Any]) -> Signal | None:
        sma50, ema20, atr, rsi = view.value("sma50"), view.value("ema20"), view.value("atr14"), view.value("rsi14")
        high20, low5, ts50 = view.value("high20"), view.value("low5"), view.value("trend_strength50")
        if None in (sma50, ema20, atr, rsi, high20, low5, ts50) or atr <= 0 or len(view) < 12:
            return None
        sma_prev = view.value("sma50", -11)
        if sma_prev is None:
            return None
        bar, prev = view.bar(), view.bar(-2)
        close = bar.close
        evidence: list[str] = []
        if not (close > sma50 and sma50 > sma_prev and ts50 >= params["min_trend_strength"]):
            return None
        evidence.append(f"Uptrend intact: close {close:.2f} above rising SMA50 {sma50:.2f}; 50-bar trend strength {ts50:.2f}.")
        pullback = (high20 - close) / high20 * 100
        if not (params["min_pullback_pct"] <= pullback <= params["max_pullback_pct"]):
            return None
        evidence.append(f"Price is {pullback:.1f}% below its 20-bar high of {high20:.2f} (allowed {params['min_pullback_pct']}-{params['max_pullback_pct']}%).")
        if abs(close - ema20) / atr > params["ema_distance_atr"]:
            return None
        evidence.append(f"Close is within {abs(close - ema20) / atr:.2f} ATR of the 20-bar EMA.")
        if not (params["rsi_low"] <= rsi <= params["rsi_high"]):
            return None
        evidence.append(f"RSI(14) at {rsi:.0f} is in the pullback zone {params['rsi_low']:.0f}-{params['rsi_high']:.0f}.")
        bar_range = bar.high - bar.low
        strong_close = bar_range > 0 and (bar.close - bar.low) / bar_range >= 0.6 and bar.close > prev.close
        if not (close > prev.high or strong_close):
            return None
        evidence.append(
            "Resumption evidence: close above the previous bar's high." if close > prev.high
            else "Resumption evidence: higher close finishing in the top 40% of the bar's range."
        )
        stop = max(low5 - 0.25 * atr, close - 2.0 * atr)
        if stop >= close:
            return None
        # Target: prior 20-bar high plus 1 ATR (measured continuation), but at least 2.5x the stop distance.
        target = max(high20 + 1.0 * atr, close + params["min_target_r"] * (close - stop))
        failure = [
            "A pullback can deepen into a trend reversal; the stop below the swing low limits but does not cap the loss.",
            "Resumption bars sometimes fail the next day (bull trap).",
            "The target at the prior high can act as resistance where sellers return.",
        ]
        depth_score = 1 - abs(pullback - (params["min_pullback_pct"] + params["max_pullback_pct"]) / 2) / max(params["max_pullback_pct"], 1)
        strength = min(1.0, max(0.0, 0.5 * ts50 + 0.5 * depth_score))
        return Signal(
            strategy=self.key, direction=Direction.LONG, entry_price=close, stop_price=stop, target_price=target,
            entry_conditions="Buy at next open after a pullback shows resumption.", evidence=evidence,
            failure_reasons=failure, max_holding_bars=int(params["max_holding_bars"]), strength=strength,
        )

    def exit_signal(self, view: MarketView, position: OpenPositionState, params: dict[str, Any]) -> str | None:
        sma50 = view.value("sma50")
        if sma50 is not None and view.close < sma50:
            return "Trend failure: close below the 50-bar SMA."
        return None
