from datetime import datetime, timedelta, timezone

from smarttrade.analysis.indicators import max_drawdown
from smarttrade.backtest import BacktestConfig, compute_metrics, run_backtest
from smarttrade.backtest.engine import Trade
from smarttrade.models import AssetClass, Bar, Direction, Signal
from smarttrade.risk import CostModel, RiskSettings
from smarttrade.strategies import get_strategy
from smarttrade.strategies.base import ParamSpec, Strategy
from tests.conftest import make_bars, trending_bars


class AlwaysBuyStrategy(Strategy):
    """Deterministic strategy for engine tests: buys at bar 10 with a 2% stop and 6% target."""

    key = "always"
    name = "Always"
    param_specs = [ParamSpec("x", "x", "int", 1)]

    def warmup_bars(self, params):
        return 10

    def prepare(self, bars, params):
        return {"close": [b.close for b in bars]}

    def entry_signal(self, view, params):
        if view.idx == 10:
            c = view.close
            return Signal(strategy=self.key, direction=Direction.LONG, entry_price=c, stop_price=c * 0.98, target_price=c * 1.06, entry_conditions="t")
        return None

    def exit_signal(self, view, position, params):
        return None


def flat_bars(n, price=100.0):
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [Bar(t0 + timedelta(days=i), price, price + 0.5, price - 0.5, price, 1000) for i in range(n)]


def test_entry_fills_at_next_open_and_target_exit_pnl():
    bars = flat_bars(30)
    # Bar 11 opens at 101 (gap up); bar 15 trades up through the 6% target (106).
    bars[11] = Bar(bars[11].ts, 101, 101.5, 100.5, 101, 1000)
    bars[15] = Bar(bars[15].ts, 104, 107, 103.5, 106.5, 1000)
    cfg = BacktestConfig(starting_balance=1000, cost_model=CostModel(), risk_settings=RiskSettings(max_position_pct_of_equity=100))
    res = run_backtest(bars, AlwaysBuyStrategy(), "X", AssetClass.STOCK, cfg)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry_ts == bars[11].ts and t.entry_price == 101.0  # next open, no slippage in this cost model
    assert t.exit_ts == bars[15].ts and t.exit_price == 106.0  # exact target, not the bar high
    assert abs(t.net_pnl - (106 - 101) * t.quantity) < 1e-9
    assert t.entry_ts < t.exit_ts


def test_stop_gap_fills_at_open_not_at_stop():
    bars = flat_bars(30)
    bars[13] = Bar(bars[13].ts, 90, 91, 89, 90, 1000)  # gap far below the 98 stop
    cfg = BacktestConfig(starting_balance=1000, cost_model=CostModel(), risk_settings=RiskSettings(max_position_pct_of_equity=100))
    res = run_backtest(bars, AlwaysBuyStrategy(), "X", AssetClass.STOCK, cfg)
    t = res.trades[0]
    assert t.exit_price == 90.0 and "gap" in t.exit_reason
    assert t.r_multiple < -1.0  # real loss exceeded the planned loss


def test_stop_assumed_before_target_when_both_touched():
    bars = flat_bars(30)
    bars[12] = Bar(bars[12].ts, 100, 110, 95, 100, 1000)  # touches both stop (98) and target (106)
    cfg = BacktestConfig(starting_balance=1000, cost_model=CostModel(), risk_settings=RiskSettings(max_position_pct_of_equity=100))
    res = run_backtest(bars, AlwaysBuyStrategy(), "X", AssetClass.STOCK, cfg)
    assert res.trades[0].exit_reason.startswith("Protective stop")


def test_slippage_and_fees_applied():
    bars = flat_bars(30)
    bars[15] = Bar(bars[15].ts, 104, 107, 103.5, 106.5, 1000)
    cm = CostModel(commission_fixed=0.0, commission_pct=0.001, spread_pct=0.001, slippage_pct=0.001)
    cfg = BacktestConfig(starting_balance=1000, cost_model=cm, risk_settings=RiskSettings(max_position_pct_of_equity=100))
    res = run_backtest(bars, AlwaysBuyStrategy(), "X", AssetClass.STOCK, cfg)
    t = res.trades[0]
    assert t.entry_price > 100.0 and t.exit_price < 106.0
    assert t.fees > 0 and t.slippage > 0 and t.net_pnl < t.gross_pnl


def test_backtest_is_chronological_and_free_of_look_ahead():
    bars = make_bars(500, seed=4, drift=0.1)
    strategy = get_strategy("trend_following")
    cfg = BacktestConfig(starting_balance=1000)
    full = run_backtest(bars, strategy, "X", AssetClass.STOCK, cfg)
    cutoff = bars[350].ts
    changed = bars[:351] + [Bar(b.ts, b.open * 3, b.high * 3, b.low * 3, b.close * 3, b.volume) for b in bars[351:]]
    alt = run_backtest(changed, strategy, "X", AssetClass.STOCK, cfg)
    early = [t for t in full.trades if t.exit_ts <= cutoff]
    early_alt = [t for t in alt.trades if t.exit_ts <= cutoff]
    assert [(t.entry_ts, t.exit_ts, t.entry_price, t.exit_price) for t in early] == [(t.entry_ts, t.exit_ts, t.entry_price, t.exit_price) for t in early_alt]
    for t in full.trades:
        assert t.entry_ts < t.exit_ts
    ts = [t for t, _ in full.equity_curve]
    assert ts == sorted(ts)


def test_period_selection_and_insufficient_data():
    bars = make_bars(400, seed=2)
    strategy = get_strategy("momentum")
    start = bars[300].ts
    res = run_backtest(bars, strategy, "X", AssetClass.STOCK, BacktestConfig(starting_balance=1000, start=start))
    assert res.start_ts == start
    assert all(t.entry_ts >= start for t in res.trades)
    short = run_backtest(bars[:140], strategy, "X", AssetClass.STOCK, BacktestConfig(starting_balance=1000))
    assert any("Fewer than 30 bars" in n for n in short.notes)
    empty = run_backtest([], strategy, "X", AssetClass.STOCK, BacktestConfig(starting_balance=1000))
    assert empty.trades == [] and empty.bars_tested == 0


def test_metrics_and_drawdown():
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def trade(pnl, r):
        return Trade("X", "s", "long", t0, t0 + timedelta(days=1), 100, 100 + pnl, 1, 98, 106, 2, 0.1, 0.05, pnl, pnl, r, 1, "x", "trending_up")

    trades = [trade(4, 2), trade(-2, -1), trade(-2, -1), trade(6, 3)]
    curve = [(t0 + timedelta(days=i), v) for i, v in enumerate([100, 104, 102, 100, 106])]
    m = compute_metrics(trades, curve, 100)
    assert m["trades"] == 4 and m["win_rate"] == 0.5
    assert m["net_profit"] == 6 and m["profit_factor"] == 2.5
    assert m["expectancy"] == 1.5 and m["expectancy_r"] == 0.75
    assert m["longest_losing_streak"] == 2
    assert abs(m["max_drawdown_pct"] - (4 / 104 * 100)) < 1e-3  # metric is rounded to 3 decimals
    assert not m["conclusive"] and "Inconclusive" in m["verdict"]
    assert m["by_regime"]["trending_up"]["trades"] == 4
    assert max_drawdown([100, 90, 95, 80])[0] == 0.2


def test_negative_expectancy_verdict_with_enough_trades():
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trades = [Trade("X", "s", "long", t0, t0 + timedelta(days=1), 100, 99, 1, 98, 106, 2, 0, 0, -1, -1, -0.5, 1, "x", "range") for _ in range(25)]
    m = compute_metrics(trades, [(t0, 100), (t0 + timedelta(days=1), 75)], 100)
    assert m["conclusive"] and "Negative expectancy" in m["verdict"]


def test_backtest_never_needs_broker_or_credentials():
    bars = trending_bars(300)
    res = run_backtest(bars, get_strategy("trend_following"), "X", AssetClass.ETF, BacktestConfig(starting_balance=500))
    assert res.bars_tested > 0
