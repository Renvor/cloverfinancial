"""Transparent ranking of qualifying candidates.

The score is a weighted sum of documented components, each scaled to 0..1
and shown to the user. It is an *evidence score*, not a probability and not
an "AI prediction".
"""

from __future__ import annotations

from typing import Any

WEIGHTS = {
    "out_of_sample_expectancy": 0.30,
    "consistency": 0.15,
    "reward_to_risk": 0.15,
    "regime_fit": 0.15,
    "cost_efficiency": 0.10,
    "volatility_liquidity": 0.10,
    "recent_performance": 0.05,
}

COMPONENT_EXPLANATIONS = {
    "out_of_sample_expectancy": "Average profit per unit of risk on historical data the strategy was not tuned on. Weak evidence scores 0.5 (neutral).",
    "consistency": "Profit factor and win-rate balance out of sample; penalises strategies that rely on one lucky trade.",
    "reward_to_risk": "Planned profit divided by planned loss after costs. 2.0 scores 0, 4.0 or better scores 1.",
    "regime_fit": "How the strategy has performed in the current market regime on this asset. Unknown scores 0.5.",
    "cost_efficiency": "Share of the planned risk that is not eaten by fees and slippage.",
    "volatility_liquidity": "Prefers moderate volatility and enough traded value for a small order to fill without moving the price.",
    "recent_performance": "Penalises strategies whose last 10 trades were clearly worse than their long-run average.",
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _scale(x: float | None, lo: float, hi: float, neutral: float = 0.5) -> float:
    if x is None:
        return neutral
    return _clamp((x - lo) / (hi - lo))


def score_candidate(
    evidence: dict[str, Any],
    sizing: dict[str, Any],
    regime: str,
    atr_pct_rank: float | None,
    dollar_volume: float | None,
    notional: float,
) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}

    conclusive = evidence.get("conclusive", False)
    exp_oos = evidence.get("expectancy_r_out_of_sample")
    oos_score = _scale(exp_oos, -0.5, 1.0) if conclusive else 0.5
    components["out_of_sample_expectancy"] = {
        "score": round(oos_score, 3),
        "detail": (f"{exp_oos:+.2f}R per trade out of sample over {evidence.get('trades_oos', 0)} trades."
                   if (exp_oos is not None and conclusive) else "Insufficient out-of-sample trades; treated as neutral (0.5)."),
    }

    pf = evidence.get("profit_factor_out_of_sample")
    cons = _scale(pf, 0.8, 2.5) if (conclusive and pf is not None) else 0.5
    components["consistency"] = {
        "score": round(cons, 3),
        "detail": f"Out-of-sample profit factor {pf:.2f}." if (pf is not None and conclusive) else "Not enough trades to judge consistency; neutral.",
    }

    rr = sizing.get("reward_risk_after_costs") or 0.0
    components["reward_to_risk"] = {
        "score": round(_scale(rr, 2.0, 4.0), 3),
        "detail": f"Reward-to-risk after costs {rr:.2f}.",
    }

    reg = (evidence.get("by_regime") or {}).get(regime)
    if reg and reg.get("trades", 0) >= 8:
        fit = _scale(reg.get("expectancy_r"), -0.5, 1.0)
        detail = f"In '{regime}' this strategy averaged {reg['expectancy_r']:+.2f}R over {reg['trades']} trades on this asset."
    else:
        fit = 0.5
        detail = f"Too few historical trades in the current regime '{regime}' to judge fit; neutral."
    components["regime_fit"] = {"score": round(fit, 3), "detail": detail}

    cost_share = sizing.get("cost_share_of_risk") or 0.0
    components["cost_efficiency"] = {
        "score": round(_clamp(1 - cost_share / 0.5), 3),
        "detail": f"Costs are {cost_share:.0%} of the planned loss.",
    }

    vol_score = 0.5
    vol_detail = "Volatility rank unavailable."
    if atr_pct_rank is not None:
        # Prefer the 0.2-0.7 band; penalise extremes.
        vol_score = 1.0 - _clamp(abs(atr_pct_rank - 0.45) / 0.45)
        vol_detail = f"Volatility rank {atr_pct_rank:.0%} (moderate is preferred)."
    liq_score = 0.5
    liq_detail = "Volume unavailable."
    if dollar_volume is not None and dollar_volume > 0:
        ratio = notional / dollar_volume if dollar_volume else 1.0
        liq_score = _clamp(1 - ratio / 0.001)  # a position above 0.1% of daily value is penalised
        liq_detail = f"Position is {ratio:.5%} of average daily traded value."
    components["volatility_liquidity"] = {
        "score": round(0.5 * vol_score + 0.5 * liq_score, 3),
        "detail": f"{vol_detail} {liq_detail}",
    }

    deterioration = evidence.get("deterioration", False)
    components["recent_performance"] = {
        "score": 0.0 if deterioration else 1.0,
        "detail": "Recent trades are clearly worse than the long-run average." if deterioration else "No recent deterioration detected.",
    }

    total = sum(WEIGHTS[k] * components[k]["score"] for k in WEIGHTS)
    return {
        "evidence_score": round(total * 100, 1),
        "components": components,
        "weights": WEIGHTS,
        "explanations": COMPONENT_EXPLANATIONS,
        "note": "The evidence score orders candidates by documented evidence. It is not a probability and not a prediction.",
    }
