"""Core typed data structures shared across the engine.

These are plain dataclasses so the engine has no web-framework dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    CRYPTO = "crypto"


class DataStatus(str, Enum):
    """Freshness label shown on every market-data view."""

    LIVE = "LIVE"  # real-time quotes (no free provider offers this; never fabricated)
    DELAYED = "DELAYED"  # end-of-day or delayed quotes from a provider
    HISTORICAL = "HISTORICAL"  # older historical data (imports, downloads)
    SIMULATED = "SIMULATED"  # synthetic bundled sample data
    UNAVAILABLE = "UNAVAILABLE"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"  # defined for completeness; disabled by risk settings


class Regime(str, Enum):
    TREND_UP = "trending_up"
    TREND_DOWN = "trending_down"
    RANGE = "range_bound"
    HIGH_VOL = "high_volatility"
    LOW_VOL = "low_volatility"
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. ``ts`` is a timezone-aware UTC datetime."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class Dataset:
    id: int | None
    symbol: str
    name: str
    asset_class: AssetClass
    interval: str  # e.g. "1d"
    source: str
    status: DataStatus
    retrieved_at: datetime
    timezone: str = "UTC"
    bar_count: int = 0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    quality_score: float | None = None
    quality_notes: list[str] = field(default_factory=list)
    is_sample: bool = False


@dataclass
class Signal:
    """A candidate produced by a strategy. It is NOT an order."""

    strategy: str
    direction: Direction
    entry_price: float
    stop_price: float
    target_price: float
    entry_conditions: str
    evidence: list[str] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    max_holding_bars: int | None = None
    strength: float = 0.0  # 0..1 internal ordering hint, never shown as probability
    extra: dict[str, Any] = field(default_factory=dict)  # strategy-private state for exit rules

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_price)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.target_price - self.entry_price)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
