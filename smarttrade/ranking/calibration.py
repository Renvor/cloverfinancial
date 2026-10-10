"""Probability calibration (shown only when it is actually supported by data).

Process (documented):
1. Collect OUT-OF-SAMPLE trades for the strategy across all scanned assets.
   In-sample trades are excluded so the estimate is evaluated on data not
   used to fit anything.
2. Bucket trades by signal strength into three equal-width bins.
3. For the bucket matching the current signal, require at least
   ``MIN_TRADES_PER_BUCKET`` trades and a Brier score at least as good as
   the naive constant predictor; otherwise report "unavailable".
4. The displayed probability is the bucket's observed win rate with a
   95% Wilson interval, so the uncertainty is visible.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

MIN_TRADES_PER_BUCKET = 30
UNAVAILABLE_TEXT = "Reliable probability estimate unavailable."


def _bucket(strength: float) -> int:
    return min(2, max(0, int(strength * 3)))


def _wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def calibrated_probability(oos_trades: Sequence[dict[str, Any]], signal_strength: float) -> dict[str, Any]:
    """``oos_trades`` items: {"win": bool, "strength": float}."""
    bucket = _bucket(signal_strength)
    in_bucket = [t for t in oos_trades if _bucket(t.get("strength", 0.0)) == bucket]
    n = len(in_bucket)
    if n < MIN_TRADES_PER_BUCKET:
        return {
            "available": False,
            "text": UNAVAILABLE_TEXT,
            "reason": f"Only {n} out-of-sample trade(s) in this signal-strength bucket; {MIN_TRADES_PER_BUCKET} are required.",
        }
    p = sum(1 for t in in_bucket if t["win"]) / n
    # Brier score of the bucket estimate vs. the naive overall base rate.
    base = sum(1 for t in oos_trades if t["win"]) / len(oos_trades)
    brier_bucket = sum((p - (1.0 if t["win"] else 0.0)) ** 2 for t in in_bucket) / n
    brier_base = sum((base - (1.0 if t["win"] else 0.0)) ** 2 for t in in_bucket) / n
    if brier_bucket > brier_base + 1e-9:
        return {
            "available": False,
            "text": UNAVAILABLE_TEXT,
            "reason": "Signal-strength buckets do not predict outcomes better than the base rate on out-of-sample data.",
        }
    lo, hi = _wilson(p, n)
    return {
        "available": True,
        "probability": round(p, 3),
        "interval_95": [round(lo, 3), round(hi, 3)],
        "sample_size": n,
        "text": f"Observed out-of-sample win rate {p:.0%} (95% range {lo:.0%}-{hi:.0%}) over {n} similar signals.",
        "reason": "Calibrated on out-of-sample trades not used to choose the strategy.",
    }
