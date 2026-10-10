"""Configurable risk limits with plain-English explanations.

The defaults are the *example* settings from the product brief. They describe
how a hypothetical $50 account would be protected; they do not imply any
money has been deposited.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class RiskSettings:
    example_account_size: float = 50.0
    desired_daily_profit: float = 5.0
    daily_loss_limit: float = 1.0
    cumulative_loss_limit: float = 10.0
    equity_floor: float = 40.0
    risk_per_trade_pct: float = 1.0
    min_reward_risk: float = 2.0
    max_positions: int = 2
    max_position_pct_of_equity: float = 60.0
    max_total_open_risk_pct: float = 2.0
    min_trade_notional: float = 1.0
    allow_fractional_stocks: bool = True
    allow_fractional_crypto: bool = True
    leverage_enabled: bool = False
    short_selling_enabled: bool = False
    options_enabled: bool = False
    require_benchmark_not_down: bool = True
    min_data_quality: float = 0.5

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RiskSettings":
        base = cls()
        if not data:
            return base
        for f in fields(cls):
            if f.name in data and data[f.name] is not None:
                raw = data[f.name]
                if f.type == "bool" or isinstance(getattr(base, f.name), bool):
                    value = raw if isinstance(raw, bool) else str(raw).lower() in ("1", "true", "yes", "on")
                elif isinstance(getattr(base, f.name), int) and not isinstance(getattr(base, f.name), bool):
                    value = int(raw)
                else:
                    value = float(raw)
                setattr(base, f.name, value)
        base.validate()
        return base

    def scaled_for_balance(self, balance: float) -> "RiskSettings":
        """Return a copy whose dollar limits are scaled from the example account to ``balance``.

        The limits are defined for the example account size (default $50: $1 daily
        loss, $10 cumulative loss, $40 floor). A $1,000 backtest therefore uses
        $20 / $200 / $800. Percent-based settings are unchanged.
        """
        if balance <= 0 or self.example_account_size <= 0:
            return RiskSettings(**self.as_dict())
        factor = balance / self.example_account_size
        data = self.as_dict()
        for key in ("desired_daily_profit", "daily_loss_limit", "cumulative_loss_limit", "equity_floor", "min_trade_notional"):
            data[key] = round(data[key] * factor, 6)
        data["example_account_size"] = balance
        return RiskSettings(**data)

    def validate(self) -> None:
        if self.risk_per_trade_pct <= 0 or self.risk_per_trade_pct > 10:
            raise ValueError("risk_per_trade_pct must be between 0 and 10")
        if self.min_reward_risk < 1.0:
            raise ValueError("min_reward_risk must be at least 1.0")
        if self.max_positions < 1:
            raise ValueError("max_positions must be at least 1")
        if self.daily_loss_limit <= 0 or self.cumulative_loss_limit <= 0:
            raise ValueError("loss limits must be positive")
        if self.equity_floor < 0:
            raise ValueError("equity_floor cannot be negative")
        if self.leverage_enabled or self.short_selling_enabled or self.options_enabled:
            raise ValueError("Leverage, short selling and options are disabled in this version.")


RISK_SETTING_EXPLANATIONS: dict[str, dict[str, str]] = {
    "example_account_size": {"label": "Example account size ($)", "help": "The hypothetical account the examples refer to. Not a deposit."},
    "desired_daily_profit": {"label": "Desired daily profit ($)", "help": "A goal used for context only. The system never forces trades to reach it."},
    "daily_loss_limit": {"label": "Daily loss limit ($)", "help": "Once today's losses reach this amount, no new positions are opened until the next day."},
    "cumulative_loss_limit": {"label": "Cumulative loss limit ($)", "help": "Total loss from the starting balance that halts new entries until you review."},
    "equity_floor": {"label": "Account equity floor ($)", "help": "If equity falls to this level, trading stops and requires your explicit review to resume."},
    "risk_per_trade_pct": {"label": "Risk per trade (% of equity)", "help": "The most a single trade may lose if the stop is hit, including fees and expected slippage."},
    "min_reward_risk": {"label": "Minimum reward-to-risk", "help": "Potential profit after costs divided by potential loss after costs. 2.0 means you aim to make $2 for every $1 risked."},
    "max_positions": {"label": "Max simultaneous positions", "help": "How many trades may be open at once."},
    "max_position_pct_of_equity": {"label": "Max position size (% of equity)", "help": "Caps how much of the account one position may use, limiting concentration."},
    "max_total_open_risk_pct": {"label": "Max total open risk (% of equity)", "help": "Sum of planned risk across open positions may not exceed this."},
    "min_trade_notional": {"label": "Minimum trade size ($)", "help": "Trades smaller than this are rejected as impractical."},
    "allow_fractional_stocks": {"label": "Allow fractional shares", "help": "Only enable if your (future) broker supports fractional shares."},
    "allow_fractional_crypto": {"label": "Allow fractional crypto", "help": "Virtually all exchanges support fractional crypto amounts."},
    "leverage_enabled": {"label": "Leverage", "help": "Disabled. Borrowed money magnifies losses."},
    "short_selling_enabled": {"label": "Short selling", "help": "Disabled. Shorts have unlimited theoretical loss."},
    "options_enabled": {"label": "Options", "help": "Disabled in this version."},
    "require_benchmark_not_down": {"label": "Require benchmark not trending down", "help": "Skip new long entries when the benchmark (broad market) is in a downtrend."},
    "min_data_quality": {"label": "Minimum data quality (0-1)", "help": "Datasets with a lower validation score are not scanned."},
}
