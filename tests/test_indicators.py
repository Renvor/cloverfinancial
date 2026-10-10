import math

from smarttrade.analysis import indicators as ind
from tests.conftest import trending_bars


def test_sma_basic():
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_seeded_with_sma():
    out = ind.ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0
    assert out[3] == 3.0  # (4*0.5 + 2*0.5)
    assert abs(out[4] - 4.0) < 1e-9


def test_rsi_bounds_and_direction():
    rising = [float(i) for i in range(1, 40)]
    out = ind.rsi(rising, 14)
    assert out[14] == 100.0
    falling = [float(40 - i) for i in range(1, 40)]
    assert ind.rsi(falling, 14)[-1] == 0.0
    mixed = [100 + math.sin(i / 3) * 5 for i in range(60)]
    for v in ind.rsi(mixed, 14)[14:]:
        assert 0 <= v <= 100


def test_atr_matches_hand_calculation():
    bars = trending_bars(20, step=1.0)
    atr = ind.atr(bars, 5)
    assert atr[3] is None
    # Reference Wilder ATR computed by hand.
    tr = [bars[0].high - bars[0].low] + [
        max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(bars, bars[1:])
    ]
    expected = sum(tr[:5]) / 5
    assert abs(atr[4] - expected) < 1e-9
    for i in range(5, len(bars)):
        expected = (expected * 4 + tr[i]) / 5
    assert abs(atr[-1] - expected) < 1e-9


def test_returns_and_momentum():
    r = ind.returns([100, 110, 121], 1)
    assert r[0] is None and abs(r[1] - 0.1) < 1e-9 and abs(r[2] - 0.1) < 1e-9
    assert abs(ind.momentum([100, 110, 121], 2)[2] - 0.21) < 1e-9


def test_rolling_max_min_and_relative_volume():
    assert ind.rolling_max([1, 3, 2, 5, 4], 3)[2:] == [3, 5, 5]
    assert ind.rolling_min([1, 3, 2, 5, 4], 3)[2:] == [1, 2, 2]
    rv = ind.relative_volume([100, 100, 100, 200], 3)
    assert rv[3] == 2.0


def test_efficiency_ratio_straight_line_is_one():
    er = ind.efficiency_ratio([float(i) for i in range(30)], 10)
    assert abs(er[-1] - 1.0) < 1e-9
    choppy = [100 + (1 if i % 2 else -1) for i in range(30)]
    assert ind.efficiency_ratio(choppy, 10)[-1] < 0.2


def test_trend_strength_sign():
    assert ind.trend_strength([float(i) for i in range(40)], 20)[-1] > 0.9
    assert ind.trend_strength([float(40 - i) for i in range(40)], 20)[-1] < -0.9


def test_max_drawdown_known_values():
    mdd, longest = ind.max_drawdown([100, 120, 90, 100, 130, 110])
    assert abs(mdd - 0.25) < 1e-9  # 120 -> 90
    assert longest == 2


def test_support_resistance_levels():
    bars = trending_bars(100, step=0.0)
    support, resistance = ind.support_resistance(bars, lookback=60)
    assert isinstance(support, list) and isinstance(resistance, list)


def test_indicators_are_causal():
    """Changing future values must not change earlier indicator values."""
    base = [100 + math.sin(i / 5) * 10 for i in range(100)]
    changed = base[:80] + [500.0] * 20
    for fn in (lambda v: ind.sma(v, 10), lambda v: ind.ema(v, 10), lambda v: ind.rsi(v, 14), lambda v: ind.momentum(v, 5), lambda v: ind.efficiency_ratio(v, 10), lambda v: ind.historical_volatility(v, 20)):
        a, b = fn(base), fn(changed)
        assert a[:80] == b[:80]
