"""Shared application services used by the API routes.

Keeps the HTTP layer thin: routes validate input, call into here, and
serialise the result.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..data.providers import ProviderRegistry, build_default_registry
from ..db import Database, get_database
from ..models import AssetClass, Bar, DataStatus, Dataset, utcnow
from ..research.evaluation import evaluate_strategy_health
from ..risk import CostModel, RiskSettings, default_cost_models
from ..strategies import Strategy, get_strategy, list_strategies

LIVE_CAPITAL = 0.0  # No brokerage is connected in this version. Never fabricated.


class Services:
    def __init__(self, db: Database | None = None, registry: ProviderRegistry | None = None):
        self.db = db or get_database()
        self.registry = registry or build_default_registry()
        self.scan_cache: dict[Any, Any] = {}

    # ------------------------------------------------------------------ risk
    def risk_settings(self) -> RiskSettings:
        return RiskSettings.from_dict(self.db.get_setting("risk_settings"))

    def save_risk_settings(self, data: dict[str, Any]) -> RiskSettings:
        rs = RiskSettings.from_dict({**self.risk_settings().as_dict(), **data})
        self.db.set_setting("risk_settings", rs.as_dict())
        self.db.add_log("INFO", "risk", "Risk settings updated.", {"settings": rs.as_dict()})
        return rs

    def cost_models(self) -> dict[str, CostModel]:
        stored = self.db.get_setting("cost_models") or {}
        base = default_cost_models()
        for k, v in stored.items():
            if k in base:
                base[k] = CostModel.from_dict(v)
        return base

    def save_cost_models(self, data: dict[str, dict[str, Any]]) -> dict[str, CostModel]:
        current = {k: v.as_dict() for k, v in self.cost_models().items()}
        for k, v in data.items():
            if k in current:
                current[k].update({kk: vv for kk, vv in v.items() if kk in current[k]})
        self.db.set_setting("cost_models", current)
        return self.cost_models()

    # ------------------------------------------------------------ strategies
    def strategy_params(self, key: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT params FROM strategy_versions WHERE strategy=? ORDER BY version DESC LIMIT 1", (key,)
        )
        strategy = get_strategy(key)
        return strategy.merge_params(json.loads(row["params"]) if row else None)

    def strategy_status(self, key: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT version, status, reason, created_at FROM strategy_versions WHERE strategy=? ORDER BY version DESC LIMIT 1", (key,)
        )
        if row is None:
            return {"version": 1, "status": "active", "reason": "Default parameters.", "since": None}
        return {"version": row["version"], "status": row["status"], "reason": row["reason"], "since": row["created_at"]}

    def save_strategy_params(self, key: str, params: dict[str, Any], reason: str = "Parameters changed by user.") -> dict[str, Any]:
        strategy = get_strategy(key)
        merged = strategy.merge_params(params)
        row = self.db.query_one("SELECT MAX(version) AS v FROM strategy_versions WHERE strategy=?", (key,))
        version = (row["v"] or 0) + 1 if row else 1
        status = self.strategy_status(key)["status"]
        self.db.execute(
            "INSERT INTO strategy_versions(strategy, version, params, status, reason, created_at) VALUES (?,?,?,?,?,?)",
            (key, version, json.dumps(merged), status, reason, utcnow().isoformat()),
        )
        self.db.add_log("INFO", "strategy", f"{strategy.name}: new parameter version {version}.", {"params": merged, "reason": reason})
        return {"version": version, "params": merged}

    def set_strategy_status(self, key: str, status: str, reason: str) -> dict[str, Any]:
        if status not in ("active", "paused", "watch"):
            raise ValueError("status must be active, paused or watch")
        params = self.strategy_params(key)
        row = self.db.query_one("SELECT MAX(version) AS v FROM strategy_versions WHERE strategy=?", (key,))
        version = (row["v"] or 0) + 1 if row else 1
        self.db.execute(
            "INSERT INTO strategy_versions(strategy, version, params, status, reason, created_at) VALUES (?,?,?,?,?,?)",
            (key, version, json.dumps(params), status, reason, utcnow().isoformat()),
        )
        self.db.add_log("WARNING" if status == "paused" else "INFO", "strategy", f"{get_strategy(key).name} set to {status}: {reason}", {"version": version})
        return self.strategy_status(key)

    def strategy_versions(self, key: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM strategy_versions WHERE strategy=? ORDER BY version", (key,))
        return [{"version": r["version"], "params": json.loads(r["params"]), "status": r["status"], "reason": r["reason"], "created_at": r["created_at"]} for r in rows]

    def active_strategies(self) -> list[tuple[Strategy, dict[str, Any]]]:
        return [(s, self.strategy_params(s.key)) for s in list_strategies()]

    def paused_strategies(self) -> set[str]:
        return {s.key for s in list_strategies() if self.strategy_status(s.key)["status"] == "paused"}

    # ----------------------------------------------------------- paper trades
    def paper_trades_for_strategy(self, key: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM paper_positions WHERE strategy=? AND status='closed' ORDER BY exit_ts", (key,)
        )
        out = []
        for r in rows:
            pnl = r["realized_pnl"] or 0.0
            out.append({
                "net_pnl": pnl, "r_multiple": (pnl / r["planned_risk"]) if r["planned_risk"] else 0.0,
                "fees": (r["entry_fees"] or 0) + (r["exit_fees"] or 0), "slippage": 0.0, "regime_at_entry": "unknown",
                "symbol": r["symbol"], "exit_ts": r["exit_ts"], "data_status": r["data_status"],
            })
        return out

    def apply_health_monitor(self) -> list[dict[str, Any]]:
        """Evaluate every strategy on its paper trades and pause/retain per documented criteria."""
        results = []
        for s in list_strategies():
            trades = self.paper_trades_for_strategy(s.key)
            health = evaluate_strategy_health(trades)
            current = self.strategy_status(s.key)
            if health["status"] == "paused" and current["status"] != "paused":
                self.set_strategy_status(s.key, "paused", "Performance monitor: " + " ".join(health["reasons"]))
            elif health["status"] == "active" and current["status"] == "paused" and current["reason"].startswith("Performance monitor"):
                self.set_strategy_status(s.key, "active", "Performance monitor: criteria satisfied again.")
            results.append({"strategy": s.key, "name": s.name, "health": health, "status": self.strategy_status(s.key)})
        return results

    # ---------------------------------------------------------------- datasets
    def datasets_with_bars(self, dataset_ids: list[int] | None = None) -> list[tuple[Dataset, list[Bar]]]:
        out = []
        for ds in self.db.list_datasets():
            if dataset_ids and ds.id not in dataset_ids:
                continue
            out.append((ds, self.db.get_bars(ds.id)))
        return out

    def benchmark_dataset(self) -> Dataset | None:
        bench_id = self.db.get_setting("benchmark_dataset_id")
        if bench_id:
            ds = self.db.get_dataset(int(bench_id))
            if ds:
                return ds
        for ds in self.db.list_datasets():
            if ds.asset_class == AssetClass.ETF:
                return ds
        return None

    def refresh_dataset_statuses(self) -> None:
        """Downgrade DELAYED datasets to HISTORICAL once their last bar is stale."""
        from ..data.providers.base import freshness_status

        for ds in self.db.list_datasets():
            if ds.status in (DataStatus.DELAYED, DataStatus.HISTORICAL) and not ds.is_sample and ds.last_ts:
                new_status = freshness_status(ds.last_ts, ds.asset_class)
                if new_status != ds.status:
                    self.db.execute("UPDATE datasets SET status=? WHERE id=?", (new_status.value, ds.id))

    def system_status(self) -> dict[str, Any]:
        self.refresh_dataset_statuses()
        datasets = self.db.list_datasets()
        statuses = {}
        for ds in datasets:
            statuses[ds.status.value] = statuses.get(ds.status.value, 0) + 1
        accounts = self.db.query("SELECT id, name, starting_balance, cash, status, clock_ts FROM paper_accounts ORDER BY id")
        last_scan = self.db.query_one("SELECT id, created_at, summary FROM scan_runs ORDER BY id DESC LIMIT 1")
        backtests = self.db.query_one("SELECT COUNT(*) AS n FROM backtest_runs")
        return {
            "app": "SmartTrade AI",
            "version": "0.1.0",
            "time": utcnow().isoformat(),
            "live_account": {
                "equity": LIVE_CAPITAL,
                "live_trading_enabled": False,
                "automated_live_execution_enabled": False,
                "broker_connections": [],
                "note": "No brokerage is connected and no real money is involved. Live trading is disabled in this version.",
            },
            "paid_api_budget": 0.0,
            "paper_accounts": [dict(a) for a in accounts],
            "datasets": {"count": len(datasets), "by_status": statuses, "sample_count": sum(1 for d in datasets if d.is_sample)},
            "strategies": [{"key": s.key, "name": s.name, **self.strategy_status(s.key)} for s in list_strategies()],
            "last_scan": {"id": last_scan["id"], "created_at": last_scan["created_at"], **json.loads(last_scan["summary"])} if last_scan else None,
            "backtests_run": backtests["n"] if backtests else 0,
            "network_providers_enabled": settings.enable_network_providers,
            "data_dir": str(settings.data_dir),
            "live_market_data": "UNAVAILABLE: no real-time data source is configured. Free providers supply end-of-day (DELAYED) data at best.",
        }


_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services()
    return _services


def set_services(svc: Services | None) -> None:
    global _services
    _services = svc


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
