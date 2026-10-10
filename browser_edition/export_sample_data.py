"""Export the bundled sample datasets and Python reference results for the browser edition."""
import json
from pathlib import Path

from smarttrade.backtest import BacktestConfig, compute_metrics, run_backtest
from smarttrade.data.sample_generator import SAMPLE_SPECS, bars_for_spec
from smarttrade.data.validation import validate_bars
from smarttrade.models import AssetClass
from smarttrade.strategies import list_strategies

HERE = Path(__file__).resolve().parent
out, ref = [], {}
for i, spec in enumerate(SAMPLE_SPECS, start=1):
    clean, _ = validate_bars(bars_for_spec(spec), "1d", is_crypto=spec["asset_class"] == AssetClass.CRYPTO)
    out.append({"id": i, "symbol": spec["symbol"], "name": spec["name"], "asset_class": spec["asset_class"].value,
                "bars": [[b.ts.strftime("%Y-%m-%d"), b.open, b.high, b.low, b.close, b.volume] for b in clean]})
    for st in list_strategies():
        res = run_backtest(clean, st, spec["symbol"], spec["asset_class"], BacktestConfig(starting_balance=1000))
        m = compute_metrics(res.trades, res.equity_curve, 1000, res.regimes)
        ref[f"{spec['symbol']}|{st.key}"] = {"trades": m["trades"], "net": m["net_profit"], "rej": len(res.rejected_signals),
            "first": [(t.entry_ts.strftime("%Y-%m-%d"), t.exit_ts.strftime("%Y-%m-%d"), round(t.net_pnl, 4)) for t in res.trades[:3]],
            "regimes": res.regimes[:5] + res.regimes[-5:]}
(HERE / "sample_data.json").write_text(json.dumps(out, separators=(",", ":")))
(HERE / "reference.json").write_text(json.dumps(ref, indent=1))
print("exported", len(out), "datasets and", len(ref), "reference results")
