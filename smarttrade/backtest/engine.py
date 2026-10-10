"""Chronological, look-ahead-safe backtesting engine.

Execution assumptions (documented limitations):

* Decisions are made on the CLOSE of bar ``i`` using a ``MarketView`` that
  cannot read bar ``i+1`` or later.
* Entries fill at the OPEN of bar ``i+1`` plus slippage/spread. If the open
  has gapped so far that the stop is already breached, the entry is skipped.
* Stops and targets are checked against each bar's high/low. If a bar opens
  beyond the stop (gap), the fill is at the open, not at the stop price.
  If both stop and target are touched in the same bar, the STOP is assumed
  to be hit first (conservative).
* Strategy-rule exits and time stops are decided on the close and filled at
  the next open.
* One position per backtest (single asset). Portfolio behaviour is handled
  by the paper-trading engine, which uses the same risk manager.
* Partial fills are not modelled; small retail sizes are assumed to fill in full.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from ..analysis.regime import regime_series
from ..models import AssetClass, Bar, Regime, Signal
from ..risk import AccountState, CostModel, OpenRisk, RiskManager, RiskSettings
from ..strategies import Strategy, build_view
from ..strategies.base import OpenPositionState


@dataclass
class Trade:
    symbol: str
    strategy: str
    direction: str
    entry_ts: datetime
    exit_ts: datetime
    entry_price: float
    exit_price: float
    quantity: float
    planned_stop: float
    planned_target: float
    planned_risk: float
    fees: float
    slippage: float
    gross_pnl: float
    net_pnl: float
    r_multiple: float
    bars_held: int
    exit_reason: str
    regime_at_entry: str
    signal_strength: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["entry_ts"] = self.entry_ts.isoformat()
        d["exit_ts"] = self.exit_ts.isoformat()
        for k in ("entry_price", "exit_price", "planned_stop", "planned_target", "planned_risk", "fees", "slippage", "gross_pnl", "net_pnl", "r_multiple"):
            d[k] = round(d[k], 6)
        return d


@dataclass
class BacktestConfig:
    starting_balance: float = 1000.0
    params: dict[str, Any] = field(default_factory=dict)
    risk_settings: RiskSettings = field(default_factory=RiskSettings)
    cost_model: CostModel | None = None
    start: datetime | None = None
    end: datetime | None = None
    benchmark_bars: Sequence[Bar] | None = None
    regime_step: int = 5  # recompute regime every N bars for speed


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    params: dict[str, Any]
    starting_balance: float
    trades: list[Trade]
    equity_curve: list[tuple[datetime, float]]
    rejected_signals: list[dict[str, Any]]
    bars_tested: int
    start_ts: datetime | None
    end_ts: datetime | None
    regimes: list[str]
    notes: list[str] = field(default_factory=list)

    def as_dict(self, include_curve: bool = True) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "params": self.params,
            "starting_balance": self.starting_balance,
            "trades": [t.as_dict() for t in self.trades],
            "equity_curve": [(ts.isoformat(), round(v, 4)) for ts, v in self.equity_curve] if include_curve else [],
            "rejected_signals": self.rejected_signals[:200],
            "rejected_count": len(self.rejected_signals),
            "bars_tested": self.bars_tested,
            "start_ts": self.start_ts.isoformat() if self.start_ts else None,
            "end_ts": self.end_ts.isoformat() if self.end_ts else None,
            "notes": self.notes,
        }


def _fill_with_slippage(price: float, costs: CostModel, side: str) -> float:
    adj = costs.spread_pct + costs.slippage_pct
    return price * (1 + adj) if side == "buy" else price * (1 - adj)


def run_backtest(
    bars: Sequence[Bar],
    strategy: Strategy,
    symbol: str,
    asset_class: AssetClass,
    config: BacktestConfig,
    risk_manager: RiskManager | None = None,
) -> BacktestResult:
    params = strategy.merge_params(config.params)
    if risk_manager is None:
        # Dollar limits are defined for the example account; scale them to this balance.
        risk_manager = RiskManager(config.risk_settings.scaled_for_balance(config.starting_balance))
    rm = risk_manager
    costs = config.cost_model or rm.cost_model(asset_class)
    if config.cost_model is not None:
        rm.cost_models[asset_class.value] = config.cost_model

    # Restrict the period but keep warm-up history BEFORE the start for indicators.
    all_bars = list(bars)
    if config.end is not None:
        all_bars = [b for b in all_bars if b.ts <= config.end]
    first_idx = 0
    if config.start is not None:
        first_idx = next((i for i, b in enumerate(all_bars) if b.ts >= config.start), len(all_bars))
    warmup = strategy.warmup_bars(params)
    start_idx = max(first_idx, warmup)
    notes: list[str] = []
    if len(all_bars) - start_idx < 30:
        notes.append("Fewer than 30 bars in the test period after indicator warm-up; results are not meaningful.")

    indicators = strategy.prepare(all_bars, params)
    regime_by_bar = regime_series(all_bars)  # causal: index i uses bars <= i only
    bench_regime_cache: dict[int, Regime] = {}
    bench_series = regime_series(config.benchmark_bars) if config.benchmark_bars else None

    def benchmark_regime_at(ts: datetime) -> Regime | None:
        if not config.benchmark_bars:
            return None
        # Only benchmark bars at or before ts may be used.
        idx = -1
        for j, b in enumerate(config.benchmark_bars):
            if b.ts <= ts:
                idx = j
            else:
                break
        if idx < 0 or bench_series is None:
            return None
        return bench_series[idx]

    cash = config.starting_balance
    equity_curve: list[tuple[datetime, float]] = []
    trades: list[Trade] = []
    rejected: list[dict[str, Any]] = []
    regimes: list[str] = []

    position: dict[str, Any] | None = None
    pending_entry: tuple[Signal, float, float, str, float] | None = None  # (signal, qty, planned_risk, regime, strength)
    pending_exit_reason: str | None = None
    day_start_equity = cash
    current_day = None
    realized_today = 0.0
    cached_regime = Regime.UNCLEAR

    def mark_to_market(close: float) -> float:
        if position is None:
            return cash
        return cash + position["qty"] * close

    for i in range(start_idx, len(all_bars)):
        bar = all_bars[i]
        if current_day != bar.ts.date():
            current_day = bar.ts.date()
            day_start_equity = mark_to_market(all_bars[i - 1].close if i > 0 else bar.open)
            realized_today = 0.0

        # 1) Pending entry from yesterday's signal fills at today's open.
        if pending_entry is not None and position is None:
            sig, qty, planned_risk, regime_name, strength = pending_entry
            fill = _fill_with_slippage(bar.open, costs, "buy")
            if fill <= sig.stop_price:
                rejected.append({"ts": bar.ts.isoformat(), "strategy": sig.strategy, "reason": "Open gapped below the stop; entry skipped."})
            else:
                notional = qty * fill
                entry_fee = costs.fees_only(notional)
                if notional + entry_fee > cash + 1e-9:
                    qty = costs.round_quantity((cash - costs.commission_fixed) / (fill * (1 + costs.commission_pct)), True)
                if qty > 0:
                    notional = qty * fill
                    entry_fee = costs.fees_only(notional)
                    cash -= notional + entry_fee
                    position = {
                        "signal": sig, "qty": qty, "entry_price": fill, "entry_ts": bar.ts, "entry_idx": i,
                        "stop": sig.stop_price, "target": sig.target_price, "fees": entry_fee,
                        "slippage": costs.slippage_only(qty * bar.open), "planned_risk": planned_risk,
                        "regime": regime_name, "strength": strength, "bars_held": 0,
                    }
            pending_entry = None

        # 2) Manage the open position during today's bar.
        if position is not None:
            exit_price: float | None = None
            reason = ""
            if pending_exit_reason is not None:
                exit_price = _fill_with_slippage(bar.open, costs, "sell")
                reason = pending_exit_reason
                pending_exit_reason = None
            else:
                # The stop/target can be hit on the entry bar too.
                if bar.open <= position["stop"]:
                    exit_price = _fill_with_slippage(bar.open, costs, "sell")
                    reason = "Stop hit on a gap down (filled at the open, worse than the stop)."
                elif bar.low <= position["stop"]:
                    exit_price = _fill_with_slippage(position["stop"], costs, "sell")
                    reason = "Protective stop hit."
                elif bar.open >= position["target"]:
                    exit_price = _fill_with_slippage(bar.open, costs, "sell")
                    reason = "Target exceeded on a gap up (filled at the open)."
                elif bar.high >= position["target"]:
                    exit_price = _fill_with_slippage(position["target"], costs, "sell")
                    reason = "Profit target hit."
            if exit_price is not None:
                qty = position["qty"]
                notional = qty * exit_price
                exit_fee = costs.fees_only(notional)
                cash += notional - exit_fee
                gross = (exit_price - position["entry_price"]) * qty
                fees = position["fees"] + exit_fee
                slippage = position["slippage"] + costs.slippage_only(notional)
                net = gross - fees
                realized_today += net
                sig = position["signal"]
                trades.append(Trade(
                    symbol=symbol, strategy=strategy.key, direction="long", entry_ts=position["entry_ts"], exit_ts=bar.ts,
                    entry_price=position["entry_price"], exit_price=exit_price, quantity=qty, planned_stop=position["stop"],
                    planned_target=position["target"], planned_risk=position["planned_risk"], fees=fees, slippage=slippage,
                    gross_pnl=gross, net_pnl=net, r_multiple=(net / position["planned_risk"]) if position["planned_risk"] > 0 else 0.0,
                    bars_held=i - position["entry_idx"], exit_reason=reason, regime_at_entry=position["regime"],
                    signal_strength=position["strength"],
                ))
                position = None

        # 3) Decisions on the close of today's bar (view cannot see tomorrow).
        view = build_view(all_bars, indicators, i)
        cached_regime = regime_by_bar[i]
        regimes.append(cached_regime.value)

        if position is not None:
            position["bars_held"] = i - position["entry_idx"]
            state = OpenPositionState(
                entry_price=position["entry_price"], stop_price=position["stop"], target_price=position["target"],
                entry_idx=position["entry_idx"], bars_held=position["bars_held"], extra=position["signal"].extra,
            )
            reason = strategy.exit_signal(view, state, params)
            max_hold = position["signal"].max_holding_bars
            if reason:
                pending_exit_reason = reason
            elif max_hold is not None and position["bars_held"] >= max_hold:
                pending_exit_reason = f"Time stop: held {position['bars_held']} bars."
        else:
            signal = strategy.entry_signal(view, params)
            if signal is not None:
                equity_now = mark_to_market(bar.close)
                account = AccountState(
                    equity=equity_now, cash=cash, starting_balance=config.starting_balance,
                    day_start_equity=day_start_equity, realized_today=realized_today, unrealized=0.0,
                    current_day=current_day, open_positions=[],
                )
                decision = rm.evaluate(signal, account, asset_class, symbol, benchmark_regime=benchmark_regime_at(bar.ts))
                if decision.approved and decision.sizing is not None:
                    pending_entry = (signal, decision.sizing.quantity, decision.sizing.planned_loss, cached_regime.value, signal.strength)
                else:
                    rejected.append({
                        "ts": bar.ts.isoformat(), "strategy": signal.strategy, "entry": signal.entry_price,
                        "stop": signal.stop_price, "target": signal.target_price, "reasons": decision.reasons,
                    })
        equity_curve.append((bar.ts, mark_to_market(bar.close)))

    # Close any open position at the final close so results are complete (documented).
    if position is not None and all_bars:
        last = all_bars[-1]
        exit_price = _fill_with_slippage(last.close, costs, "sell")
        qty = position["qty"]
        notional = qty * exit_price
        exit_fee = costs.fees_only(notional)
        cash += notional - exit_fee
        gross = (exit_price - position["entry_price"]) * qty
        fees = position["fees"] + exit_fee
        net = gross - fees
        trades.append(Trade(
            symbol=symbol, strategy=strategy.key, direction="long", entry_ts=position["entry_ts"], exit_ts=last.ts,
            entry_price=position["entry_price"], exit_price=exit_price, quantity=qty, planned_stop=position["stop"],
            planned_target=position["target"], planned_risk=position["planned_risk"], fees=fees,
            slippage=position["slippage"] + costs.slippage_only(notional), gross_pnl=gross, net_pnl=net,
            r_multiple=(net / position["planned_risk"]) if position["planned_risk"] > 0 else 0.0,
            bars_held=len(all_bars) - 1 - position["entry_idx"], exit_reason="End of test period (position closed at last close).",
            regime_at_entry=position["regime"], signal_strength=position["strength"],
        ))
        if equity_curve:
            equity_curve[-1] = (last.ts, cash)
        notes.append("The final open position was closed at the last available close to report complete results.")

    return BacktestResult(
        symbol=symbol, strategy=strategy.key, params=params, starting_balance=config.starting_balance,
        trades=trades, equity_curve=equity_curve, rejected_signals=rejected,
        bars_tested=max(0, len(all_bars) - start_idx),
        start_ts=all_bars[start_idx].ts if start_idx < len(all_bars) else None,
        end_ts=all_bars[-1].ts if all_bars else None, regimes=regimes, notes=notes,
    )
