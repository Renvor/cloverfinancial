from datetime import datetime, timezone

from smarttrade.models import AssetClass, DataStatus, Dataset
from smarttrade.ranking import scan_opportunities
from smarttrade.ranking.calibration import UNAVAILABLE_TEXT, calibrated_probability
from smarttrade.ranking.ranker import WEIGHTS, score_candidate
from smarttrade.ranking.scanner import NO_TRADES_TEXT
from smarttrade.risk import AccountState, RiskSettings
from smarttrade.strategies import list_strategies
from tests.conftest import trending_bars


def account(equity=50.0):
    return AccountState(equity=equity, cash=equity, starting_balance=equity, day_start_equity=equity)


def test_scanner_rejects_everything_when_no_signals():
    ds = Dataset(id=1, symbol="FLAT", name="Flat", asset_class=AssetClass.STOCK, interval="1d", source="t", status=DataStatus.SIMULATED,
                 retrieved_at=datetime.now(timezone.utc), quality_score=1.0)
    bars = trending_bars(250, step=0.0)
    res = scan_opportunities([(ds, bars)], [(s, {}) for s in list_strategies()], account(), RiskSettings())
    assert res.candidates == []
    assert res.summary["message"] == NO_TRADES_TEXT
    assert res.summary["rejections"] == len(list_strategies())


def test_scanner_skips_low_quality_and_short_datasets():
    bad = Dataset(id=1, symbol="BAD", name="Bad", asset_class=AssetClass.STOCK, interval="1d", source="t", status=DataStatus.HISTORICAL,
                  retrieved_at=datetime.now(timezone.utc), quality_score=0.2)
    short = Dataset(id=2, symbol="SHORT", name="Short", asset_class=AssetClass.STOCK, interval="1d", source="t", status=DataStatus.HISTORICAL,
                    retrieved_at=datetime.now(timezone.utc), quality_score=1.0)
    res = scan_opportunities([(bad, trending_bars(300)), (short, trending_bars(50))], [(s, {}) for s in list_strategies()], account(), RiskSettings())
    assert res.candidates == []
    stages = {r["stage"] for r in res.rejected}
    assert stages == {"data"}


def test_scanner_finds_and_ranks_candidates(sample_sets):
    strategies = [(s, {}) for s in list_strategies()]
    found = None
    for ds, bars in sample_sets:
        for n in range(len(bars) - 1, 300, -5):
            res = scan_opportunities([(ds, bars)], strategies, account(), RiskSettings(), as_of=bars[n].ts)
            if res.candidates:
                found = res
                break
        if found:
            break
    assert found is not None, "expected at least one candidate somewhere in the sample history"
    c = found.candidates[0]
    for key in ("symbol", "asset_class", "data_source", "data_timestamp", "strategy", "direction", "entry_conditions", "entry_price",
                "stop_price", "target_price", "estimated_costs", "potential_profit", "potential_loss", "reward_to_risk", "regime",
                "evidence", "failure_reasons", "ranking", "probability", "is_signal_not_order"):
        assert key in c
    assert c["reward_to_risk"] >= 2.0
    assert c["potential_loss"] <= 0.5 + 1e-6  # 1% of $50
    assert c["data_status"] == "SIMULATED" and "SIMULATED" in c["data_status_note"]
    scores = [x["evidence_score"] for x in found.candidates]
    assert scores == sorted(scores, reverse=True)


def test_scanner_honours_paused_strategies(sample_sets):
    ds, bars = sample_sets[0]
    res = scan_opportunities([(ds, bars)], [(s, {}) for s in list_strategies()], account(), RiskSettings(), paused_strategies={s.key for s in list_strategies()})
    assert res.candidates == [] and all("paused" in r["reason"] for r in res.rejected if r["stage"] == "strategy")


def test_scanner_is_causal(sample_sets):
    ds, bars = sample_sets[3]
    strategies = [(s, {}) for s in list_strategies()]
    as_of = bars[500].ts
    a = scan_opportunities([(ds, bars)], strategies, account(), RiskSettings(), as_of=as_of)
    b = scan_opportunities([(ds, bars[:501])], strategies, account(), RiskSettings())
    strip = lambda r: [(c["symbol"], c["strategy"], c["entry_price"], c["stop_price"], c["target_price"], c["quantity"]) for c in r.candidates]
    assert strip(a) == strip(b)


def test_score_components_and_weights():
    evidence = {"conclusive": True, "expectancy_r_out_of_sample": 0.5, "trades_oos": 20, "profit_factor_out_of_sample": 1.8,
                "by_regime": {"trending_up": {"trades": 10, "expectancy_r": 0.4}}, "deterioration": False}
    sizing = {"reward_risk_after_costs": 3.0, "cost_share_of_risk": 0.1, "notional": 25}
    s = score_candidate(evidence, sizing, "trending_up", 0.45, 1_000_000, 25)
    assert 0 <= s["evidence_score"] <= 100
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
    assert set(s["components"]) == set(WEIGHTS)
    worse = score_candidate({**evidence, "deterioration": True, "expectancy_r_out_of_sample": -0.4}, {**sizing, "reward_risk_after_costs": 2.0}, "range_bound", 0.95, 1_000_000, 25)
    assert worse["evidence_score"] < s["evidence_score"]
    neutral = score_candidate({"conclusive": False}, sizing, "unclear", None, None, 25)
    assert neutral["components"]["out_of_sample_expectancy"]["score"] == 0.5


def test_probability_unavailable_without_enough_calibration_data():
    p = calibrated_probability([{"win": True, "strength": 0.5}] * 5, 0.5)
    assert not p["available"] and p["text"] == UNAVAILABLE_TEXT
    good = [{"win": i % 3 != 0, "strength": 0.8} for i in range(60)] + [{"win": i % 3 == 0, "strength": 0.1} for i in range(60)]
    p2 = calibrated_probability(good, 0.8)
    assert p2["available"] and 0.5 < p2["probability"] < 0.8 and p2["sample_size"] == 60
    assert p2["interval_95"][0] < p2["probability"] < p2["interval_95"][1]
