"""Historical evidence for a (dataset, strategy, params) combination.

The scanner needs to know how a strategy has behaved on an asset *before*
the current bar. We run an in-sample / out-of-sample backtest on the history
up to the as-of bar (so no future data leaks into the ranking) and cache it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..backtest import BacktestConfig, compute_metrics, run_backtest
from ..models import AssetClass, Bar
from ..risk import RiskSettings
from ..strategies import Strategy

EVIDENCE_BALANCE = 10_000.0  # a large balance so sizing never limits the historical evidence


@dataclass
class StrategyEvidence:
    trades_total: int
    trades_oos: int
    expectancy_r_is: float | None
    expectancy_r_oos: float | None
    profit_factor_oos: float | None
    win_rate_oos: float | None
    max_drawdown_oos_pct: float | None
    by_regime: dict[str, dict[str, Any]]
    recent_expectancy_r: float | None  # last 10 trades overall
    deterioration: bool
    conclusive: bool
    notes: list[str] = field(default_factory=list)
    oos_trades_detail: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trades_total": self.trades_total,
            "trades_oos": self.trades_oos,
            "expectancy_r_in_sample": self.expectancy_r_is,
            "expectancy_r_out_of_sample": self.expectancy_r_oos,
            "profit_factor_out_of_sample": self.profit_factor_oos,
            "win_rate_out_of_sample": self.win_rate_oos,
            "max_drawdown_out_of_sample_pct": self.max_drawdown_oos_pct,
            "by_regime": self.by_regime,
            "recent_expectancy_r": self.recent_expectancy_r,
            "deterioration": self.deterioration,
            "conclusive": self.conclusive,
            "notes": self.notes,
        }


_cache: dict[tuple, StrategyEvidence] = {}


def gather_evidence(
    bars: Sequence[Bar],
    strategy: Strategy,
    params: dict[str, Any],
    symbol: str,
    asset_class: AssetClass,
    risk_settings: RiskSettings,
    cache_key: tuple | None = None,
    oos_fraction: float = 0.3,
) -> StrategyEvidence:
    key = cache_key or (symbol, strategy.key, tuple(sorted(params.items())), len(bars), bars[-1].ts if bars else None)
    if key in _cache:
        return _cache[key]
    merged = strategy.merge_params(params)
    n = len(bars)
    split = int(n * (1 - oos_fraction))
    notes: list[str] = []
    cfg_common = dict(starting_balance=EVIDENCE_BALANCE, params=merged, risk_settings=risk_settings)
    full = run_backtest(bars, strategy, symbol, asset_class, BacktestConfig(**cfg_common))
    is_trades = [t for t in full.trades if t.exit_ts <= bars[split - 1].ts] if split > 0 else []
    oos_trades = [t for t in full.trades if t.exit_ts > bars[split - 1].ts] if split > 0 else list(full.trades)
    oos_curve = [(ts, v) for ts, v in full.equity_curve if ts > bars[split - 1].ts] if split > 0 else full.equity_curve
    oos_start_balance = oos_curve[0][1] if oos_curve else EVIDENCE_BALANCE
    m_oos = compute_metrics(oos_trades, oos_curve, oos_start_balance)
    exp_is = (sum(t.r_multiple for t in is_trades) / len(is_trades)) if is_trades else None
    recent = full.trades[-10:]
    recent_exp = (sum(t.r_multiple for t in recent) / len(recent)) if len(recent) >= 5 else None
    overall_exp = (sum(t.r_multiple for t in full.trades) / len(full.trades)) if full.trades else None
    deterioration = bool(
        recent_exp is not None and overall_exp is not None and len(full.trades) >= 15
        and recent_exp < overall_exp - 0.3 and recent_exp < 0
    )
    conclusive = len(oos_trades) >= 10 and len(full.trades) >= 20
    if not conclusive:
        notes.append(
            f"Only {len(full.trades)} historical trade(s) ({len(oos_trades)} out-of-sample) for this strategy on this asset; "
            "historical evidence is weak."
        )
    if deterioration:
        notes.append("Recent trades have done markedly worse than the long-run average (possible deterioration).")
    ev = StrategyEvidence(
        trades_total=len(full.trades), trades_oos=len(oos_trades),
        expectancy_r_is=round(exp_is, 3) if exp_is is not None else None,
        expectancy_r_oos=m_oos["expectancy_r"], profit_factor_oos=m_oos["profit_factor"],
        win_rate_oos=m_oos["win_rate"], max_drawdown_oos_pct=m_oos["max_drawdown_pct"],
        by_regime=compute_metrics(full.trades, full.equity_curve, EVIDENCE_BALANCE)["by_regime"],
        recent_expectancy_r=round(recent_exp, 3) if recent_exp is not None else None,
        deterioration=deterioration, conclusive=conclusive, notes=notes,
        oos_trades_detail=[{"r": t.r_multiple, "win": t.net_pnl > 0, "strength": t.signal_strength} for t in oos_trades],
    )
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = ev
    return ev


def clear_evidence_cache() -> None:
    _cache.clear()
