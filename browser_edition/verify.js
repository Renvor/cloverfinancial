// Compare the JS engine with the Python reference results on identical sample data.
const fs = require("fs");
const ST = require("./engine.js");
const data = JSON.parse(fs.readFileSync(__dirname + "/sample_data.json", "utf8"));
const ref = JSON.parse(fs.readFileSync(__dirname + "/reference.json", "utf8"));
const toBars = (rows) => rows.map(([d, o, h, l, c, v]) => ({ ts: `${d}T00:00:00+00:00`, open: o, high: h, low: l, close: c, volume: v }));
let mismatches = 0, total = 0;
for (const ds of data) {
  const bars = toBars(ds.bars);
  for (const st of ST.listStrategies()) {
    total++;
    const res = ST.runBacktest(bars, st, ds.symbol, ds.asset_class, { starting_balance: 1000 });
    const m = ST.computeMetrics(res.trades, res.equity_curve, 1000, res.regimes);
    const r = ref[`${ds.symbol}|${st.key}`];
    const first = res.trades.slice(0, 3).map((t) => [t.entry_ts.slice(0, 10), t.exit_ts.slice(0, 10), Math.round(t.net_pnl * 1e4) / 1e4]);
    const ok = m.trades === r.trades && Math.abs(m.net_profit - r.net) < 0.01 && res.rejected_signals.length === r.rej && JSON.stringify(first) === JSON.stringify(r.first) && JSON.stringify(res.regimes.slice(0, 5).concat(res.regimes.slice(-5))) === JSON.stringify(r.regimes);
    if (!ok) { mismatches++; console.log("MISMATCH", ds.symbol, st.key, { js: { trades: m.trades, net: m.net_profit, rej: res.rejected_signals.length, first }, py: r }); }
  }
}
console.log(`${total - mismatches}/${total} backtests match the Python engine exactly (trades, net P&L, rejections, first trades, regimes).`);
// Scanner smoke test
const sets = data.map((d) => [{ id: d.id, symbol: d.symbol, name: d.name, asset_class: d.asset_class, source: "bundled-sample", status: "SIMULATED", quality_score: 1 }, toBars(d.bars)]);
const acct = ST.accountState({ equity: 50, cash: 50, starting_balance: 50, day_start_equity: 50 });
const t0 = Date.now();
const scan = ST.scanOpportunities(sets, ST.listStrategies().map((s) => [s, {}]), acct, { ...ST.RISK_DEFAULTS }, null, sets[2]);
console.log("scan:", scan.summary.message, scan.summary.rejections_by_stage, scan.candidates.map((c) => [c.symbol, c.strategy, c.evidence_score, c.reward_to_risk]), `${Date.now() - t0}ms`);
const ps = ST.parameterSearch(sets[3][1], ST.getStrategy("trend_following"), { fast_ema: [10, 20], stop_atr: [1.5, 2.0, 2.5] }, "SMPL-GROWTH", "stock", { ...ST.RISK_DEFAULTS });
console.log("research:", ps.combinations_tested, ps.best && ps.best.params, ps.best && ps.best.test, ps.warnings);
process.exit(mismatches ? 1 : 0);
