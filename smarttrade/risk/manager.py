"""Risk manager: the single gate every proposed trade must pass.

Strategies produce signals; only the RiskManager can turn a signal into an
approved, sized proposal. Backtesting, scanning and paper trading all call
``RiskManager.evaluate`` so no code path can bypass the limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ..models import AssetClass, Regime, Signal
from .costs import CostModel, default_cost_models
from .position_sizing import SizingResult, size_position
from .settings import RiskSettings


@dataclass
class OpenRisk:
    symbol: str
    asset_class: AssetClass
    planned_risk: float
    notional: float
    strategy: str = ""


@dataclass
class AccountState:
    equity: float
    cash: float
    starting_balance: float
    day_start_equity: float
    realized_today: float = 0.0
    unrealized: float = 0.0
    current_day: date | None = None
    open_positions: list[OpenRisk] = field(default_factory=list)
    halted_for_review: bool = False

    @property
    def total_open_risk(self) -> float:
        return sum(p.planned_risk for p in self.open_positions)

    @property
    def today_pnl(self) -> float:
        """Realized today plus unrealized on open positions (conservative)."""
        return self.realized_today + self.unrealized


@dataclass
class RiskDecision:
    approved: bool
    reasons: list[str]
    checks: list[dict]
    sizing: SizingResult | None = None
    halt_required: bool = False

    def as_dict(self) -> dict:
        return {
            "approved": self.approved,
            "reasons": self.reasons,
            "checks": self.checks,
            "sizing": self.sizing.as_dict() if self.sizing else None,
            "halt_required": self.halt_required,
        }


class RiskManager:
    def __init__(self, settings: RiskSettings | None = None, cost_models: dict[str, CostModel] | None = None):
        self.settings = settings or RiskSettings()
        self.cost_models = cost_models or default_cost_models()

    def cost_model(self, asset_class: AssetClass) -> CostModel:
        return self.cost_models.get(asset_class.value, CostModel())

    # ------------------------------------------------------------ account gates
    def account_gates(self, account: AccountState) -> list[dict]:
        """Checks that apply before any individual trade is considered."""
        s = self.settings
        checks: list[dict] = []

        floor_hit = account.equity <= s.equity_floor
        checks.append({
            "name": "Equity floor",
            "passed": not floor_hit,
            "detail": f"Equity ${account.equity:.2f} vs floor ${s.equity_floor:.2f}."
            + (" Trading halted: review required before resuming." if floor_hit else ""),
            "halt": floor_hit,
        })
        cum_loss = account.starting_balance - account.equity
        cum_hit = cum_loss >= s.cumulative_loss_limit
        checks.append({
            "name": "Cumulative loss limit",
            "passed": not cum_hit,
            "detail": f"Loss from start ${cum_loss:.2f} vs limit ${s.cumulative_loss_limit:.2f}.",
            "halt": cum_hit,
        })
        daily_hit = account.today_pnl <= -s.daily_loss_limit
        checks.append({
            "name": "Daily loss limit",
            "passed": not daily_hit,
            "detail": f"Today's P&L ${account.today_pnl:+.2f} vs limit -${s.daily_loss_limit:.2f}."
            + (" No new positions today." if daily_hit else ""),
        })
        checks.append({
            "name": "Review halt",
            "passed": not account.halted_for_review,
            "detail": "Account is halted pending your review." if account.halted_for_review else "Not halted.",
        })
        checks.append({
            "name": "Max positions",
            "passed": len(account.open_positions) < s.max_positions,
            "detail": f"{len(account.open_positions)} open of {s.max_positions} allowed.",
        })
        return checks

    # ----------------------------------------------------------------- evaluate
    def evaluate(
        self,
        signal: Signal,
        account: AccountState,
        asset_class: AssetClass,
        symbol: str,
        benchmark_regime: Regime | None = None,
        data_quality: float | None = None,
    ) -> RiskDecision:
        s = self.settings
        checks = self.account_gates(account)
        reasons = [c["detail"] for c in checks if not c["passed"]]
        halt = any(c.get("halt") and not c["passed"] for c in checks)

        if signal.direction.value == "short" and not s.short_selling_enabled:
            checks.append({"name": "Short selling", "passed": False, "detail": "Short selling is disabled."})
            reasons.append("Short selling is disabled.")
        if any(p.symbol == symbol for p in account.open_positions):
            checks.append({"name": "Duplicate exposure", "passed": False, "detail": f"Already holding {symbol}."})
            reasons.append(f"Already holding a position in {symbol}.")
        else:
            checks.append({"name": "Duplicate exposure", "passed": True, "detail": f"No open position in {symbol}."})
        if data_quality is not None:
            ok = data_quality >= s.min_data_quality
            checks.append({"name": "Data quality", "passed": ok, "detail": f"Quality {data_quality:.2f} vs minimum {s.min_data_quality:.2f}."})
            if not ok:
                reasons.append("Data quality below the configured minimum.")
        if s.require_benchmark_not_down and benchmark_regime is not None:
            ok = benchmark_regime != Regime.TREND_DOWN
            checks.append({"name": "Benchmark regime", "passed": ok, "detail": f"Benchmark regime is '{benchmark_regime.value}'."})
            if not ok:
                reasons.append("Benchmark (broad market) is trending down; new long entries are paused.")
        # Correlation / concentration: at most 75% of the position slots in one asset class
        # (with 2 slots that allows 2, with 4 slots it allows 3).
        same_class = [p for p in account.open_positions if p.asset_class == asset_class]
        class_cap = max(1, math.ceil(s.max_positions * 0.75))
        if len(same_class) >= class_cap:
            checks.append({"name": "Concentration", "passed": False, "detail": f"Already {len(same_class)} position(s) in {asset_class.value}; diversify across asset classes."})
            reasons.append(f"Too concentrated in {asset_class.value}.")
        else:
            checks.append({"name": "Concentration", "passed": True, "detail": f"{len(same_class)} other position(s) in {asset_class.value}."})

        remaining_risk = account.equity * s.max_total_open_risk_pct / 100.0 - account.total_open_risk
        fractional = s.allow_fractional_crypto if asset_class == AssetClass.CRYPTO else s.allow_fractional_stocks
        sizing = size_position(
            signal, account.equity, account.cash, self.cost_model(asset_class), s.risk_per_trade_pct,
            s.max_position_pct_of_equity, s.min_trade_notional, fractional, remaining_risk_budget=remaining_risk,
        )
        for rej in sizing.rejections:
            checks.append({"name": "Position sizing", "passed": False, "detail": rej})
            reasons.append(rej)
        if sizing.ok:
            checks.append({"name": "Position sizing", "passed": True, "detail": f"{sizing.quantity:g} units, planned loss ${sizing.planned_loss:.2f} within budget ${sizing.risk_budget:.2f}."})
            rr_ok = sizing.reward_risk_after_costs >= s.min_reward_risk
            checks.append({
                "name": "Reward-to-risk after costs",
                "passed": rr_ok,
                "detail": f"{sizing.reward_risk_after_costs:.2f} vs minimum {s.min_reward_risk:.2f} "
                          f"(before costs {sizing.reward_risk_before_costs:.2f}; costs are {sizing.cost_share_of_risk:.0%} of the planned loss).",
            })
            if not rr_ok:
                reasons.append(f"Reward-to-risk after costs {sizing.reward_risk_after_costs:.2f} is below {s.min_reward_risk:.2f}.")
            if sizing.cost_share_of_risk > 0.5:
                checks.append({"name": "Cost drag", "passed": False, "detail": f"Costs would be {sizing.cost_share_of_risk:.0%} of the planned loss (limit 50%). The trade is too small for its costs."})
                reasons.append("Transaction costs consume more than half of the planned risk.")
            else:
                checks.append({"name": "Cost drag", "passed": True, "detail": f"Costs are {sizing.cost_share_of_risk:.0%} of the planned loss."})
        approved = not reasons
        return RiskDecision(approved=approved, reasons=reasons, checks=checks, sizing=sizing, halt_required=halt)
