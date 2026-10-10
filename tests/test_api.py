import io

CSV = "Date,Open,High,Low,Close,Volume\n" + "\n".join(
    f"2024-{1 + i // 28:02d}-{1 + i % 28:02d},{100 + i},{101 + i},{99 + i},{100.5 + i},1000" for i in range(140)
)


def test_app_launches_without_credentials_or_capital(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    s = r.json()
    assert s["live_account"]["equity"] == 0.0
    assert s["live_account"]["live_trading_enabled"] is False
    assert s["live_account"]["automated_live_execution_enabled"] is False
    assert s["live_account"]["broker_connections"] == []
    assert s["paid_api_budget"] == 0.0
    assert s["paper_accounts"] == []
    assert "UNAVAILABLE" in s["live_market_data"]


def test_index_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "SmartTrade AI" in r.text


def test_load_samples_and_scan(client):
    r = client.post("/api/datasets/load-samples")
    assert r.status_code == 200 and len(r.json()["loaded"]) == 7
    assert all(d["status"] == "SIMULATED" and d["is_sample"] for d in r.json()["loaded"])
    r = client.post("/api/scan", json={})
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body and "candidates" in body and "rejected" in body
    assert "EXAMPLE account" in body["account_note"]
    assert body["data_statuses"] == ["SIMULATED"]
    assert client.get("/api/scan/latest").json()["scan_id"] == body["scan_id"]


def test_csv_import_endpoint_and_validation_errors(client):
    r = client.post("/api/datasets/import-csv", files={"file": ("t.csv", io.BytesIO(CSV.encode()), "text/csv")},
                    data={"symbol": "tst", "name": "Test", "asset_class": "stock", "interval": "1d"})
    assert r.status_code == 200, r.text
    d = r.json()["dataset"]
    assert d["symbol"] == "TST" and d["status"] == "HISTORICAL" and d["bar_count"] == 140
    bad = client.post("/api/datasets/import-csv", files={"file": ("t.csv", io.BytesIO(b"a,b,c\n1,2,3\n"), "text/csv")}, data={"symbol": "BAD"})
    assert bad.status_code == 400
    r = client.get(f"/api/datasets/{d['id']}/analysis")
    # A perfectly straight synthetic line is both trending and unusually quiet; either reading is correct.
    assert r.status_code == 200 and r.json()["regime"]["regime"] in ("trending_up", "low_volatility")
    assert "HISTORICAL" in r.json()["data_note"]


def test_backtest_compare_and_history(client):
    client.post("/api/datasets/load-samples")
    ds = client.get("/api/datasets").json()[0]
    r = client.post("/api/backtest", json={"dataset_id": ds["id"], "strategy": "momentum", "starting_balance": 1000})
    assert r.status_code == 200
    m = r.json()["metrics"]
    for key in ("total_net_return_pct", "trades", "win_rate", "profit_factor", "expectancy", "max_drawdown_pct", "longest_losing_streak", "total_fees", "total_slippage", "sharpe_ratio", "by_regime", "by_period", "verdict"):
        assert key in m
    assert r.json()["risk_settings_used"]["daily_loss_limit"] == 20.0
    assert r.json()["assumptions"]
    assert client.get("/api/backtests").json()[0]["strategy"] == "momentum"
    r = client.post("/api/backtest/compare", json={"dataset_id": ds["id"], "starting_balance": 500})
    assert r.status_code == 200 and len(r.json()["results"]) == 4
    assert client.post("/api/backtest", json={"dataset_id": 999, "strategy": "momentum"}).status_code == 404


def test_research_endpoint_tracks_combinations(client):
    client.post("/api/datasets/load-samples")
    ds = client.get("/api/datasets").json()[3]
    r = client.post("/api/research", json={"dataset_id": ds["id"], "strategy": "trend_following", "grid": {"stop_atr": [1.5, 2.0], "target_atr": [3.0, 4.5]}})
    assert r.status_code == 200
    body = r.json()
    assert body["combinations_tested"] == 4 and body["split"]["test"]
    r = client.post("/api/research", json={"dataset_id": ds["id"], "strategy": "momentum", "mode": "walkforward", "grid": {"stop_atr": [2.0, 3.0]}})
    assert r.status_code == 200 and r.json()["summary"]["folds"] >= 1
    assert client.get("/api/research").json()[0]["mode"] == "walkforward"


def test_risk_settings_roundtrip_and_hard_limits(client):
    r = client.get("/api/risk").json()
    assert r["settings"]["risk_per_trade_pct"] == 1.0 and r["settings"]["leverage_enabled"] is False
    r = client.put("/api/risk", json={"settings": {"risk_per_trade_pct": 0.5, "max_positions": 3}})
    assert r.status_code == 200 and r.json()["settings"]["risk_per_trade_pct"] == 0.5
    r = client.put("/api/risk", json={"settings": {"leverage_enabled": True}})
    assert r.status_code == 400


def test_paper_trading_flow(client):
    client.post("/api/datasets/load-samples")
    r = client.post("/api/paper/accounts", json={"name": "Example $50", "starting_balance": 50})
    assert r.status_code == 200
    acc = r.json()["account"]
    assert acc["cash"] == 50 and "Not real money" in acc["notes"]
    r = client.post(f"/api/paper/accounts/{acc['id']}/advance", json={"steps": 40})
    assert r.status_code == 200
    body = r.json()
    assert "events" in body and body["account"]["equity"] > 0
    detail = client.get(f"/api/paper/accounts/{acc['id']}").json()
    assert "closed_positions" in detail and "pending_entries" in detail
    perf = client.get("/api/performance").json()
    assert perf["live"]["equity"] == 0.0 and len(perf["paper_accounts"]) == 1
    assert client.post("/api/paper/accounts", json={"name": "x", "starting_balance": 0}).status_code == 400
    assert client.delete(f"/api/paper/accounts/{acc['id']}").status_code == 200


def test_strategy_params_versioning_and_status(client):
    r = client.put("/api/strategies/breakout/params", json={"params": {"lookback": 30}})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["params"]["lookback"] == 30
    r = client.put("/api/strategies/breakout/status", json={"status": "paused", "reason": "test"})
    assert r.json()["status"] == "paused"
    versions = client.get("/api/strategies/breakout/versions").json()
    assert len(versions) == 2
    strategies = client.get("/api/strategies").json()
    assert next(s for s in strategies if s["key"] == "breakout")["status"]["status"] == "paused"
    assert client.get("/api/strategies/nope/versions").status_code == 404


def test_providers_and_logs(client):
    p = client.get("/api/providers").json()
    keys = {x["key"] for x in p}
    assert {"bundled-sample", "stooq", "coingecko"} <= keys
    assert all(x["requires_api_key"] is False for x in p)
    client.post("/api/datasets/load-samples")
    logs = client.get("/api/logs?category=data").json()
    assert logs and "SIMULATED" in logs[0]["message"]
    assert client.get("/api/glossary").json()["Stop"]
