"""Strategy 1: Trend Following."""

from __future__ import annotations

from typing import Any, Sequence

from ..analysis import indicators as ind
from ..models import Bar, Direction, Signal
from .base import MarketView, OpenPositionState, ParamSpec, Strategy, atr_rank, common_indicators


class TrendFollowingStrategy(Strategy):
    key = "trend_following"
    name = "Trend Following"
    description = (
        "Buys assets in an established, orderly uptrend and holds while the trend persists. "
        "It deliberately avoids entering after a sharp, extended move."
    )
    entry_rules = [
        "Fast EMA is above slow EMA and price closes above both (uptrend structure).",
        "The slow EMA is rising over the last 10 bars.",
        "Trend strength (20-bar efficiency ratio) is at least the configured minimum.",
        "Price is not more than `max_extension_atr` ATRs above the fast EMA (no chasing).",
        "Volatility is not in the extreme top of its 120-bar range.",
    ]
    exit_rules = [
        "Protective stop: entry minus `stop_atr` ATRs (filled with slippage, may gap through).",
        "Profit target: entry plus `target_atr` ATRs.",
        "Trend break: fast EMA crosses below slow EMA, exit at next open.",
        "Time stop: exit after `max_holding_bars` bars.",
    ]
    unfavourable_regimes = ["range_bound", "trending_down", "high_volatility"]
    param_specs = [
        ParamSpec("fast_ema", "Fast EMA period", "int", 20, 5, 60, 1, "Shorter average that reacts quickly."),
        ParamSpec("slow_ema", "Slow EMA period", "int", 50, 20, 200, 1, "Longer average that defines the trend."),
        ParamSpec("min_trend_strength", "Min trend strength", "float", 0.30, 0.0, 1.0, 0.05,
                  "20-bar efficiency ratio required (1 = perfectly straight move)."),
        ParamSpec("max_extension_atr", "Max extension (ATR)", "float", 2.0, 0.5, 5.0, 0.25,
                  "Reject entries more than this many ATRs above the fast EMA."),
        ParamSpec("stop_atr", "Stop distance (ATR)", "float", 2.0, 0.5, 5.0, 0.25, "Initial stop below entry."),
        ParamSpec("target_atr", "Target distance (ATR)", "float", 4.5, 1.0, 10.0, 0.5, "Profit target above entry."),
        ParamSpec("max_holding_bars", "Max holding bars", "int", 40, 5, 200, 1, "Time stop."),
    ]

    def warmup_bars(self, params: dict[str, Any]) -> int:
        return max(int(params["slow_ema"]) + 12, 130)

    def prepare(self, bars: Sequence[Bar], params: dict[str, Any]) -> dict[str, Sequence[Any]]:
        out = common_indicators(bars)
        closes = out["close"]
        out["ema_fast"] = ind.ema(closes, int(params["fast_ema"]))
        out["ema_slow"] = ind.ema(closes, int(params["slow_ema"]))
        out["trend_strength20"] = ind.trend_strength(closes, 20)
        out["mom20"] = ind.momentum(closes, 20)
        return out

    def entry_signal(self, view: MarketView, params: dict[str, Any]) -> Signal | None:
        fast, slow, atr = view.value("ema_fast"), view.value("ema_slow"), view.value("atr14")
        ts = view.value("trend_strength20")
        if None in (fast, slow, atr, ts) or atr <= 0:
            return None
        slow_prev = view.value("ema_slow", -11) if len(view) > 11 else None
        if slow_prev is None:
            return None
        close = view.close
        evidence: list[str] = []
        if not (fast > slow and close > fast):
            return None
        evidence.append(f"Fast EMA {fast:.2f} is above slow EMA {slow:.2f}; price {close:.2f} is above both.")
        slope = slow / slow_prev - 1
        if slope <= 0:
            return None
        evidence.append(f"Slow EMA is rising ({slope:+.2%} over 10 bars).")
        if ts < params["min_trend_strength"]:
            return None
        evidence.append(f"Trend strength {ts:.2f} meets the minimum {params['min_trend_strength']:.2f}.")
        extension = (close - fast) / atr
        if extension > params["max_extension_atr"]:
            return None
        evidence.append(f"Price is {extension:.2f} ATR above the fast EMA (limit {params['max_extension_atr']}), so not chasing.")
        rank = atr_rank(view)
        if rank is not None and rank >= 0.9:
            return None
        if rank is not None:
            evidence.append(f"Volatility rank {rank:.0%} is below the 90% extreme.")
        stop = close - params["stop_atr"] * atr
        target = close + params["target_atr"] * atr
        failure = [
            "Trends can reverse abruptly; a gap below the stop would produce a larger loss than planned.",
            "Trend-following has long losing streaks in range-bound markets.",
            "A rising fast EMA can lag a top by several bars, so the trend-break exit gives back profit.",
        ]
        strength = min(1.0, 0.5 * ts + 0.5 * max(0.0, 1 - extension / params["max_extension_atr"]))
        return Signal(
            strategy=self.key, direction=Direction.LONG, entry_price=close, stop_price=stop, target_price=target,
            entry_conditions="Buy at next open while the uptrend structure holds.", evidence=evidence,
            failure_reasons=failure, max_holding_bars=int(params["max_holding_bars"]), strength=strength,
        )

    def exit_signal(self, view: MarketView, position: OpenPositionState, params: dict[str, Any]) -> str | None:
        fast, slow = view.value("ema_fast"), view.value("ema_slow")
        if fast is not None and slow is not None and fast < slow:
            return "Trend break: fast EMA crossed below slow EMA."
        return None
