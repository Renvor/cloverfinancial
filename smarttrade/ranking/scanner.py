"""Opportunity scanner.

For each dataset: validate -> regime -> strategies -> signals -> costs and
sizing -> risk checks -> historical evidence -> ranking. Every rejection is
recorded with its reason. The scanner is perfectly happy to return zero
candidates: "No qualifying trades found. Waiting for better conditions."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from ..analysis.regime import REGIME_LABELS, classify_regime
from ..models import AssetClass, Bar, DataStatus, Dataset, Regime
from ..risk import AccountState, RiskManager, RiskSettings
from ..strategies import Strategy, build_view
from ..strategies.base import atr_rank
from .calibration import calibrated_probability
from .evidence import gather_evidence
from .ranker import score_candidate

NO_TRADES_TEXT = "No qualifying trades found. Waiting for better conditions."


@dataclass
class ScanResult:
    as_of: datetime | None
    candidates: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "candidates": self.candidates,
            "rejected": self.rejected,
            "summary": self.summary,
        }


def _status_note(ds: Dataset) -> str:
    if ds.status == DataStatus.SIMULATED:
        return "This is SIMULATED sample data. The candidate is a demonstration, not a current market opportunity."
    if ds.status == DataStatus.HISTORICAL:
        return "This data is HISTORICAL (not current). The candidate shows what the system would have flagged at that time."
    if ds.status == DataStatus.DELAYED:
        return "Data is end-of-day/DELAYED. Prices may have moved since the last bar."
    if ds.status == DataStatus.LIVE:
        return "Live data."
    return "Data status unavailable."


def scan_opportunities(
    datasets: Sequence[tuple[Dataset, list[Bar]]],
    strategies: Sequence[tuple[Strategy, dict[str, Any]]],
    account: AccountState,
    risk_settings: RiskSettings,
    as_of: datetime | None = None,
    benchmark: tuple[Dataset, list[Bar]] | None = None,
    paused_strategies: set[str] | None = None,
    max_candidates: int = 10,
    cache: dict[Any, Any] | None = None,
) -> ScanResult:
    """``cache`` (optional) persists indicator/regime series across repeated scans of the
    same datasets (e.g. paper-trading replays). All cached series are causal, so using a
    series computed on the full history and reading it at the as-of index is leak-free."""
    rm = RiskManager(risk_settings)
    paused_strategies = paused_strategies or set()
    cache = cache if cache is not None else {}
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    scanned = 0
    skipped_datasets = 0
    bench_regime: Regime | None = None
    bench_label = None
    if benchmark is not None:
        b_ds, b_bars = benchmark
        b_bars = [b for b in b_bars if as_of is None or b.ts <= as_of]
        if len(b_bars) >= 120:
            bench_regime = classify_regime(b_bars).regime
            bench_label = f"{b_ds.symbol}: {REGIME_LABELS[bench_regime]}"

    latest_ts: datetime | None = None
    for ds, bars_all in datasets:
        bars = [b for b in bars_all if as_of is None or b.ts <= as_of]
        if not bars:
            skipped_datasets += 1
            rejected.append({"symbol": ds.symbol, "stage": "data", "reason": "No bars at or before the as-of date."})
            continue
        latest_ts = bars[-1].ts if latest_ts is None else max(latest_ts, bars[-1].ts)
        if ds.quality_score is not None and ds.quality_score < risk_settings.min_data_quality:
            skipped_datasets += 1
            rejected.append({"symbol": ds.symbol, "stage": "data", "reason": f"Data quality {ds.quality_score:.2f} is below the minimum {risk_settings.min_data_quality:.2f}."})
            continue
        if ds.status == DataStatus.UNAVAILABLE:
            skipped_datasets += 1
            rejected.append({"symbol": ds.symbol, "stage": "data", "reason": "Data unavailable."})
            continue
        scanned += 1
        idx = len(bars) - 1  # bars is a prefix of bars_all, so indices line up
        regime_res = classify_regime(bars)
        regime = regime_res.regime
        if regime == Regime.UNCLEAR and len(bars) < 120:
            rejected.append({"symbol": ds.symbol, "stage": "data", "reason": f"Only {len(bars)} bars; at least 120 are required for analysis."})
            continue
        avg_dollar_volume = None
        tail = bars[-20:]
        if tail and all(b.volume >= 0 for b in tail):
            avg_dollar_volume = sum(b.close * b.volume for b in tail) / len(tail)

        for strategy, params in strategies:
            params = strategy.merge_params(params)
            if strategy.key in paused_strategies:
                rejected.append({"symbol": ds.symbol, "strategy": strategy.key, "stage": "strategy", "reason": "Strategy is paused by the performance monitor."})
                continue
            if len(bars) <= strategy.warmup_bars(params):
                rejected.append({"symbol": ds.symbol, "strategy": strategy.key, "stage": "strategy", "reason": f"Needs {strategy.warmup_bars(params)} bars of history, have {len(bars)}."})
                continue
            ckey = (ds.id, strategy.key, tuple(sorted(params.items())), len(bars_all), bars_all[-1].ts)
            indicators = cache.get(ckey)
            if indicators is None:
                indicators = strategy.prepare(bars_all, params)
                cache[ckey] = indicators
            view = build_view(bars_all, indicators, idx)
            signal = strategy.entry_signal(view, params)
            if signal is None:
                rejected.append({"symbol": ds.symbol, "strategy": strategy.key, "stage": "signal", "reason": "Entry conditions not met."})
                continue
            if regime.value in strategy.unfavourable_regimes:
                rejected.append({
                    "symbol": ds.symbol, "strategy": strategy.key, "stage": "regime",
                    "reason": f"Signal found, but the current regime '{REGIME_LABELS[regime]}' is unfavourable for this strategy.",
                })
                continue
            decision = rm.evaluate(signal, account, ds.asset_class, ds.symbol, benchmark_regime=bench_regime, data_quality=ds.quality_score)
            if not decision.approved or decision.sizing is None:
                rejected.append({
                    "symbol": ds.symbol, "strategy": strategy.key, "stage": "risk",
                    "reason": "; ".join(decision.reasons) or "Rejected by risk checks.",
                    "checks": decision.checks, "signal": _signal_dict(signal),
                })
                continue
            # Historical evidence uses only bars strictly before the current bar (no leakage of the outcome).
            evidence = gather_evidence(bars[:-1], strategy, params, ds.symbol, ds.asset_class, risk_settings)
            reg_perf = evidence.by_regime.get(regime.value)
            if reg_perf and reg_perf.get("trades", 0) >= 15 and (reg_perf.get("expectancy_r") or 0) < -0.2:
                rejected.append({
                    "symbol": ds.symbol, "strategy": strategy.key, "stage": "evidence",
                    "reason": f"Historically this strategy lost money in '{REGIME_LABELS[regime]}' on this asset ({reg_perf['expectancy_r']:+.2f}R over {reg_perf['trades']} trades).",
                })
                continue
            sizing = decision.sizing.as_dict()
            rank = atr_rank(view)
            score = score_candidate(evidence.as_dict(), sizing, regime.value, rank, avg_dollar_volume, sizing["notional"])
            prob = calibrated_probability(evidence.oos_trades_detail, signal.strength)
            candidates.append({
                "symbol": ds.symbol,
                "name": ds.name,
                "asset_class": ds.asset_class.value,
                "dataset_id": ds.id,
                "data_source": ds.source,
                "data_status": ds.status.value,
                "data_status_note": _status_note(ds),
                "data_timestamp": bars[-1].ts.isoformat(),
                "strategy": strategy.key,
                "strategy_name": strategy.name,
                "direction": signal.direction.value,
                "entry_conditions": signal.entry_conditions,
                "entry_price": round(signal.entry_price, 6),
                "stop_price": round(signal.stop_price, 6),
                "target_price": round(signal.target_price, 6),
                "max_holding_bars": signal.max_holding_bars,
                "quantity": sizing["quantity"],
                "notional": sizing["notional"],
                "estimated_costs": round(sizing["estimated_fees"] + sizing["estimated_slippage"], 4),
                "estimated_fees": sizing["estimated_fees"],
                "estimated_slippage": sizing["estimated_slippage"],
                "potential_profit": sizing["planned_profit"],
                "potential_loss": sizing["planned_loss"],
                "reward_to_risk": sizing["reward_risk_after_costs"],
                "reward_to_risk_before_costs": sizing["reward_risk_before_costs"],
                "regime": regime.value,
                "regime_label": REGIME_LABELS[regime],
                "regime_evidence": regime_res.evidence,
                "benchmark_regime": bench_label,
                "evidence": signal.evidence,
                "failure_reasons": signal.failure_reasons,
                "risk_checks": decision.checks,
                "historical_evidence": evidence.as_dict(),
                "probability": prob,
                "ranking": score,
                "evidence_score": score["evidence_score"],
                "signal_strength": round(signal.strength, 3),
                "plain_summary": _plain_summary(ds, strategy, signal, sizing, regime),
                "is_signal_not_order": True,
            })

    candidates.sort(key=lambda c: c["evidence_score"], reverse=True)
    for i, c in enumerate(candidates, start=1):
        c["rank"] = i
    summary = {
        "datasets_scanned": scanned,
        "datasets_skipped": skipped_datasets,
        "strategies_evaluated": len(strategies),
        "candidates": len(candidates),
        "rejections": len(rejected),
        "benchmark_regime": bench_label,
        "message": NO_TRADES_TEXT if not candidates else f"{len(candidates)} qualifying candidate(s) found.",
        "rejections_by_stage": _count_by(rejected, "stage"),
    }
    return ScanResult(as_of=as_of or latest_ts, candidates=candidates[:max_candidates], rejected=rejected, summary=summary)


def _signal_dict(signal) -> dict[str, Any]:
    return {
        "entry_price": round(signal.entry_price, 6), "stop_price": round(signal.stop_price, 6),
        "target_price": round(signal.target_price, 6), "evidence": signal.evidence,
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it.get(key, "other")] = out.get(it.get(key, "other"), 0) + 1
    return out


def _plain_summary(ds: Dataset, strategy: Strategy, signal, sizing: dict[str, Any], regime: Regime) -> str:
    return (
        f"{strategy.name} suggests buying {sizing['quantity']:g} units of {ds.symbol} near {signal.entry_price:.2f}, "
        f"with a protective stop at {signal.stop_price:.2f} and a target at {signal.target_price:.2f}. "
        f"If the stop is hit you would lose about ${sizing['planned_loss']:.2f}; if the target is hit you would make about "
        f"${sizing['planned_profit']:.2f} after estimated costs (reward-to-risk {sizing['reward_risk_after_costs']:.1f}). "
        f"Market regime: {REGIME_LABELS[regime]}. This is a signal, not an order."
    )
