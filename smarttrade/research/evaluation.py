"""Strategy performance monitor ("the brain learns from evidence").

Predefined, documented criteria decide whether a strategy stays active, is
put on watch, or is paused. The monitor never changes parameters on its
own, never reacts to a single loss, and never increases risk after losses.

Criteria (evaluated on completed trades, newest last):
* Fewer than MIN_TRADES trades -> "insufficient data": stays active, no conclusion.
* Expectancy after costs <= 0 over all trades (>= MIN_TRADES) -> PAUSED.
* Profit factor over the last RECENT_WINDOW trades < 0.7 while there are
  at least RECENT_WINDOW trades -> PAUSED (deterioration).
* Max drawdown of the strategy's cumulative P&L > MAX_DD_R (in R units) -> PAUSED.
* Last RECENT_WINDOW trades expectancy below the long-run average by more
  than 0.3R -> WATCH (warning only).
"""

from __future__ import annotations

from typing import Any, Sequence

MIN_TRADES = 20
RECENT_WINDOW = 15
MAX_DD_R = 12.0


def evaluate_strategy_health(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """``trades`` items need: net_pnl, r_multiple, fees, slippage, regime_at_entry (optional)."""
    n = len(trades)
    if n == 0:
        return {"status": "active", "label": "No data", "reasons": ["No completed trades yet."], "metrics": {"trades": 0}}
    r_values = [float(t.get("r_multiple", 0.0)) for t in trades]
    net = [float(t.get("net_pnl", 0.0)) for t in trades]
    wins = sum(1 for x in net if x > 0)
    expectancy_r = sum(r_values) / n
    gross_win = sum(x for x in net if x > 0)
    gross_loss = -sum(x for x in net if x <= 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    # Drawdown of cumulative R.
    peak = 0.0
    cum = 0.0
    dd = 0.0
    for r in r_values:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    recent = trades[-RECENT_WINDOW:]
    recent_r = [float(t.get("r_multiple", 0.0)) for t in recent]
    recent_exp = sum(recent_r) / len(recent_r)
    recent_net = [float(t.get("net_pnl", 0.0)) for t in recent]
    rw = sum(x for x in recent_net if x > 0)
    rl = -sum(x for x in recent_net if x <= 0)
    recent_pf = (rw / rl) if rl > 0 else None
    costs = sum(float(t.get("fees", 0.0)) + float(t.get("slippage", 0.0)) for t in trades)
    by_regime: dict[str, dict[str, Any]] = {}
    for t in trades:
        key = t.get("regime_at_entry", "unknown")
        b = by_regime.setdefault(key, {"trades": 0, "r_sum": 0.0, "wins": 0})
        b["trades"] += 1
        b["r_sum"] += float(t.get("r_multiple", 0.0))
        b["wins"] += 1 if float(t.get("net_pnl", 0.0)) > 0 else 0
    for b in by_regime.values():
        b["expectancy_r"] = round(b["r_sum"] / b["trades"], 3)
        b["win_rate"] = round(b["wins"] / b["trades"], 3)
        del b["r_sum"]

    metrics = {
        "trades": n, "win_rate": round(wins / n, 3), "expectancy_r": round(expectancy_r, 3),
        "profit_factor": round(pf, 3) if pf is not None else None, "max_drawdown_r": round(dd, 2),
        "recent_trades": len(recent), "recent_expectancy_r": round(recent_exp, 3),
        "recent_profit_factor": round(recent_pf, 3) if recent_pf is not None else None,
        "execution_costs": round(costs, 4), "net_profit": round(sum(net), 4), "by_regime": by_regime,
    }
    reasons: list[str] = []
    status = "active"
    if n < MIN_TRADES:
        reasons.append(f"Only {n} completed trade(s); at least {MIN_TRADES} are needed before judging the strategy. Kept active.")
        return {"status": status, "label": "Active (insufficient data)", "reasons": reasons, "metrics": metrics}
    if expectancy_r <= 0:
        status = "paused"
        reasons.append(f"Expectancy after costs is {expectancy_r:+.2f}R over {n} trades (must be positive).")
    if len(recent) >= RECENT_WINDOW and recent_pf is not None and recent_pf < 0.7:
        status = "paused"
        reasons.append(f"Profit factor over the last {RECENT_WINDOW} trades is {recent_pf:.2f} (below 0.7): performance has deteriorated.")
    if dd > MAX_DD_R:
        status = "paused"
        reasons.append(f"Cumulative drawdown of {dd:.1f}R exceeds the {MAX_DD_R:.0f}R limit.")
    if status == "active" and len(recent) >= RECENT_WINDOW and recent_exp < expectancy_r - 0.3:
        status = "watch"
        reasons.append(f"Recent expectancy {recent_exp:+.2f}R is well below the long-run {expectancy_r:+.2f}R. Watching, not paused.")
    if status == "active":
        reasons.append(f"Positive expectancy ({expectancy_r:+.2f}R), acceptable drawdown ({dd:.1f}R) and no recent deterioration. Retained.")
    label = {"active": "Active", "watch": "Watch", "paused": "Paused"}[status]
    return {"status": status, "label": label, "reasons": reasons, "metrics": metrics}
