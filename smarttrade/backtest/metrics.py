"""Performance metrics with plain-English explanations.

Results with fewer than ``MIN_TRADES_FOR_CONCLUSION`` trades are labelled
inconclusive; nothing here ever claims profitability from a handful of trades.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Sequence

from ..analysis.indicators import max_drawdown
from .engine import Trade

MIN_TRADES_FOR_CONCLUSION = 20

METRIC_EXPLANATIONS: dict[str, str] = {
    "total_net_return_pct": "Percentage change of the simulated balance from start to finish, after all costs.",
    "net_profit": "Dollars gained or lost after fees and slippage.",
    "gross_profit": "Dollars gained or lost before fees and slippage.",
    "trades": "Number of completed round-trip trades.",
    "win_rate": "Share of trades that made money. A high win rate with tiny wins and big losses still loses.",
    "avg_win": "Average profit of winning trades, in dollars.",
    "avg_loss": "Average loss of losing trades, in dollars (shown negative).",
    "profit_factor": "Total winnings divided by total losses. Above 1.0 means the strategy made more than it lost.",
    "expectancy": "Average net profit per trade in dollars. Negative means the strategy loses on average.",
    "expectancy_r": "Average profit per trade measured in units of planned risk (R). 0.3R means +30% of the risked amount per trade.",
    "max_drawdown_pct": "Largest peak-to-trough fall in the equity curve, as a percentage. Measures the worst stretch you would have lived through.",
    "longest_losing_streak": "Most consecutive losing trades.",
    "total_fees": "All simulated commissions paid.",
    "total_slippage": "All simulated spread and slippage costs.",
    "return_on_capital_pct": "Net profit divided by the starting simulated balance.",
    "sharpe_ratio": "Return per unit of volatility (annualised). Above 1 is decent; only meaningful with many trades.",
    "avg_bars_held": "Average number of bars a position was held.",
    "conclusive": "Whether there are enough trades (20+) to draw any conclusion.",
}


def _sharpe(equity: Sequence[float], periods_per_year: int = 252) -> float | None:
    if len(equity) < 30:
        return None
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return mean / sd * math.sqrt(periods_per_year)


def compute_metrics(
    trades: Sequence[Trade],
    equity_curve: Sequence[tuple[datetime, float]],
    starting_balance: float,
    regimes_by_bar: Sequence[str] | None = None,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in losses)
    net = sum(t.net_pnl for t in trades)
    equity_values = [v for _, v in equity_curve] or [starting_balance]
    mdd, longest_dd = max_drawdown(equity_values)

    streak = longest = 0
    for t in trades:
        if t.net_pnl <= 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0

    final_equity = equity_values[-1] if equity_values else starting_balance
    metrics: dict[str, Any] = {
        "trades": n,
        "net_profit": round(net, 4),
        "gross_profit": round(sum(t.gross_pnl for t in trades), 4),
        "total_net_return_pct": round((final_equity / starting_balance - 1) * 100, 3) if starting_balance else 0.0,
        "return_on_capital_pct": round(net / starting_balance * 100, 3) if starting_balance else 0.0,
        "win_rate": round(len(wins) / n, 4) if n else None,
        "avg_win": round(gross_win / len(wins), 4) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 4) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (None if not wins else float("inf")),
        "expectancy": round(net / n, 4) if n else None,
        "expectancy_r": round(sum(t.r_multiple for t in trades) / n, 4) if n else None,
        "max_drawdown_pct": round(mdd * 100, 3),
        "longest_drawdown_bars": longest_dd,
        "longest_losing_streak": longest,
        "total_fees": round(sum(t.fees for t in trades), 4),
        "total_slippage": round(sum(t.slippage for t in trades), 4),
        "sharpe_ratio": (lambda s: round(s, 3) if s is not None else None)(_sharpe(equity_values, periods_per_year)),
        "avg_bars_held": round(sum(t.bars_held for t in trades) / n, 2) if n else None,
        "final_equity": round(final_equity, 4),
        "conclusive": n >= MIN_TRADES_FOR_CONCLUSION,
    }
    if metrics["profit_factor"] == float("inf"):
        metrics["profit_factor"] = None
        metrics["profit_factor_note"] = "No losing trades yet, so the profit factor is undefined."

    # Verdict in plain English, never overstated.
    if n == 0:
        verdict = "No trades were taken, so nothing can be concluded."
    elif n < MIN_TRADES_FOR_CONCLUSION:
        verdict = f"Inconclusive: only {n} trade(s). At least {MIN_TRADES_FOR_CONCLUSION} are needed before drawing conclusions."
    elif metrics["expectancy"] is not None and metrics["expectancy"] <= 0:
        verdict = "Negative expectancy after costs on this data: the strategy lost money on average per trade."
    else:
        verdict = (
            "Positive expectancy after costs on this historical data. This is evidence, not a guarantee; "
            "check out-of-sample and regime results before trusting it."
        )
    metrics["verdict"] = verdict

    # Performance by regime at entry.
    by_regime: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        grouped[t.regime_at_entry].append(t)
    for regime, ts in grouped.items():
        w = [t for t in ts if t.net_pnl > 0]
        by_regime[regime] = {
            "trades": len(ts),
            "net_profit": round(sum(t.net_pnl for t in ts), 4),
            "win_rate": round(len(w) / len(ts), 3),
            "expectancy_r": round(sum(t.r_multiple for t in ts) / len(ts), 3),
            "conclusive": len(ts) >= 15,
        }
    metrics["by_regime"] = by_regime
    if regimes_by_bar:
        counts: dict[str, int] = defaultdict(int)
        for r in regimes_by_bar:
            counts[r] += 1
        metrics["regime_bar_share"] = {k: round(v / len(regimes_by_bar), 3) for k, v in counts.items()}

    # Performance by calendar year.
    by_year: dict[str, dict[str, Any]] = {}
    year_groups: dict[int, list[Trade]] = defaultdict(list)
    for t in trades:
        year_groups[t.exit_ts.year].append(t)
    for year in sorted(year_groups):
        ts = year_groups[year]
        by_year[str(year)] = {
            "trades": len(ts),
            "net_profit": round(sum(t.net_pnl for t in ts), 4),
            "win_rate": round(sum(1 for t in ts if t.net_pnl > 0) / len(ts), 3),
        }
    metrics["by_period"] = by_year
    metrics["explanations"] = METRIC_EXPLANATIONS
    return metrics
