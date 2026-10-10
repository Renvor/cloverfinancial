import pytest

from smarttrade.models import AssetClass, Direction, Regime, Signal
from smarttrade.risk import AccountState, CostModel, OpenRisk, RiskManager, RiskSettings, size_position
from smarttrade.risk.position_sizing import estimate_trade_economics


def signal(entry=100.0, stop=98.0, target=106.0, direction=Direction.LONG) -> Signal:
    return Signal(strategy="t", direction=direction, entry_price=entry, stop_price=stop, target_price=target, entry_conditions="x")


def account(equity=50.0, cash=None, **kw) -> AccountState:
    return AccountState(equity=equity, cash=equity if cash is None else cash, starting_balance=kw.pop("starting_balance", equity),
                        day_start_equity=equity, **kw)


def test_fee_calculations():
    cm = CostModel(commission_fixed=1.0, commission_pct=0.001, spread_pct=0.0005, slippage_pct=0.0005)
    assert cm.fees_only(1000) == 1.0 + 1.0
    assert abs(cm.slippage_only(1000) - 1.0) < 1e-9
    assert abs(cm.side_cost(1000) - 3.0) < 1e-9
    loss, profit, fees, slip = estimate_trade_economics(signal(), 10, CostModel(commission_pct=0.01))
    # loss: 2*10 + 1% of 1000 + 1% of 980 = 20 + 10 + 9.8
    assert abs(loss - 39.8) < 1e-9
    # profit: 6*10 - 10 - 10.6
    assert abs(profit - 39.4) < 1e-9
    assert abs(fees - 19.8) < 1e-9 and slip == 0


def test_position_size_respects_budget_and_rounds_down():
    cm = CostModel(spread_pct=0.0003, slippage_pct=0.0005, quantity_precision=4)
    res = size_position(signal(), equity=50, cash=50, costs=cm, risk_per_trade_pct=1.0, max_position_pct_of_equity=100, min_trade_notional=1, fractional_allowed=True)
    assert res.ok
    assert res.planned_loss <= res.risk_budget + 1e-9
    assert res.risk_budget == 0.5
    assert res.quantity == int(res.quantity * 10_000) / 10_000
    assert res.reward_risk_after_costs < res.reward_risk_before_costs


def test_whole_shares_when_fractional_disabled():
    res = size_position(signal(), equity=50, cash=50, costs=CostModel(), risk_per_trade_pct=1.0, max_position_pct_of_equity=100, min_trade_notional=1, fractional_allowed=False)
    assert not res.ok  # 0.25 shares rounds down to 0
    big = size_position(signal(), equity=5000, cash=5000, costs=CostModel(), risk_per_trade_pct=1.0, max_position_pct_of_equity=100, min_trade_notional=1, fractional_allowed=False)
    assert big.ok and big.quantity == 25.0


def test_cash_cap_prevents_leverage():
    res = size_position(signal(entry=100, stop=99.9, target=101), equity=50, cash=50, costs=CostModel(), risk_per_trade_pct=1.0, max_position_pct_of_equity=100, min_trade_notional=1, fractional_allowed=True)
    assert res.notional <= 50.0 + 1e-9


def test_rejects_invalid_signals():
    assert not size_position(signal(stop=101), 50, 50, CostModel(), 1, 100, 1, True).ok
    assert not size_position(signal(target=99), 50, 50, CostModel(), 1, 100, 1, True).ok
    assert not size_position(signal(), 0, 0, CostModel(), 1, 100, 1, True).ok


def test_risk_manager_approves_good_trade():
    rm = RiskManager(RiskSettings())
    d = rm.evaluate(signal(), account(), AssetClass.STOCK, "AAA")
    assert d.approved, d.reasons
    assert d.sizing.reward_risk_after_costs >= 2.0


def test_reward_risk_minimum_enforced():
    rm = RiskManager(RiskSettings())
    d = rm.evaluate(signal(target=103), account(), AssetClass.STOCK, "AAA")
    assert not d.approved
    assert any("Reward-to-risk" in r for r in d.reasons)


def test_daily_loss_limit_blocks_new_entries():
    rm = RiskManager(RiskSettings())
    acc = account(realized_today=-1.0)
    d = rm.evaluate(signal(), acc, AssetClass.STOCK, "AAA")
    assert not d.approved and any("Daily loss limit" in c["name"] and not c["passed"] for c in d.checks)
    assert not d.halt_required


def test_equity_floor_halts_and_requires_review():
    rm = RiskManager(RiskSettings())
    d = rm.evaluate(signal(), account(equity=40.0, starting_balance=50.0), AssetClass.STOCK, "AAA")
    assert not d.approved and d.halt_required
    assert any("Equity floor" in c["name"] and not c["passed"] for c in d.checks)


def test_cumulative_loss_limit_halts():
    rm = RiskManager(RiskSettings(equity_floor=10.0))
    d = rm.evaluate(signal(), account(equity=39.0, starting_balance=50.0), AssetClass.STOCK, "AAA")
    assert not d.approved and d.halt_required
    assert any("Cumulative loss" in c["name"] and not c["passed"] for c in d.checks)


def test_max_positions_and_duplicate_and_concentration():
    rm = RiskManager(RiskSettings(max_positions=2))
    acc = account(open_positions=[OpenRisk("AAA", AssetClass.STOCK, 0.5, 20), OpenRisk("BBB", AssetClass.STOCK, 0.5, 20)])
    assert not rm.evaluate(signal(), acc, AssetClass.STOCK, "CCC").approved
    acc = account(open_positions=[OpenRisk("AAA", AssetClass.STOCK, 0.5, 20)])
    assert any("Already holding" in r for r in rm.evaluate(signal(), acc, AssetClass.STOCK, "AAA").reasons)
    rm4 = RiskManager(RiskSettings(max_positions=4))
    acc = account(equity=200, open_positions=[OpenRisk(s, AssetClass.STOCK, 0.5, 20) for s in ("A", "B", "C")])
    assert any("concentrated" in r for r in rm4.evaluate(signal(), acc, AssetClass.STOCK, "D").reasons)


def test_short_selling_rejected():
    rm = RiskManager(RiskSettings())
    d = rm.evaluate(signal(direction=Direction.SHORT), account(), AssetClass.STOCK, "AAA")
    assert not d.approved and any("Short selling" in r for r in d.reasons)


def test_benchmark_downtrend_blocks_longs():
    rm = RiskManager(RiskSettings())
    d = rm.evaluate(signal(), account(), AssetClass.STOCK, "AAA", benchmark_regime=Regime.TREND_DOWN)
    assert not d.approved and any("trending down" in r for r in d.reasons)
    assert rm.evaluate(signal(), account(), AssetClass.STOCK, "AAA", benchmark_regime=Regime.TREND_UP).approved


def test_cost_drag_rejects_trades_too_small_for_their_costs():
    rm = RiskManager(RiskSettings(), {"crypto": CostModel(commission_pct=0.02)})  # absurd 2% fee
    d = rm.evaluate(signal(), account(), AssetClass.CRYPTO, "BTC")
    assert not d.approved


def test_total_open_risk_budget():
    rm = RiskManager(RiskSettings(max_positions=3, max_total_open_risk_pct=1.0))
    acc = account(open_positions=[OpenRisk("AAA", AssetClass.CRYPTO, 0.5, 20)])
    d = rm.evaluate(signal(), acc, AssetClass.STOCK, "BBB")
    assert not d.approved and any("risk budget" in r.lower() for r in d.reasons)


def test_settings_validation_and_scaling():
    with pytest.raises(ValueError):
        RiskSettings(leverage_enabled=True).validate()
    with pytest.raises(ValueError):
        RiskSettings.from_dict({"min_reward_risk": 0.5})
    scaled = RiskSettings().scaled_for_balance(1000)
    assert scaled.daily_loss_limit == 20 and scaled.cumulative_loss_limit == 200 and scaled.equity_floor == 800
    assert scaled.risk_per_trade_pct == 1.0
