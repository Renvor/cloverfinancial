"""Position sizing from the risk budget.

The planned loss if the stop is hit (including entry costs, exit costs and
expected slippage) must not exceed the risk budget. Quantities are rounded
DOWN, never up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Signal
from .costs import CostModel


@dataclass
class SizingResult:
    quantity: float = 0.0
    notional: float = 0.0
    risk_budget: float = 0.0
    planned_loss: float = 0.0  # loss if stop is hit, after all costs
    planned_profit: float = 0.0  # profit if target is hit, after all costs
    estimated_fees: float = 0.0  # round trip commissions
    estimated_slippage: float = 0.0  # round trip spread + slippage
    reward_risk_after_costs: float = 0.0
    reward_risk_before_costs: float = 0.0
    cost_share_of_risk: float = 0.0  # fraction of the planned loss that is pure cost
    rejections: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejections and self.quantity > 0

    def as_dict(self) -> dict:
        return {
            "quantity": self.quantity,
            "notional": round(self.notional, 4),
            "risk_budget": round(self.risk_budget, 4),
            "planned_loss": round(self.planned_loss, 4),
            "planned_profit": round(self.planned_profit, 4),
            "estimated_fees": round(self.estimated_fees, 4),
            "estimated_slippage": round(self.estimated_slippage, 4),
            "reward_risk_after_costs": round(self.reward_risk_after_costs, 3),
            "reward_risk_before_costs": round(self.reward_risk_before_costs, 3),
            "cost_share_of_risk": round(self.cost_share_of_risk, 3),
            "rejections": self.rejections,
        }


def estimate_trade_economics(signal: Signal, qty: float, costs: CostModel) -> tuple[float, float, float, float]:
    """Return (planned_loss, planned_profit, fees_round_trip, slippage_round_trip) for a long of ``qty``."""
    entry_notional = signal.entry_price * qty
    stop_notional = signal.stop_price * qty
    target_notional = signal.target_price * qty
    entry_cost = costs.side_cost(entry_notional)
    exit_cost_stop = costs.side_cost(stop_notional)
    exit_cost_target = costs.side_cost(target_notional)
    loss = (signal.entry_price - signal.stop_price) * qty + entry_cost + exit_cost_stop
    profit = (signal.target_price - signal.entry_price) * qty - entry_cost - exit_cost_target
    fees = costs.fees_only(entry_notional) + costs.fees_only(stop_notional)
    slip = costs.slippage_only(entry_notional) + costs.slippage_only(stop_notional)
    return loss, profit, fees, slip


def size_position(
    signal: Signal,
    equity: float,
    cash: float,
    costs: CostModel,
    risk_per_trade_pct: float,
    max_position_pct_of_equity: float,
    min_trade_notional: float,
    fractional_allowed: bool,
    remaining_risk_budget: float | None = None,
) -> SizingResult:
    res = SizingResult()
    if signal.direction.value != "long":
        res.rejections.append("Only long positions are supported in this version.")
        return res
    if signal.entry_price <= 0 or signal.stop_price <= 0 or signal.target_price <= 0:
        res.rejections.append("Invalid prices in signal.")
        return res
    if signal.stop_price >= signal.entry_price:
        res.rejections.append("Stop must be below the entry for a long trade.")
        return res
    if signal.target_price <= signal.entry_price:
        res.rejections.append("Target must be above the entry for a long trade.")
        return res
    if equity <= 0:
        res.rejections.append("Account equity is zero; configure a simulated balance first.")
        return res

    budget = equity * risk_per_trade_pct / 100.0
    if remaining_risk_budget is not None:
        budget = min(budget, max(0.0, remaining_risk_budget))
    res.risk_budget = budget
    if budget <= 0:
        res.rejections.append("No remaining risk budget for new positions.")
        return res

    # Solve for quantity: loss(qty) = budget, where loss = qty * (entry - stop) + 2*fixed + pct*(entry+stop)*qty
    per_unit_stop = signal.entry_price - signal.stop_price
    per_unit_costs = costs.per_side_pct() * (signal.entry_price + signal.stop_price)
    fixed = 2 * costs.commission_fixed
    if budget <= fixed:
        res.rejections.append(f"Fixed commissions (${fixed:.2f} round trip) alone exceed the risk budget (${budget:.2f}).")
        return res
    qty = (budget - fixed) / (per_unit_stop + per_unit_costs)

    # Cash and concentration caps (no leverage: notional must fit in cash).
    max_notional = min(cash, equity * max_position_pct_of_equity / 100.0)
    if max_notional <= 0:
        res.rejections.append("No cash available for a new position.")
        return res
    cap_qty = max_notional / (signal.entry_price * (1 + costs.per_side_pct()))
    capped = qty > cap_qty
    qty = min(qty, cap_qty)
    qty = costs.round_quantity(qty, fractional_allowed)
    if qty < costs.min_quantity or qty <= 0:
        res.rejections.append(
            "Position would be smaller than the minimum tradable quantity"
            + ("" if fractional_allowed else " (fractional shares disabled)") + "."
        )
        return res

    loss, profit, fees, slip = estimate_trade_economics(signal, qty, costs)
    res.quantity = qty
    res.notional = qty * signal.entry_price
    res.planned_loss = loss
    res.planned_profit = profit
    res.estimated_fees = fees
    res.estimated_slippage = slip
    res.reward_risk_before_costs = signal.reward_per_unit / signal.risk_per_unit if signal.risk_per_unit else 0.0
    res.reward_risk_after_costs = (profit / loss) if loss > 0 else 0.0
    res.cost_share_of_risk = (fees + slip) / loss if loss > 0 else 1.0
    if res.notional < min_trade_notional:
        res.rejections.append(f"Trade size ${res.notional:.2f} is below the minimum of ${min_trade_notional:.2f}.")
    if loss > budget + 1e-9:
        res.rejections.append("Planned loss exceeds the risk budget after rounding.")
    if capped and res.notional < min_trade_notional:
        res.rejections.append("Cash cap left an impractically small position.")
    return res
