"""Strategy 2: Breakout Trading."""

from __future__ import annotations

from typing import Any, Sequence

from ..analysis import indicators as ind
from ..models import Bar, Direction, Signal
from .base import MarketView, OpenPositionState, ParamSpec, Strategy, atr_rank, common_indicators


class BreakoutStrategy(Strategy):
    key = "breakout"
    name = "Breakout"
    description = (
        "Buys when price closes above the high of a previously established range on expanding "
        "volume, provided the move is not already extended and there is room before the next resistance."
    )
    entry_rules = [
        "Close is above the highest high of the previous `lookback` bars (the breakout level).",
        "Relative volume (today vs 20-bar average) is at least `min_rel_volume`.",
        "Close is no more than `max_extension_atr` ATRs above the breakout level.",
        "Distance to the next resistance (swing highs of the last 120 bars) is at least 2x the stop distance, or no resistance is above.",
        "Volatility is not in the extreme top of its 120-bar range.",
    ]
    exit_rules = [
        "Protective stop: just below the breakout level (level minus `stop_buffer_atr` ATR), but never closer than 1 ATR from entry.",
        "Profit target: the lower of (entry + `target_atr` ATR) and the next resistance level (if it offers at least 2x the stop distance).",
        "Failed breakout: a close back below the breakout level exits at the next open.",
        "Time stop: exit after `max_holding_bars` bars.",
    ]
    unfavourable_regimes = ["range_bound", "trending_down"]
    param_specs = [
        ParamSpec("lookback", "Range lookback (bars)", "int", 20, 5, 120, 1, "Bars that define the range being broken."),
        ParamSpec("min_rel_volume", "Min relative volume", "float", 1.3, 0.5, 5.0, 0.1, "Volume confirmation."),
        ParamSpec("max_extension_atr", "Max extension (ATR)", "float", 1.0, 0.1, 3.0, 0.1, "How far above the level is still acceptable."),
        ParamSpec("stop_buffer_atr", "Stop buffer (ATR)", "float", 0.5, 0.1, 2.0, 0.1, "Buffer below the breakout level."),
        ParamSpec("target_atr", "Target distance (ATR)", "float", 3.5, 1.0, 10.0, 0.5, "Profit target above entry."),
        ParamSpec("max_holding_bars", "Max holding bars", "int", 30, 5, 200, 1, "Time stop."),
    ]

    def warmup_bars(self, params: dict[str, Any]) -> int:
        return max(int(params["lookback"]) + 25, 130)

    def prepare(self, bars: Sequence[Bar], params: dict[str, Any]) -> dict[str, Sequence[Any]]:
        out = common_indicators(bars)
        # Highest high of the previous `lookback` bars, excluding the current bar.
        highs = out["high"]
        lb = int(params["lookback"])
        level: list[float | None] = [None] * len(bars)
        for i in range(lb, len(bars)):
            level[i] = max(highs[i - lb : i])
        out["breakout_level"] = level
        out["ema20"] = ind.ema(out["close"], 20)
        return out

    def entry_signal(self, view: MarketView, params: dict[str, Any]) -> Signal | None:
        level, atr, rel_vol = view.value("breakout_level"), view.value("atr14"), view.value("rel_volume20")
        if None in (level, atr) or atr <= 0:
            return None
        close = view.close
        if close <= level:
            return None
        evidence = [f"Close {close:.2f} is above the {params['lookback']}-bar high of {level:.2f}."]
        if rel_vol is None or rel_vol < params["min_rel_volume"]:
            return None
        evidence.append(f"Relative volume {rel_vol:.2f}x confirms participation (min {params['min_rel_volume']}x).")
        extension = (close - level) / atr
        if extension > params["max_extension_atr"]:
            return None
        evidence.append(f"Only {extension:.2f} ATR above the level (limit {params['max_extension_atr']}), not extended.")
        rank = atr_rank(view)
        if rank is not None and rank >= 0.9:
            return None
        stop = min(level - params["stop_buffer_atr"] * atr, close - 1.0 * atr)
        risk = close - stop
        _, resistances = ind.support_resistance(view.bars(120), lookback=120)
        above = [r for r in resistances if r > close * 1.002]
        target = close + params["target_atr"] * atr
        if above:
            nearest = min(above)
            room = nearest - close
            if room < 2.0 * risk:
                return None  # not enough room before resistance
            evidence.append(f"Next resistance {nearest:.2f} leaves {room / risk:.1f}x the stop distance as room.")
            target = min(target, nearest)
        else:
            evidence.append("No swing resistance above within the last 120 bars (price in open territory).")
        failure = [
            "False breakouts are common: price can close back inside the range within a few bars.",
            "A volume spike can mark climactic buying rather than the start of a move.",
            "Overnight gaps can fill at prices worse than the stop.",
        ]
        strength = min(1.0, 0.4 * min(rel_vol / 3, 1) + 0.6 * max(0.0, 1 - extension / params["max_extension_atr"]))
        return Signal(
            strategy=self.key, direction=Direction.LONG, entry_price=close, stop_price=stop, target_price=target,
            entry_conditions=f"Buy at next open after a confirmed close above {level:.2f}.", evidence=evidence,
            failure_reasons=failure, max_holding_bars=int(params["max_holding_bars"]), strength=strength,
            extra={"breakout_level": level},
        )

    def exit_signal(self, view: MarketView, position: OpenPositionState, params: dict[str, Any]) -> str | None:
        level = position.extra.get("breakout_level")
        if level is not None and view.close < level:
            return "Failed breakout: close back below the breakout level."
        return None
