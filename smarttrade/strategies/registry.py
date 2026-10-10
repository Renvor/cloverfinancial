"""Strategy registry. Register a new Strategy subclass here to make it available everywhere."""

from __future__ import annotations

from .base import Strategy
from .breakout import BreakoutStrategy
from .momentum import MomentumStrategy
from .pullback import PullbackStrategy
from .trend_following import TrendFollowingStrategy

STRATEGIES: dict[str, Strategy] = {
    s.key: s for s in (TrendFollowingStrategy(), BreakoutStrategy(), PullbackStrategy(), MomentumStrategy())
}


def get_strategy(key: str) -> Strategy:
    if key not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{key}'. Available: {', '.join(STRATEGIES)}")
    return STRATEGIES[key]


def list_strategies() -> list[Strategy]:
    return list(STRATEGIES.values())
