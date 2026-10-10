"""FastAPI application: JSON API plus the static single-page dashboard."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..analysis import indicators as ind
from ..analysis.regime import REGIME_LABELS, REGIME_RULES_TEXT, classify_regime
from ..backtest import BacktestConfig, compute_metrics, run_backtest
from ..backtest.metrics import METRIC_EXPLANATIONS
from ..config import settings
from ..data.csv_importer import CsvFormatError, import_csv_text
from ..data.providers.base import ProviderError
from ..logging_setup import configure_logging
from ..models import AssetClass, DataStatus, utcnow
from ..paper import PaperTradingEngine
from ..ranking import scan_opportunities
from ..ranking.evidence import clear_evidence_cache
from ..ranking.ranker import COMPONENT_EXPLANATIONS, WEIGHTS
from ..research import parameter_search, walk_forward
from ..research.evaluation import evaluate_strategy_health
from ..risk import AccountState, RiskSettings
from ..risk.costs import COST_EXPLANATIONS
from ..risk.settings import RISK_SETTING_EXPLANATIONS
from ..strategies import get_strategy, list_strategies
from .glossary import GLOSSARY
from .services import LIVE_CAPITAL, get_services, parse_dt

log = logging.getLogger("smarttrade.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_path)
    app = FastAPI(title="SmartTrade AI", version=__version__)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(KeyError)
    async def _key_error(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(exc.args[0]) if exc.args else "Not found"})

    # ------------------------------------------------------------------ system
    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return get_services().system_status()

    @app.get("/api/glossary")
    def glossary() -> dict[str, str]:
        return GLOSSARY

    @app.get("/api/logs")
    def logs(limit: int = 200, category: str | None = None) -> list[dict[str, Any]]:
        return get_services().db.list_logs(limit=min(limit, 1000), category=category)

    # --------------------------------------------------------------- providers
    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        svc = get_services()
        out = []
        for p in svc.registry.all():
            info = p.info().as_dict()
            info["symbols"] = p.available_symbols()
            out.append(info)
        return out

    # ---------------------------------------------------------------- datasets
    @app.get("/api/datasets")
    def datasets() -> list[dict[str, Any]]:
        svc = get_services()
        svc.refresh_dataset_statuses()
        return [_dataset_dict(d) for d in svc.db.list_datasets()]

    @app.post("/api/datasets/load-samples")
    def load_samples() -> dict[str, Any]:
        svc = get_services()
        provider = svc.registry.get("bundled-sample")
        loaded = []
        for s in provider.available_symbols():
            result = provider.fetch(s["symbol"], AssetClass(s["asset_class"]))
            ds = svc.db.upsert_dataset(result.dataset, result.bars)
            loaded.append(_dataset_dict(ds))
        clear_evidence_cache()
        svc.scan_cache.clear()
        svc.db.add_log("INFO", "data", f"Loaded {len(loaded)} bundled SIMULATED sample datasets.")
        return {"loaded": loaded}

    @app.post("/api/datasets/fetch")
    def fetch_dataset(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        provider_key = payload.get("provider")
        symbol = (payload.get("symbol") or "").strip()
        if not provider_key or not symbol:
            raise HTTPException(400, "provider and symbol are required")
        asset_class = AssetClass(payload.get("asset_class", "stock"))
        provider = svc.registry.get(provider_key)
        try:
            result = provider.fetch(symbol, asset_class)
        except ProviderError as exc:
            svc.db.add_log("ERROR", "data", f"Fetch {symbol} from {provider_key} failed: {exc}")
            raise HTTPException(502, str(exc))
        ds = svc.db.upsert_dataset(result.dataset, result.bars)
        clear_evidence_cache()
        svc.scan_cache.clear()
        svc.db.add_log("INFO", "data", f"Fetched {symbol} from {provider.name}: {ds.bar_count} bars, status {ds.status.value}.", result.validation)
        return {"dataset": _dataset_dict(ds), "validation": result.validation}

    @app.post("/api/datasets/import-csv")
    async def import_csv(
        file: UploadFile = File(...),
        symbol: str = Form(...),
        name: str = Form(""),
        asset_class: str = Form("stock"),
        interval: str = Form("1d"),
        timezone_name: str = Form("UTC"),
    ) -> dict[str, Any]:
        svc = get_services()
        raw = await file.read()
        if len(raw) > 50_000_000:
            raise HTTPException(413, "File too large (limit 50 MB).")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        try:
            result = import_csv_text(text, symbol, name or symbol, AssetClass(asset_class), interval, source="csv-import", source_tz=timezone_name)
        except CsvFormatError as exc:
            svc.db.add_log("ERROR", "data", f"CSV import for {symbol} failed: {exc}")
            raise HTTPException(400, str(exc))
        except Exception as exc:  # zoneinfo errors etc.
            raise HTTPException(400, f"Import failed: {exc}")
        if not result.bars:
            raise HTTPException(400, "No valid rows after validation: " + "; ".join(result.report.issues))
        settings.import_dir.mkdir(parents=True, exist_ok=True)
        (settings.import_dir / f"{symbol.upper()}_{utcnow().strftime('%Y%m%dT%H%M%S')}.csv").write_text(text, encoding="utf-8")
        ds = svc.db.upsert_dataset(result.dataset, result.bars)
        clear_evidence_cache()
        svc.scan_cache.clear()
        svc.db.add_log("INFO", "data", f"Imported CSV for {ds.symbol}: {ds.bar_count} bars accepted.", result.report.as_dict())
        return {"dataset": _dataset_dict(ds), "validation": result.report.as_dict()}

    @app.delete("/api/datasets/{dataset_id}")
    def delete_dataset(dataset_id: int) -> dict[str, Any]:
        svc = get_services()
        ds = svc.db.get_dataset(dataset_id)
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        svc.db.delete_dataset(dataset_id)
        clear_evidence_cache()
        svc.scan_cache.clear()
        return {"deleted": dataset_id}

    @app.get("/api/datasets/{dataset_id}/bars")
    def dataset_bars(dataset_id: int, limit: int = 400, end: str | None = None) -> dict[str, Any]:
        svc = get_services()
        ds = svc.db.get_dataset(dataset_id)
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        bars = svc.db.get_bars(dataset_id, end=parse_dt(end))
        bars = bars[-limit:] if limit else bars
        return {"dataset": _dataset_dict(ds), "bars": [b.as_dict() for b in bars]}

    @app.get("/api/datasets/{dataset_id}/analysis")
    def dataset_analysis(dataset_id: int, as_of: str | None = None) -> dict[str, Any]:
        svc = get_services()
        ds = svc.db.get_dataset(dataset_id)
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        bars = svc.db.get_bars(dataset_id, end=parse_dt(as_of))
        if len(bars) < 30:
            return {"dataset": _dataset_dict(ds), "error": f"Only {len(bars)} bars; at least 30 are needed for indicators."}
        closes = [b.close for b in bars]
        vols = [b.volume for b in bars]
        crypto = ds.asset_class == AssetClass.CRYPTO
        last = -1
        series = {
            "sma20": ind.sma(closes, 20), "sma50": ind.sma(closes, 50), "sma200": ind.sma(closes, 200),
            "ema20": ind.ema(closes, 20), "rsi14": ind.rsi(closes, 14), "atr14": ind.atr(bars, 14),
            "momentum20": ind.momentum(closes, 20), "momentum60": ind.momentum(closes, 60),
            "rel_volume20": ind.relative_volume(vols, 20), "hist_vol20": ind.historical_volatility(closes, 20, 365 if crypto else 252),
            "trend_strength20": ind.trend_strength(closes, 20), "return_5": ind.returns(closes, 5), "return_20": ind.returns(closes, 20), "return_120": ind.returns(closes, 120),
        }
        latest = {k: (round(v[last], 6) if v[last] is not None else None) for k, v in series.items()}
        support, resistance = ind.support_resistance(bars[-120:], lookback=120)
        regime = classify_regime(bars)
        return {
            "dataset": _dataset_dict(ds),
            "as_of": bars[-1].ts.isoformat(),
            "last_close": bars[-1].close,
            "indicators": latest,
            "support": [round(s, 4) for s in support[-5:]],
            "resistance": [round(r, 4) for r in resistance[:5]],
            "regime": regime.as_dict(),
            "regime_rules": REGIME_RULES_TEXT,
            "chart": {
                "bars": [b.as_dict() for b in bars[-250:]],
                "sma50": [round(v, 4) if v is not None else None for v in series["sma50"][-250:]],
                "ema20": [round(v, 4) if v is not None else None for v in series["ema20"][-250:]],
            },
            "data_note": _status_text(ds.status),
        }

    @app.post("/api/datasets/benchmark")
    def set_benchmark(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        did = int(payload.get("dataset_id"))
        if svc.db.get_dataset(did) is None:
            raise HTTPException(404, "Dataset not found")
        svc.db.set_setting("benchmark_dataset_id", did)
        return {"benchmark_dataset_id": did}

    # -------------------------------------------------------------- strategies
    @app.get("/api/strategies")
    def strategies() -> list[dict[str, Any]]:
        svc = get_services()
        out = []
        for s in list_strategies():
            d = s.describe()
            d["current_params"] = svc.strategy_params(s.key)
            d["status"] = svc.strategy_status(s.key)
            d["health"] = evaluate_strategy_health(svc.paper_trades_for_strategy(s.key))
            out.append(d)
        return out

    @app.get("/api/strategies/{key}/versions")
    def strategy_versions(key: str) -> list[dict[str, Any]]:
        get_strategy(key)
        return get_services().strategy_versions(key)

    @app.put("/api/strategies/{key}/params")
    def set_params(key: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        get_strategy(key)
        svc = get_services()
        result = svc.save_strategy_params(key, payload.get("params", {}), payload.get("reason") or "Parameters changed by user.")
        clear_evidence_cache()
        svc.scan_cache.clear()
        return result

    @app.put("/api/strategies/{key}/status")
    def set_status(key: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        get_strategy(key)
        return get_services().set_strategy_status(key, payload.get("status", "active"), payload.get("reason") or "Changed by user.")

    @app.post("/api/strategies/monitor")
    def run_monitor() -> list[dict[str, Any]]:
        return get_services().apply_health_monitor()

    # --------------------------------------------------------------------- scan
    @app.post("/api/scan")
    def scan(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        svc = get_services()
        rs = svc.risk_settings()
        as_of = parse_dt(payload.get("as_of"))
        dataset_ids = payload.get("dataset_ids") or None
        account_id = payload.get("account_id")
        if account_id:
            engine = PaperTradingEngine(svc.db)
            account = engine._account_state(int(account_id), rs)
            account_note = f"Sized for paper account #{account_id} (simulated equity ${account.equity:.2f})."
        else:
            size = float(payload.get("example_balance") or rs.example_account_size)
            account = AccountState(equity=size, cash=size, starting_balance=size, day_start_equity=size)
            account_note = f"Sized for an EXAMPLE account of ${size:.2f}. No money has been deposited."
        sets = svc.datasets_with_bars(dataset_ids)
        if not sets:
            raise HTTPException(400, "No datasets available. Load the bundled samples or import a CSV first.")
        bench_ds = svc.benchmark_dataset()
        bench = next(((d, b) for d, b in sets if bench_ds and d.id == bench_ds.id), None)
        if bench is None and bench_ds is not None:
            bench = (bench_ds, svc.db.get_bars(bench_ds.id))
        result = scan_opportunities(sets, svc.active_strategies(), account, rs, as_of=as_of, benchmark=bench,
                                    paused_strategies=svc.paused_strategies(), cache=svc.scan_cache)
        out = result.as_dict()
        out["account_note"] = account_note
        out["ranking_method"] = {"weights": WEIGHTS, "explanations": COMPONENT_EXPLANATIONS}
        out["live_note"] = (
            "No real-time data source is connected. Candidates are based on the latest stored bars and their data status."
        )
        statuses = {d.status.value for d, _ in sets}
        out["data_statuses"] = sorted(statuses)
        run_id = svc.db.insert_json_row(
            "scan_runs", created_at=utcnow().isoformat(), as_of=out["as_of"], summary=out["summary"],
            candidates=out["candidates"], rejected=[{k: v for k, v in r.items() if k != "checks"} for r in result.rejected],
        )
        out["scan_id"] = run_id
        svc.db.add_log("INFO", "scan", result.summary["message"], {"scan_id": run_id, **result.summary})
        for r in result.rejected:
            if r.get("stage") in ("risk", "regime", "evidence"):
                svc.db.add_log("INFO", "rejected", f"{r['symbol']} {r.get('strategy', '')}: {r['reason']}", {"scan_id": run_id, "stage": r["stage"]})
        return out

    @app.get("/api/scan/latest")
    def latest_scan() -> dict[str, Any]:
        svc = get_services()
        row = svc.db.query_one("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
        if row is None:
            return {"scan_id": None, "candidates": [], "rejected": [], "summary": {"message": "No scan has been run yet."}}
        return {
            "scan_id": row["id"], "created_at": row["created_at"], "as_of": row["as_of"],
            "summary": json.loads(row["summary"]), "candidates": json.loads(row["candidates"]), "rejected": json.loads(row["rejected"]),
        }

    # ---------------------------------------------------------------- backtest
    @app.post("/api/backtest")
    def backtest(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        result = _run_backtest(svc, payload)
        run_id = svc.db.insert_json_row(
            "backtest_runs", created_at=utcnow().isoformat(), dataset_id=int(payload["dataset_id"]), strategy=payload["strategy"],
            params=result["params"], start_ts=result["start_ts"], end_ts=result["end_ts"], starting_balance=result["starting_balance"],
            result={k: v for k, v in result.items() if k not in ("equity_curve",)},
        )
        result["backtest_id"] = run_id
        svc.db.add_log("INFO", "backtest", f"Backtest {payload['strategy']} on {result['symbol']}: {result['metrics']['trades']} trades, net ${result['metrics']['net_profit']:.2f}.", {"backtest_id": run_id})
        return result

    @app.post("/api/backtest/compare")
    def backtest_compare(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        keys = payload.get("strategies") or [s.key for s in list_strategies()]
        results = []
        for key in keys:
            sub = {**payload, "strategy": key, "params": (payload.get("params_by_strategy") or {}).get(key)}
            r = _run_backtest(svc, sub)
            results.append({
                "strategy": key, "name": get_strategy(key).name, "metrics": r["metrics"],
                "equity_curve": r["equity_curve"][:: max(1, len(r["equity_curve"]) // 300)], "notes": r["notes"],
            })
        return {"symbol": results[0]["metrics"].get("symbol") if results else None, "results": results,
                "disclaimer": "Historical simulation on the same period and balance. Not a forecast."}

    @app.get("/api/backtests")
    def list_backtests(limit: int = 30) -> list[dict[str, Any]]:
        svc = get_services()
        rows = svc.db.query("SELECT id, created_at, dataset_id, strategy, params, start_ts, end_ts, starting_balance, result FROM backtest_runs ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            res = json.loads(r["result"])
            out.append({
                "id": r["id"], "created_at": r["created_at"], "dataset_id": r["dataset_id"], "symbol": res.get("symbol"),
                "strategy": r["strategy"], "params": json.loads(r["params"]), "start_ts": r["start_ts"], "end_ts": r["end_ts"],
                "starting_balance": r["starting_balance"], "metrics": res.get("metrics", {}),
            })
        return out

    @app.get("/api/backtests/{run_id}")
    def get_backtest(run_id: int) -> dict[str, Any]:
        svc = get_services()
        row = svc.db.query_one("SELECT * FROM backtest_runs WHERE id=?", (run_id,))
        if row is None:
            raise HTTPException(404, "Backtest not found")
        return {"id": row["id"], "created_at": row["created_at"], **json.loads(row["result"])}

    # ---------------------------------------------------------------- research
    @app.post("/api/research")
    def research(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        ds = svc.db.get_dataset(int(payload["dataset_id"]))
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        strategy = get_strategy(payload["strategy"])
        grid = {k: v for k, v in (payload.get("grid") or {}).items() if isinstance(v, list) and v}
        bars = svc.db.get_bars(ds.id)
        rs = svc.risk_settings()
        mode = payload.get("mode", "split")
        if mode == "walkforward":
            result = walk_forward(bars, strategy, grid, ds.symbol, ds.asset_class, rs,
                                  train_bars=int(payload.get("train_bars", 300)), test_bars=int(payload.get("test_bars", 120)))
        else:
            result = parameter_search(bars, strategy, grid, ds.symbol, ds.asset_class, rs)
        if "error" in result:
            raise HTTPException(400, result["error"])
        result["mode"] = mode
        result["data_status"] = ds.status.value
        run_id = svc.db.insert_json_row("research_runs", created_at=utcnow().isoformat(), strategy=strategy.key, dataset_id=ds.id, result=result)
        result["research_id"] = run_id
        svc.db.add_log("INFO", "research", f"Research ({mode}) for {strategy.key} on {ds.symbol}: {result.get('combinations_tested', result.get('combinations_per_fold'))} combinations.", {"research_id": run_id, "warnings": result.get("warnings")})
        return result

    @app.get("/api/research")
    def list_research(limit: int = 20) -> list[dict[str, Any]]:
        svc = get_services()
        rows = svc.db.query("SELECT id, created_at, strategy, dataset_id, result FROM research_runs ORDER BY id DESC LIMIT ?", (limit,))
        out = []
        for r in rows:
            res = json.loads(r["result"])
            out.append({"id": r["id"], "created_at": r["created_at"], "strategy": r["strategy"], "dataset_id": r["dataset_id"],
                        "symbol": res.get("symbol"), "mode": res.get("mode"), "warnings": res.get("warnings"),
                        "combinations": res.get("combinations_tested", res.get("combinations_per_fold")),
                        "summary": res.get("summary") or (res.get("best") or {}).get("test")})
        return out

    # -------------------------------------------------------------------- risk
    @app.get("/api/risk")
    def risk() -> dict[str, Any]:
        svc = get_services()
        rs = svc.risk_settings()
        return {
            "settings": rs.as_dict(),
            "explanations": RISK_SETTING_EXPLANATIONS,
            "cost_models": {k: v.as_dict() for k, v in svc.cost_models().items()},
            "cost_explanations": COST_EXPLANATIONS,
            "example_note": "These limits describe an EXAMPLE account. Dollar limits scale with the balance of each backtest or paper account.",
            "hard_rules": [
                "Strategies cannot bypass the risk manager; every proposed trade passes the same checks.",
                "Position size is never rounded up beyond the permitted risk.",
                "Position size is never increased to recover losses; no averaging down; no martingale.",
                "At the daily loss limit, no new positions are opened for the rest of the day.",
                "At the equity floor or cumulative loss limit, the account halts until you review it.",
                "Real losses can exceed planned losses because of gaps, slippage and execution failures.",
                "Live trading and automated live execution are disabled and cannot be enabled in this version.",
            ],
        }

    @app.put("/api/risk")
    def update_risk(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        if "settings" in payload:
            svc.save_risk_settings(payload["settings"])
        if "cost_models" in payload:
            svc.save_cost_models(payload["cost_models"])
        clear_evidence_cache()
        return risk()

    # ------------------------------------------------------------------- paper
    @app.get("/api/paper/accounts")
    def paper_accounts() -> list[dict[str, Any]]:
        engine = PaperTradingEngine(get_services().db)
        return [engine.summary(a.id) for a in engine.list_accounts()]

    @app.post("/api/paper/accounts")
    def create_paper_account(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        svc = get_services()
        engine = PaperTradingEngine(svc.db)
        name = (payload.get("name") or "Paper account").strip()
        balance = float(payload.get("starting_balance", 50))
        start_ts = parse_dt(payload.get("start_ts"))
        if start_ts is None:
            # Default: start the clock so that 120 bars of warm-up history exist (replay) or at the latest bar.
            bench = svc.benchmark_dataset()
            if bench and payload.get("mode", "replay") == "replay":
                bars = svc.db.get_bars(bench.id)
                if len(bars) > 200:
                    start_ts = bars[-200].ts
        acc = engine.create_account(name, balance, payload.get("mode", "replay"), start_ts)
        return engine.summary(acc.id)

    @app.get("/api/paper/accounts/{account_id}")
    def paper_account(account_id: int) -> dict[str, Any]:
        engine = PaperTradingEngine(get_services().db)
        s = engine.summary(account_id)
        s["closed_positions"] = engine.positions(account_id, "closed")
        s["pending_entries"] = get_services().db.get_setting(f"paper_pending_{account_id}", []) or []
        return s

    @app.delete("/api/paper/accounts/{account_id}")
    def delete_paper_account(account_id: int) -> dict[str, Any]:
        engine = PaperTradingEngine(get_services().db)
        engine.get_account(account_id)
        engine.delete_account(account_id)
        return {"deleted": account_id}

    @app.post("/api/paper/accounts/{account_id}/advance")
    def paper_advance(account_id: int, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        svc = get_services()
        engine = PaperTradingEngine(svc.db)
        steps = max(1, min(int(payload.get("steps", 1)), 500))
        dataset_ids = payload.get("dataset_ids") or [d.id for d in svc.db.list_datasets()]
        if not dataset_ids:
            raise HTTPException(400, "No datasets available.")
        bench = svc.benchmark_dataset()
        result = engine.advance(account_id, [int(d) for d in dataset_ids], svc.active_strategies(), svc.risk_settings(),
                                steps=steps, benchmark_dataset_id=bench.id if bench else None, paused_strategies=svc.paused_strategies())
        svc.apply_health_monitor()
        return result

    @app.post("/api/paper/accounts/{account_id}/resume")
    def paper_resume(account_id: int) -> dict[str, Any]:
        engine = PaperTradingEngine(get_services().db)
        engine.resume_after_review(account_id)
        return engine.summary(account_id)

    @app.post("/api/paper/accounts/{account_id}/positions/{position_id}/close")
    def paper_close(account_id: int, position_id: int) -> dict[str, Any]:
        svc = get_services()
        engine = PaperTradingEngine(svc.db)
        engine.manual_close(position_id, svc.risk_settings())
        return engine.summary(account_id)

    @app.post("/api/paper/accounts/{account_id}/open")
    def paper_open(account_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Open a position from a scan candidate at the latest close available at the account clock."""
        svc = get_services()
        engine = PaperTradingEngine(svc.db)
        cand = payload.get("candidate") or {}
        rs = svc.risk_settings()
        acc = engine.get_account(account_id)
        ds = svc.db.get_dataset(int(cand.get("dataset_id", 0)))
        if ds is None:
            raise HTTPException(404, "Dataset not found")
        bars = svc.db.get_bars(ds.id, end=acc.clock_ts)
        if not bars:
            raise HTTPException(400, "No price available at the account clock.")
        state = engine._account_state(account_id, rs)
        from ..models import Direction, Signal
        from ..risk import RiskManager

        signal = Signal(strategy=cand["strategy"], direction=Direction.LONG, entry_price=float(cand["entry_price"]),
                        stop_price=float(cand["stop_price"]), target_price=float(cand["target_price"]), entry_conditions="manual")
        decision = RiskManager(rs).evaluate(signal, state, ds.asset_class, ds.symbol, data_quality=ds.quality_score)
        if not decision.approved or decision.sizing is None:
            raise HTTPException(400, "Risk manager rejected the trade: " + "; ".join(decision.reasons))
        cand = {**cand, "quantity": decision.sizing.quantity, "potential_loss": decision.sizing.planned_loss}
        costs = RiskManager(rs).cost_model(ds.asset_class)
        fill = bars[-1].close * (1 + costs.spread_pct + costs.slippage_pct)
        engine.open_position_from_candidate(account_id, cand, fill, bars[-1].ts, rs)
        return engine.summary(account_id)

    # ------------------------------------------------------------- performance
    @app.get("/api/performance")
    def performance() -> dict[str, Any]:
        svc = get_services()
        per_strategy = []
        for s in list_strategies():
            trades = svc.paper_trades_for_strategy(s.key)
            health = evaluate_strategy_health(trades)
            latest_bt = svc.db.query("SELECT result FROM backtest_runs WHERE strategy=? ORDER BY id DESC LIMIT 5", (s.key,))
            bts = []
            for r in latest_bt:
                res = json.loads(r["result"])
                bts.append({"symbol": res.get("symbol"), "metrics": {k: res["metrics"].get(k) for k in ("trades", "net_profit", "win_rate", "profit_factor", "expectancy_r", "max_drawdown_pct", "conclusive")}})
            per_strategy.append({
                "strategy": s.key, "name": s.name, "status": svc.strategy_status(s.key),
                "paper": {"trades": len(trades), "health": health},
                "recent_backtests": bts,
            })
        engine = PaperTradingEngine(svc.db)
        accounts = [engine.summary(a.id) for a in engine.list_accounts()]
        return {
            "live": {"equity": LIVE_CAPITAL, "note": "No live trading in this version."},
            "paper_accounts": accounts,
            "per_strategy": per_strategy,
            "metric_explanations": METRIC_EXPLANATIONS,
            "disclaimer": "Historical (backtest) performance and paper-trading performance are simulations. Neither is real money nor a guarantee of future results.",
        }

    # -------------------------------------------------------------------- web
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

    return app


# ----------------------------------------------------------------------- helpers
def _status_text(status: DataStatus) -> str:
    return {
        DataStatus.LIVE: "LIVE data.",
        DataStatus.DELAYED: "DELAYED: end-of-day data from a free provider; not real-time.",
        DataStatus.HISTORICAL: "HISTORICAL: this is past data, not current prices.",
        DataStatus.SIMULATED: "SIMULATED: synthetic sample data for learning and testing; not real prices.",
        DataStatus.UNAVAILABLE: "UNAVAILABLE.",
    }[status]


def _dataset_dict(ds) -> dict[str, Any]:
    return {
        "id": ds.id, "symbol": ds.symbol, "name": ds.name, "asset_class": ds.asset_class.value, "interval": ds.interval,
        "source": ds.source, "status": ds.status.value, "status_note": _status_text(ds.status),
        "retrieved_at": ds.retrieved_at.isoformat(), "timezone": ds.timezone, "bar_count": ds.bar_count,
        "first_ts": ds.first_ts.isoformat() if ds.first_ts else None, "last_ts": ds.last_ts.isoformat() if ds.last_ts else None,
        "quality_score": ds.quality_score, "quality_notes": ds.quality_notes, "is_sample": ds.is_sample,
    }


def _run_backtest(svc, payload: dict[str, Any]) -> dict[str, Any]:
    ds = svc.db.get_dataset(int(payload["dataset_id"]))
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    strategy = get_strategy(payload["strategy"])
    params = payload.get("params") or svc.strategy_params(strategy.key)
    balance = float(payload.get("starting_balance") or 1000.0)
    if balance <= 0:
        raise HTTPException(400, "starting_balance must be positive")
    bars = svc.db.get_bars(ds.id)
    bench = svc.benchmark_dataset()
    bench_bars = svc.db.get_bars(bench.id) if (bench and bench.id != ds.id and payload.get("use_benchmark", True)) else None
    rs = svc.risk_settings()
    cfg = BacktestConfig(
        starting_balance=balance, params=params, risk_settings=rs, cost_model=svc.cost_models().get(ds.asset_class.value),
        start=parse_dt(payload.get("start")), end=parse_dt(payload.get("end")), benchmark_bars=bench_bars,
    )
    result = run_backtest(bars, strategy, ds.symbol, ds.asset_class, cfg)
    metrics = compute_metrics(result.trades, result.equity_curve, balance, result.regimes, 365 if ds.asset_class == AssetClass.CRYPTO else 252)
    metrics["symbol"] = ds.symbol
    out = result.as_dict()
    out["metrics"] = metrics
    out["dataset"] = _dataset_dict(ds)
    out["data_status"] = ds.status.value
    out["risk_settings_used"] = rs.scaled_for_balance(balance).as_dict()
    out["assumptions"] = [
        "Decisions use only data up to each bar's close; entries fill at the next bar's open plus slippage.",
        "Stops are checked against each bar's low; a gap below the stop fills at the open (worse than planned).",
        "If both stop and target are touched in one bar, the stop is assumed to hit first.",
        "One position at a time per backtest; fees, spread and slippage are estimates from the cost model.",
        "Dollar risk limits are scaled to the simulated starting balance.",
        "Any position still open at the end is closed at the final close.",
        f"Data status: {ds.status.value}. " + _status_text(ds.status),
        "Past performance, simulated or real, does not guarantee future results.",
    ]
    out["regime_labels"] = {k.value: v for k, v in REGIME_LABELS.items()}
    return out


app = create_app()
