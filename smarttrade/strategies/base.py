"""Strategy interface and the look-ahead-safe ``MarketView``.

Indicators are precomputed once per dataset (each value at index ``i`` only
depends on bars ``<= i``). A ``MarketView`` then exposes bars and indicator
values **only up to the current index**; any attempt to read a future value
raises ``LookAheadError``. Strategies never see the future, by construction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..analysis import indicators as ind
from ..models import Bar, Signal


class LookAheadError(RuntimeError):
    """Raised when code tries to read data beyond the current bar."""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    type: str  # "int" | "float" | "bool"
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    description: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name, "label": self.label, "type": self.type, "default": self.default,
            "minimum": self.minimum, "maximum": self.maximum, "step": self.step,
            "description": self.description,
        }


class MarketView:
    """Read-only window onto bars/indicators that refuses to look ahead."""

    def __init__(self, bars: Sequence[Bar], indicators: dict[str, Sequence[Any]], idx: int):
        self._bars = bars
        self._ind = indicators
        self.idx = idx

    def __len__(self) -> int:
        return self.idx + 1

    def _check(self, i: int) -> int:
        if i < 0:
            i = self.idx + 1 + i
        if i > self.idx:
            raise LookAheadError(f"Attempted to read bar {i} while current bar is {self.idx}")
        return i

    def bar(self, i: int = -1) -> Bar:
        return self._bars[self._check(i)]

    def bars(self, lookback: int) -> list[Bar]:
        start = max(0, self.idx + 1 - lookback)
        return list(self._bars[start : self.idx + 1])

    def value(self, name: str, i: int = -1) -> Any:
        return self._ind[name][self._check(i)]

    def values(self, name: str, lookback: int) -> list[Any]:
        start = max(0, self.idx + 1 - lookback)
        return list(self._ind[name][start : self.idx + 1])

    @property
    def close(self) -> float:
        return self.bar().close


def build_view(bars: Sequence[Bar], indicators: dict[str, Sequence[Any]], idx: int) -> MarketView:
    return MarketView(bars, indicators, idx)


@dataclass
class OpenPositionState:
    """What a strategy is allowed to know about its open position for exit rules."""

    entry_price: float
    stop_price: float
    target_price: float
    entry_idx: int
    bars_held: int
    direction: str = "long"
    extra: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    key: str = "base"
    name: str = "Base"
    description: str = ""
    entry_rules: list[str] = []
    exit_rules: list[str] = []
    unfavourable_regimes: list[str] = []
    param_specs: list[ParamSpec] = []

    def default_params(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.param_specs}

    def merge_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.default_params()
        if params:
            for spec in self.param_specs:
                if spec.name in params and params[spec.name] is not None:
                    raw = params[spec.name]
                    if spec.type == "int":
                        merged[spec.name] = int(raw)
                    elif spec.type == "float":
                        merged[spec.name] = float(raw)
                    elif spec.type == "bool":
                        merged[spec.name] = bool(raw) if not isinstance(raw, str) else raw.lower() in ("1", "true", "yes")
        return merged

    @abstractmethod
    def warmup_bars(self, params: dict[str, Any]) -> int:
        """Number of bars required before the first signal can be evaluated."""

    @abstractmethod
    def prepare(self, bars: Sequence[Bar], params: dict[str, Any]) -> dict[str, Sequence[Any]]:
        """Precompute indicator series (each index uses only data <= that index)."""

    @abstractmethod
    def entry_signal(self, view: MarketView, params: dict[str, Any]) -> Signal | None:
        """Return a long Signal if entry conditions hold at the view's current bar."""

    @abstractmethod
    def exit_signal(self, view: MarketView, position: OpenPositionState, params: dict[str, Any]) -> str | None:
        """Return a plain-English reason to exit at the next open, or None to hold."""

    def describe(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "entry_rules": self.entry_rules,
            "exit_rules": self.exit_rules,
            "unfavourable_regimes": self.unfavourable_regimes,
            "params": [p.as_dict() for p in self.param_specs],
        }


# --------------------------------------------------------------------------- helpers
def common_indicators(bars: Sequence[Bar]) -> dict[str, Sequence[Any]]:
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]
    atr14 = ind.atr(bars, 14)
    return {
        "close": closes,
        "high": highs,
        "low": lows,
        "volume": vols,
        "atr14": atr14,
        "atr_pct": [(a / c) if (a is not None and c) else None for a, c in zip(atr14, closes)],
        "rel_volume20": ind.relative_volume(vols, 20),
        "rsi14": ind.rsi(closes, 14),
    }


def atr_rank(view: MarketView) -> float | None:
    """Percentile rank of current ATR% within the trailing 120 bars (0..1)."""
    window = [v for v in view.values("atr_pct", 120) if v is not None]
    cur = view.value("atr_pct")
    if cur is None or len(window) < 40:
        return None
    return sum(1 for v in window if v < cur) / len(window)
