from datetime import datetime, timezone

import pytest

from smarttrade.db import Database
from smarttrade.paper import PaperTradingEngine
from smarttrade.research.evaluation import evaluate_strategy_health
from smarttrade.risk import RiskSettings
from smarttrade.strategies import list_strategies


@pytest.fixture
def db(sample_sets):
    db = Database(":memory:")
    for ds, bars in sample_sets:
        db.upsert_dataset(ds, bars)
    yield db
    db.close()


def test_paper_accounts_are_separate_from_each_other_and_from_live(db):
    eng = PaperTradingEngine(db)
    a = eng.create_account("A", 50.0)
    b = eng.create_account("B", 500.0)
    ids = [d.id for d in db.list_datasets()]
    strategies = [(s, {}) for s in list_strategies()]
    eng.advance(a.id, ids, strategies, RiskSettings(), steps=60, benchmark_dataset_id=ids[0])
    sa, sb = eng.summary(a.id), eng.summary(b.id)
    assert sb["equity"] == 500.0 and sb["closed_trades"] == 0 and sb["open_positions"] == []
    assert sa["account"]["clock_ts"] is not None and sb["account"]["clock_ts"] is None
    assert "simulated" in sa["disclaimer"].lower()
    # Live capital is always zero and never derived from paper results.
    from smarttrade.api.services import LIVE_CAPITAL

    assert LIVE_CAPITAL == 0.0


def test_replay_opens_and_closes_positions_under_risk_rules(db):
    eng = PaperTradingEngine(db)
    ids = [d.id for d in db.list_datasets()]
    acc = eng.create_account("Replay", 50.0, start_ts=datetime(2024, 6, 1, tzinfo=timezone.utc))
    out = eng.advance(acc.id, ids, [(s, {}) for s in list_strategies()], RiskSettings(), steps=150, benchmark_dataset_id=ids[0])
    types = {e["type"] for e in out["events"]}
    assert "entry" in types and "exit" in types
    closed = eng.positions(acc.id, "closed")
    assert closed
    for p in closed:
        assert p["planned_risk"] <= 0.01 * 50.0 * 1.3  # ~1% of equity (equity may have grown a little)
        assert p["data_status"] == "SIMULATED"
        assert p["exit_ts"] >= p["entry_ts"]  # same-bar stop-outs are possible (entry at open, stop intrabar)
    s = eng.summary(acc.id)
    assert abs(s["equity"] - (s["cash"] + sum(p["mark_price"] * p["quantity"] for p in s["open_positions"]))) < 1e-3  # values are rounded to 4 dp
    assert len(s["open_positions"]) <= RiskSettings().max_positions
    logs = db.list_logs(limit=1000, category="paper")
    assert any("Opened simulated" in l["message"] for l in logs)


def test_equity_floor_halts_account_until_review(db):
    eng = PaperTradingEngine(db)
    ids = [d.id for d in db.list_datasets()]
    acc = eng.create_account("Tiny", 50.0, start_ts=datetime(2024, 6, 1, tzinfo=timezone.utc))
    # Force the account below the floor by removing cash (simulating losses).
    db.execute("UPDATE paper_accounts SET cash=39.0 WHERE id=?", (acc.id,))
    out = eng.advance(acc.id, ids, [(s, {}) for s in list_strategies()], RiskSettings(), steps=3, benchmark_dataset_id=ids[0])
    assert eng.get_account(acc.id).status == "halted"
    assert any(e["type"] == "halt" for e in out["events"])
    assert not any(e["type"] == "entry" for e in out["events"])
    eng.resume_after_review(acc.id)
    assert eng.get_account(acc.id).status == "active"


def test_manual_close_and_delete(db):
    eng = PaperTradingEngine(db)
    ids = [d.id for d in db.list_datasets()]
    acc = eng.create_account("M", 50.0, start_ts=datetime(2024, 6, 1, tzinfo=timezone.utc))
    eng.advance(acc.id, ids, [(s, {}) for s in list_strategies()], RiskSettings(), steps=40, benchmark_dataset_id=ids[0])
    open_pos = eng.positions(acc.id, "open")
    if open_pos:
        eng.manual_close(open_pos[0]["id"], RiskSettings())
        assert eng.positions(acc.id, "open") == open_pos[1:]
    eng.delete_account(acc.id)
    with pytest.raises(KeyError):
        eng.get_account(acc.id)


# ------------------------------------------------------------ health monitor
def trades(rs):
    return [{"net_pnl": r * 0.5, "r_multiple": r, "fees": 0.01, "slippage": 0.01, "regime_at_entry": "trending_up"} for r in rs]


def test_health_insufficient_data_stays_active():
    h = evaluate_strategy_health(trades([-1, -1, -1]))
    assert h["status"] == "active" and "insufficient" in h["label"].lower()


def test_health_pauses_on_negative_expectancy():
    h = evaluate_strategy_health(trades([-1, 1.5] * 5 + [-1] * 15))
    assert h["status"] == "paused" and any("Expectancy" in r for r in h["reasons"])


def test_health_pauses_on_recent_deterioration():
    h = evaluate_strategy_health(trades([2.0] * 30 + [-1.0] * 15))
    assert h["status"] == "paused" and any("deteriorated" in r for r in h["reasons"])


def test_health_retains_good_strategy_and_does_not_react_to_one_loss():
    h = evaluate_strategy_health(trades([2.0, -1.0] * 15 + [-1.0]))
    assert h["status"] == "active" and "Retained" in h["reasons"][-1]
    assert h["metrics"]["by_regime"]["trending_up"]["trades"] == 31
