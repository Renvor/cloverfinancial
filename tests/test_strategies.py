import pytest

from smarttrade.models import Bar, Direction
from smarttrade.strategies import build_view, get_strategy, list_strategies
from smarttrade.strategies.base import LookAheadError, OpenPositionState
from tests.conftest import make_bars, trending_bars


def all_signals(bars, strategy, params=None):
    params = strategy.merge_params(params)
    indicators = strategy.prepare(bars, params)
    out = {}
    for i in range(strategy.warmup_bars(params), len(bars)):
        sig = strategy.entry_signal(build_view(bars, indicators, i), params)
        if sig is not None:
            out[i] = (round(sig.entry_price, 8), round(sig.stop_price, 8), round(sig.target_price, 8))
    return out


@pytest.mark.parametrize("key", [s.key for s in list_strategies()])
def test_every_strategy_produces_well_formed_long_signals(key):
    strategy = get_strategy(key)
    found = 0
    for seed in range(1, 8):
        bars = make_bars(500, seed=seed, drift=0.15)
        params = strategy.default_params()
        indicators = strategy.prepare(bars, params)
        for i in range(strategy.warmup_bars(params), len(bars)):
            sig = strategy.entry_signal(build_view(bars, indicators, i), params)
            if sig is None:
                continue
            found += 1
            assert sig.direction == Direction.LONG
            assert sig.stop_price < sig.entry_price < sig.target_price
            assert sig.entry_price == bars[i].close
            assert sig.evidence and sig.failure_reasons
            assert 0.0 <= sig.strength <= 1.0
    assert found > 0, f"{key} never produced a signal on 7 synthetic series"


@pytest.mark.parametrize("key", [s.key for s in list_strategies()])
def test_signals_do_not_depend_on_future_bars(key):
    strategy = get_strategy(key)
    bars = make_bars(500, seed=3, drift=0.15)
    full = all_signals(bars, strategy)
    truncated = all_signals(bars[:350], strategy)
    assert {i: v for i, v in full.items() if i < 350} == truncated


def test_market_view_refuses_future_access():
    bars = trending_bars(50)
    view = build_view(bars, {"close": [b.close for b in bars]}, 10)
    assert view.bar().ts == bars[10].ts
    assert view.value("close", -1) == bars[10].close
    with pytest.raises(LookAheadError):
        view.bar(11)
    with pytest.raises(LookAheadError):
        view.value("close", 20)
    assert len(view.bars(100)) == 11


def test_trend_following_entry_and_exit_rules():
    strategy = get_strategy("trend_following")
    bars = trending_bars(260, step=0.1)  # gentle slope: price stays close to the fast EMA
    params = strategy.default_params()
    indicators = strategy.prepare(bars, params)
    view = build_view(bars, indicators, len(bars) - 1)
    sig = strategy.entry_signal(view, params)
    assert sig is not None  # clean, orderly uptrend qualifies
    # Exit rule: fast EMA below slow EMA triggers an exit reason.
    down = trending_bars(260, step=-0.5, start=400)
    ind2 = strategy.prepare(down, params)
    pos = OpenPositionState(entry_price=100, stop_price=90, target_price=120, entry_idx=200, bars_held=10)
    assert strategy.exit_signal(build_view(down, ind2, len(down) - 1), pos, params) is not None
    assert strategy.exit_signal(view, pos, params) is None


def test_trend_following_rejects_extended_moves():
    strategy = get_strategy("trend_following")
    bars = trending_bars(260, step=0.5)
    # Spike the last bar far above the fast EMA.
    last = bars[-1]
    bars[-1] = Bar(last.ts, last.open, last.close * 1.5, last.low, last.close * 1.5, last.volume)
    params = strategy.default_params()
    indicators = strategy.prepare(bars, params)
    assert strategy.entry_signal(build_view(bars, indicators, len(bars) - 1), params) is None


def test_breakout_requires_volume_and_level():
    strategy = get_strategy("breakout")
    bars = trending_bars(200, step=0.0)  # flat: no breakout
    # A cluster of wider bars (deeper lows only, so no resistance is created above) a few weeks
    # earlier, so that the breakout bar is not the most volatile moment of the window.
    for i in range(150, 171):
        b = bars[i]
        bars[i] = Bar(b.ts, b.open, b.high, b.low - 1.0, b.close, b.volume)
    params = strategy.default_params()
    indicators = strategy.prepare(bars, params)
    assert strategy.entry_signal(build_view(bars, indicators, len(bars) - 1), params) is None
    # Breakout bar modestly above the range with 3x volume.
    last = bars[-1]
    level = max(b.high for b in bars[-21:-1])
    bars[-1] = Bar(last.ts, level, level + 0.6, level - 0.1, level + 0.4, 3_500_000)
    indicators = strategy.prepare(bars, params)
    sig = strategy.entry_signal(build_view(bars, indicators, len(bars) - 1), params)
    assert sig is not None and sig.extra["breakout_level"] < sig.entry_price
    # Same breakout without volume confirmation is rejected.
    bars[-1] = Bar(last.ts, level, level + 0.6, level - 0.1, level + 0.4, 1_000_000)
    indicators = strategy.prepare(bars, params)
    assert strategy.entry_signal(build_view(bars, indicators, len(bars) - 1), params) is None


def test_momentum_exit_when_short_return_negative():
    strategy = get_strategy("momentum")
    bars = trending_bars(200, step=-0.3, start=300)
    params = strategy.default_params()
    indicators = strategy.prepare(bars, params)
    pos = OpenPositionState(entry_price=100, stop_price=90, target_price=120, entry_idx=150, bars_held=5)
    assert "Momentum loss" in strategy.exit_signal(build_view(bars, indicators, len(bars) - 1), pos, params)


def test_pullback_exit_below_sma50():
    strategy = get_strategy("pullback")
    bars = trending_bars(200, step=-0.3, start=300)
    params = strategy.default_params()
    indicators = strategy.prepare(bars, params)
    pos = OpenPositionState(entry_price=100, stop_price=90, target_price=120, entry_idx=150, bars_held=5)
    assert "Trend failure" in strategy.exit_signal(build_view(bars, indicators, len(bars) - 1), pos, params)


def test_params_merge_and_types():
    strategy = get_strategy("breakout")
    merged = strategy.merge_params({"lookback": "30", "min_rel_volume": "2"})
    assert merged["lookback"] == 30 and isinstance(merged["lookback"], int)
    assert merged["min_rel_volume"] == 2.0
