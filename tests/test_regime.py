from smarttrade.analysis.regime import classify_regime, regime_series
from smarttrade.models import Regime
from tests.conftest import make_bars, trending_bars


def test_insufficient_data_is_unclear():
    res = classify_regime(trending_bars(50))
    assert res.regime == Regime.UNCLEAR
    assert "at least 120" in res.evidence[0]


def test_clean_uptrend_detected():
    res = classify_regime(trending_bars(200, step=0.5))
    assert res.regime == Regime.TREND_UP
    assert res.confidence in ("medium", "high")
    assert res.evidence


def test_clean_downtrend_detected():
    bars = trending_bars(200, step=-0.4, start=300)
    assert classify_regime(bars).regime == Regime.TREND_DOWN


def test_series_matches_pointwise_classification():
    bars = make_bars(400, seed=5)
    series = regime_series(bars)
    assert len(series) == len(bars)
    for i in range(0, len(bars), 23):
        assert series[i] == classify_regime(bars, i).regime


def test_regime_is_causal():
    bars = make_bars(400, seed=9)
    series_full = regime_series(bars)
    series_trunc = regime_series(bars[:300])
    assert series_full[:300] == series_trunc
