"""Strategy 4: Momentum Continuation."""

from __future__ import annotations

from typing import Any, Sequence

from ..analysis import indicators as ind
from ..models import Bar, Direction, Signal
from .base import MarketView, OpenPositionState, ParamSpec, Strategy, atr_rank, common_indicators


class MomentumStrategy(Strategy):
    key = "momentum"
    name = "Momentum Continuation"
    description = (
        "Buys assets whose medium-term momentum is strong and still improving, provided the move "
        "is not exhausted (RSI not overbought) and volatility is manageable."
    )
    entry_rules = [
        "`long_period`-bar return is at least `min_long_return` and the `short_period`-bar return is positive.",
        "Price is above the 20-bar EMA, which is above the 50-bar EMA (aligned momentum).",
        "RSI(14) is between `rsi_low` and `rsi_high` (strong but not exhausted).",
        "The short-period return is below `max_short_return` (not a parabolic spike).",
        "Volatility rank is below 85% of its 120-bar range.",
    ]
    exit_rules = [
        "Protective stop: entry minus `stop_atr` ATRs.",
        "Profit target: entry plus `target_atr` ATRs.",
        "Momentum loss: the short-period return turns negative, exit at next open.",
        "Time stop: exit after `max_holding_bars` bars.",
    ]
    unfavourable_regimes = ["range_bound", "trending_down", "high_volatility"]
    param_specs = [
        ParamSpec("long_period", "Long momentum period", "int", 60, 20, 250, 1, "Bars for the medium-term return."),
        ParamSpec("short_period", "Short momentum period", "int", 20, 5, 60, 1, "Bars for the recent return."),
        ParamSpec("min_long_return", "Min long return", "float", 0.08, 0.0, 1.0, 0.01, "e.g. 0.08 = +8% over the long period."),
        ParamSpec("max_short_return", "Max short return", "float", 0.20, 0.02, 1.0, 0.01, "Reject parabolic moves above this."),
        ParamSpec("rsi_low", "RSI lower bound", "float", 52.0, 30.0, 70.0, 1.0, ""),
        ParamSpec("rsi_high", "RSI upper bound", "float", 72.0, 55.0, 95.0, 1.0, ""),
        ParamSpec("stop_atr", "Stop distance (ATR)", "float", 2.5, 0.5, 6.0, 0.25, ""),
        ParamSpec("target_atr", "Target distance (ATR)", "float", 5.5, 1.0, 12.0, 0.5, ""),
        ParamSpec("max_holding_bars", "Max holding bars", "int", 40, 5, 200, 1, "Time stop."),
    ]

    def warmup_bars(self, params: dict[str, Any]) -> int:
        return max(int(params["long_period"]) + 5, 130)

    def prepare(self, bars: Sequence[Bar], params: dict[str, Any]) -> dict[str, Sequence[Any]]:
        out = common_indicators(bars)
        closes = out["close"]
        out["ret_long"] = ind.returns(closes, int(params["long_period"]))
        out["ret_short"] = ind.returns(closes, int(params["short_period"]))
        out["ema20"] = ind.ema(closes, 20)
        out["ema50"] = ind.ema(closes, 50)
        return out

    def entry_signal(self, view: MarketView, params: dict[str, Any]) -> Signal | None:
        rl, rs, ema20, ema50 = view.value("ret_long"), view.value("ret_short"), view.value("ema20"), view.value("ema50")
        atr, rsi = view.value("atr14"), view.value("rsi14")
        if None in (rl, rs, ema20, ema50, atr, rsi) or atr <= 0:
            return None
        close = view.close
        if rl < params["min_long_return"] or rs <= 0:
            return None
        evidence = [f"{params['long_period']}-bar return {rl:+.1%} (min {params['min_long_return']:+.0%}); {params['short_period']}-bar return {rs:+.1%}."]
        if not (close > ema20 > ema50):
            return None
        evidence.append(f"Price {close:.2f} > EMA20 {ema20:.2f} > EMA50 {ema50:.2f}: averages are aligned.")
        if not (params["rsi_low"] <= rsi <= params["rsi_high"]):
            return None
        evidence.append(f"RSI(14) {rsi:.0f} is strong but not exhausted ({params['rsi_low']:.0f}-{params['rsi_high']:.0f}).")
        if rs > params["max_short_return"]:
            return None
        evidence.append(f"Recent return {rs:+.1%} is below the parabolic limit of {params['max_short_return']:+.0%}.")
        rank = atr_rank(view)
        if rank is not None and rank >= 0.85:
            return None
        if rank is not None:
            evidence.append(f"Volatility rank {rank:.0%} is manageable.")
        stop = close - params["stop_atr"] * atr
        target = close + params["target_atr"] * atr
        failure = [
            "Momentum crashes: strong winners can reverse sharply when the broader market turns.",
            "Crowded momentum names have wider spreads and worse fills in stress.",
            "The wider stop means a smaller position; costs eat a larger share of the planned risk.",
        ]
        strength = min(1.0, 0.6 * min(rl / 0.3, 1.0) + 0.4 * (1 - rs / params["max_short_return"]))
        return Signal(
            strategy=self.key, direction=Direction.LONG, entry_price=close, stop_price=stop, target_price=target,
            entry_conditions="Buy at next open while momentum is positive and aligned.", evidence=evidence,
            failure_reasons=failure, max_holding_bars=int(params["max_holding_bars"]), strength=strength,
        )

    def exit_signal(self, view: MarketView, position: OpenPositionState, params: dict[str, Any]) -> str | None:
        rs = view.value("ret_short")
        if rs is not None and rs < 0:
            return "Momentum loss: short-period return turned negative."
        return None
