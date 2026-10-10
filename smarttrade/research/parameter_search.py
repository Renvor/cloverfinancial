"""Strategy research: parameter grids with honest validation.

* ``parameter_search``: chronological 50/25/25 split into development,
  validation and final out-of-sample test. Every combination is scored on
  the development set; the top few are re-scored on validation; the single
  best-by-validation is finally reported on the untouched test set. The
  number of combinations is tracked and over-optimisation warnings are
  raised when the grid is large relative to the number of trades.
* ``walk_forward``: rolling windows; in each window the best parameters on
  the training slice are applied to the following test slice, and the
  out-of-sample slices are stitched together.
"""

from __future__ import annotations

import itertools
from datetime import datetime
from typing import Any, Sequence

from ..backtest import BacktestConfig, compute_metrics, run_backtest
from ..models import AssetClass, Bar
from ..risk import RiskSettings
from ..strategies import Strategy

MAX_RECOMMENDED_COMBINATIONS = 60
RESEARCH_BALANCE = 10_000.0


def expand_grid(grid: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = [k for k, v in grid.items() if v]
    values = [list(grid[k]) for k in keys]
    if not keys:
        return [{}]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _run(bars: Sequence[Bar], strategy: Strategy, params: dict[str, Any], symbol: str, asset_class: AssetClass,
         risk_settings: RiskSettings, start: datetime | None, end: datetime | None) -> dict[str, Any]:
    cfg = BacktestConfig(starting_balance=RESEARCH_BALANCE, params=params, risk_settings=risk_settings, start=start, end=end)
    res = run_backtest(bars, strategy, symbol, asset_class, cfg)
    m = compute_metrics(res.trades, res.equity_curve, RESEARCH_BALANCE, res.regimes)
    return {
        "trades": m["trades"], "net_profit": m["net_profit"], "expectancy_r": m["expectancy_r"],
        "profit_factor": m["profit_factor"], "win_rate": m["win_rate"], "max_drawdown_pct": m["max_drawdown_pct"],
        "return_pct": m["total_net_return_pct"], "conclusive": m["conclusive"],
    }


def _objective(m: dict[str, Any], min_trades: int) -> float:
    """Expectancy in R, penalised when there are too few trades (unreliable)."""
    if m["expectancy_r"] is None or m["trades"] == 0:
        return -9.0
    penalty = 1.0 if m["trades"] >= min_trades else m["trades"] / min_trades
    return m["expectancy_r"] * penalty - 0.002 * m["max_drawdown_pct"]


def parameter_search(
    bars: Sequence[Bar],
    strategy: Strategy,
    grid: dict[str, Sequence[Any]],
    symbol: str,
    asset_class: AssetClass,
    risk_settings: RiskSettings | None = None,
    min_trades: int = 10,
    top_k: int = 5,
) -> dict[str, Any]:
    risk_settings = risk_settings or RiskSettings()
    combos = expand_grid(grid)
    n = len(bars)
    if n < 300:
        return {"error": f"Need at least 300 bars for a development/validation/test split; have {n}."}
    dev_end = bars[int(n * 0.5) - 1].ts
    val_start = bars[int(n * 0.5)].ts
    val_end = bars[int(n * 0.75) - 1].ts
    test_start = bars[int(n * 0.75)].ts
    warnings: list[str] = []
    if len(combos) > MAX_RECOMMENDED_COMBINATIONS:
        warnings.append(
            f"{len(combos)} parameter combinations tested. Testing many combinations makes it likely that the best one "
            "is a lucky fit to the past (over-optimisation). Treat the result with suspicion."
        )

    dev_results = []
    for params in combos:
        merged = strategy.merge_params(params)
        m = _run(bars, strategy, merged, symbol, asset_class, risk_settings, None, dev_end)
        dev_results.append({"params": params, "development": m, "objective": _objective(m, min_trades)})
    dev_results.sort(key=lambda r: r["objective"], reverse=True)
    shortlist = dev_results[:top_k]
    for r in shortlist:
        merged = strategy.merge_params(r["params"])
        r["validation"] = _run(bars, strategy, merged, symbol, asset_class, risk_settings, val_start, val_end)
        r["validation_objective"] = _objective(r["validation"], min_trades)
    shortlist.sort(key=lambda r: r["validation_objective"], reverse=True)
    best = shortlist[0] if shortlist else None
    if best is not None:
        merged = strategy.merge_params(best["params"])
        best["test"] = _run(bars, strategy, merged, symbol, asset_class, risk_settings, test_start, None)
        dev_trades = best["development"]["trades"]
        if dev_trades and len(combos) > max(5, dev_trades / 2):
            warnings.append(
                f"The grid has {len(combos)} combinations but the development period only produced {dev_trades} trades for the "
                "chosen parameters; the selection is statistically weak."
            )
        stable = (
            best["development"]["expectancy_r"] is not None and best["validation"]["expectancy_r"] is not None
            and best["test"]["expectancy_r"] is not None
            and best["development"]["expectancy_r"] > 0 and best["validation"]["expectancy_r"] > 0 and best["test"]["expectancy_r"] > 0
        )
        best["stable_across_periods"] = stable
        if not stable:
            warnings.append("The best parameters did not stay positive across development, validation and test; the edge is not confirmed.")
        if best["test"]["trades"] < min_trades:
            warnings.append(f"Only {best['test']['trades']} test-period trade(s); the final out-of-sample result is inconclusive.")
    return {
        "strategy": strategy.key,
        "symbol": symbol,
        "combinations_tested": len(combos),
        "split": {
            "development": [bars[0].ts.isoformat(), dev_end.isoformat()],
            "validation": [val_start.isoformat(), val_end.isoformat()],
            "test": [test_start.isoformat(), bars[-1].ts.isoformat()],
        },
        "best": best,
        "shortlist": shortlist,
        "all_development_results": dev_results[:50],
        "warnings": warnings,
        "method": (
            "Combinations are ranked on the development period, the top few are re-checked on the validation period, "
            "and only the winner by validation is scored on the final test period. Selecting by test performance would be cheating."
        ),
    }


def walk_forward(
    bars: Sequence[Bar],
    strategy: Strategy,
    grid: dict[str, Sequence[Any]],
    symbol: str,
    asset_class: AssetClass,
    risk_settings: RiskSettings | None = None,
    train_bars: int = 300,
    test_bars: int = 120,
    min_trades: int = 5,
) -> dict[str, Any]:
    risk_settings = risk_settings or RiskSettings()
    combos = expand_grid(grid)
    n = len(bars)
    if n < train_bars + test_bars:
        return {"error": f"Need at least {train_bars + test_bars} bars for walk-forward; have {n}."}
    folds = []
    start = 0
    while start + train_bars + test_bars <= n:
        train_slice = bars[start : start + train_bars]
        test_slice_end = min(n, start + train_bars + test_bars)
        fold_best = None
        for params in combos:
            merged = strategy.merge_params(params)
            m = _run(train_slice, strategy, merged, symbol, asset_class, risk_settings, None, None)
            obj = _objective(m, min_trades)
            if fold_best is None or obj > fold_best["objective"]:
                fold_best = {"params": params, "train": m, "objective": obj}
        assert fold_best is not None
        merged = strategy.merge_params(fold_best["params"])
        # Test uses history for warm-up but only trades inside the test window.
        test_m = _run(bars[: test_slice_end], strategy, merged, symbol, asset_class, risk_settings,
                      bars[start + train_bars].ts, bars[test_slice_end - 1].ts)
        folds.append({
            "train_period": [train_slice[0].ts.isoformat(), train_slice[-1].ts.isoformat()],
            "test_period": [bars[start + train_bars].ts.isoformat(), bars[test_slice_end - 1].ts.isoformat()],
            "params": fold_best["params"], "train": fold_best["train"], "test": test_m,
        })
        start += test_bars
    test_exps = [f["test"]["expectancy_r"] for f in folds if f["test"]["expectancy_r"] is not None]
    positive = sum(1 for e in test_exps if e > 0)
    total_oos_trades = sum(f["test"]["trades"] for f in folds)
    oos_net = sum(f["test"]["net_profit"] for f in folds)
    mean_exp = sum(test_exps) / len(test_exps) if test_exps else None
    sd_exp = None
    if len(test_exps) > 1 and mean_exp is not None:
        sd_exp = (sum((e - mean_exp) ** 2 for e in test_exps) / (len(test_exps) - 1)) ** 0.5
    warnings = []
    if len(combos) > MAX_RECOMMENDED_COMBINATIONS:
        warnings.append(f"{len(combos)} combinations per fold is a lot; over-optimisation risk is high.")
    if total_oos_trades < 20:
        warnings.append(f"Only {total_oos_trades} out-of-sample trade(s) across all folds; inconclusive.")
    return {
        "strategy": strategy.key, "symbol": symbol, "combinations_per_fold": len(combos), "folds": folds,
        "summary": {
            "folds": len(folds), "folds_positive": positive, "oos_trades": total_oos_trades,
            "oos_net_profit": round(oos_net, 2), "mean_oos_expectancy_r": round(mean_exp, 3) if mean_exp is not None else None,
            "sd_oos_expectancy_r": round(sd_exp, 3) if sd_exp is not None else None,
            "stability": (f"{positive}/{len(folds)} folds were profitable out of sample." if folds else "No folds."),
        },
        "warnings": warnings,
        "method": "For each window the best parameters on the training slice are applied, unchanged, to the following test slice.",
    }
