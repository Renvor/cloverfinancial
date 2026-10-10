/* SmartTrade AI dashboard - plain JavaScript, no build step. */
(function () {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const content = $("#content");
  const state = { page: "dashboard", glossary: {}, datasets: [], strategies: [], risk: null };

  // ------------------------------------------------------------ helpers
  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body instanceof FormData) init.body = opts.body;
    else if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && (data.error || data.detail)) || `Request failed (${res.status})`);
    return data;
  }
  function toast(msg, kind = "") {
    const t = $("#toast"); t.textContent = msg; t.className = `toast ${kind}`;
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 5000);
  }
  function modal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
  $("#modal-close").onclick = () => $("#modal").classList.add("hidden");
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });
  // Two-click confirmation (dialogs such as confirm() are unavailable in some hosts).
  const armed = (b, label = "Click again to confirm") => { if (b.dataset.armed) { delete b.dataset.armed; return true; } b.dataset.armed = "1"; const old = b.textContent; b.textContent = label; setTimeout(() => { delete b.dataset.armed; b.textContent = old; }, 4000); return false; };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "–" : (v < 0 ? "-" : "") + "$" + Math.abs(v).toFixed(d);
  const pct = (v, d = 1) => (v === null || v === undefined || isNaN(v)) ? "–" : (v * 100).toFixed(d) + "%";
  const num = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "–" : Number(v).toFixed(d);
  const signed = (v, fmt = money) => v === null || v === undefined ? "–" : `<span class="${v >= 0 ? "pos" : "neg"}">${fmt(v)}</span>`;
  const date = (iso) => iso ? iso.slice(0, 10) : "–";
  const term = (name, label) => {
    const def = state.glossary[name];
    return def ? `<span class="term" title="${esc(def)}">${esc(label || name)}</span>` : esc(label || name);
  };
  const statusPill = (s) => {
    const map = { LIVE: "pill-green", DELAYED: "pill-amber", HISTORICAL: "pill-blue", SIMULATED: "pill-purple", UNAVAILABLE: "pill-red" };
    return `<span class="pill ${map[s] || ""}" title="${esc(state.glossary["Data status"] || "")}">${esc(s)}</span>`;
  };
  const verdictPill = (m) => {
    if (!m || !m.trades) return `<span class="pill">No trades</span>`;
    if (!m.conclusive) return `<span class="pill pill-amber">Inconclusive (${m.trades} trades)</span>`;
    return m.expectancy > 0 ? `<span class="pill pill-green">Positive expectancy</span>` : `<span class="pill pill-red">Negative expectancy</span>`;
  };
  function datasetOptions(selected) {
    return state.datasets.map((d) => `<option value="${d.id}" ${d.id == selected ? "selected" : ""}>${esc(d.symbol)} – ${esc(d.name)} [${d.status}]</option>`).join("");
  }
  function strategyOptions(selected) {
    return state.strategies.map((s) => `<option value="${s.key}" ${s.key === selected ? "selected" : ""}>${esc(s.name)}</option>`).join("");
  }

  // ------------------------------------------------------------- charts
  function lineChart(series, opts = {}) {
    // series: [{name, values:[numbers|null], color}], opts: {labels:[], height, baseline}
    const w = 720, h = opts.height || 220, padR = 12, padT = 10, padB = 26;
    const all = series.flatMap((s) => s.values.filter((v) => v !== null && v !== undefined && !isNaN(v)));
    if (!all.length) return `<div class="empty">No data to chart.</div>`;
    let min = Math.min(...all), max = Math.max(...all);
    if (opts.baseline !== undefined) { min = Math.min(min, opts.baseline); max = Math.max(max, opts.baseline); }
    if (min === max) { min -= 1; max += 1; }
    const padL = 14 + 6.5 * String(opts.fmt ? opts.fmt(max) : max.toFixed(2)).length;
    const n = Math.max(...series.map((s) => s.values.length));
    const x = (i) => padL + (i / Math.max(1, n - 1)) * (w - padL - padR);
    const y = (v) => padT + (1 - (v - min) / (max - min)) * (h - padT - padB);
    let svg = `<svg class="chart" viewBox="0 0 ${w} ${h}">`;
    for (let g = 0; g <= 4; g++) {
      const v = min + (g / 4) * (max - min), yy = y(v);
      svg += `<line x1="${padL}" x2="${w - padR}" y1="${yy}" y2="${yy}" stroke="#2a3441" stroke-width="1"/>`;
      svg += `<text x="${padL - 6}" y="${yy + 4}" fill="#8b98a8" font-size="10" text-anchor="end">${opts.fmt ? opts.fmt(v) : v.toFixed(2)}</text>`;
    }
    if (opts.baseline !== undefined) svg += `<line x1="${padL}" x2="${w - padR}" y1="${y(opts.baseline)}" y2="${y(opts.baseline)}" stroke="#8b98a8" stroke-dasharray="4 4"/>`;
    for (const s of series) {
      let d = "", started = false;
      s.values.forEach((v, i) => { if (v === null || v === undefined || isNaN(v)) { started = false; return; } d += (started ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " "; started = true; });
      svg += `<path d="${d}" fill="none" stroke="${s.color || "#3b82f6"}" stroke-width="${s.width || 1.6}"/>`;
    }
    if (opts.labels && opts.labels.length) {
      const step = Math.max(1, Math.floor(opts.labels.length / 6));
      for (let i = 0; i < opts.labels.length; i += step) svg += `<text x="${x(i)}" y="${h - 8}" fill="#8b98a8" font-size="10" text-anchor="middle">${esc(date(opts.labels[i]))}</text>`;
    }
    svg += `</svg>`;
    const legend = series.length > 1 ? `<div class="row" style="font-size:12px">${series.map((s) => `<span><span style="display:inline-block;width:10px;height:10px;background:${s.color};border-radius:2px;margin-right:4px"></span>${esc(s.name)}</span>`).join("")}</div>` : "";
    return svg + legend;
  }
  function candleChart(bars, overlays = []) {
    const w = 720, h = 260, padR = 12, padT = 10, padB = 26;
    if (!bars.length) return `<div class="empty">No bars.</div>`;
    const lows = bars.map((b) => b.low), highs = bars.map((b) => b.high);
    const min = Math.min(...lows), max = Math.max(...highs);
    const padL = 14 + 6.5 * max.toFixed(2).length;
    const n = bars.length, bw = Math.max(1, ((w - padL - padR) / n) * 0.7);
    const x = (i) => padL + ((i + 0.5) / n) * (w - padL - padR);
    const y = (v) => padT + (1 - (v - min) / (max - min || 1)) * (h - padT - padB);
    let svg = `<svg class="chart" viewBox="0 0 ${w} ${h}">`;
    for (let g = 0; g <= 4; g++) { const v = min + (g / 4) * (max - min), yy = y(v); svg += `<line x1="${padL}" x2="${w - padR}" y1="${yy}" y2="${yy}" stroke="#2a3441"/><text x="${padL - 6}" y="${yy + 4}" fill="#8b98a8" font-size="10" text-anchor="end">${v.toFixed(2)}</text>`; }
    bars.forEach((b, i) => {
      const up = b.close >= b.open, c = up ? "#22c55e" : "#ef4444";
      svg += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${c}" stroke-width="1"/>`;
      svg += `<rect x="${x(i) - bw / 2}" y="${y(Math.max(b.open, b.close))}" width="${bw}" height="${Math.max(1, Math.abs(y(b.open) - y(b.close)))}" fill="${c}"/>`;
    });
    for (const o of overlays) {
      let d = "", st = false;
      o.values.forEach((v, i) => { if (v === null || v === undefined) { st = false; return; } d += (st ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1) + " "; st = true; });
      svg += `<path d="${d}" fill="none" stroke="${o.color}" stroke-width="1.4"/>`;
    }
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) svg += `<text x="${x(i)}" y="${h - 8}" fill="#8b98a8" font-size="10" text-anchor="middle">${date(bars[i].ts)}</text>`;
    svg += `</svg>`;
    return svg + `<div class="row" style="font-size:12px">${overlays.map((o) => `<span><span style="display:inline-block;width:10px;height:10px;background:${o.color};border-radius:2px;margin-right:4px"></span>${esc(o.name)}</span>`).join("")}</div>`;
  }

  // --------------------------------------------------------------- shell
  async function loadShared() {
    const [glossary, datasets, strategies, risk] = await Promise.all([api("/api/glossary"), api("/api/datasets"), api("/api/strategies"), api("/api/risk")]);
    state.glossary = glossary; state.datasets = datasets; state.strategies = strategies; state.risk = risk;
  }
  async function renderChips() {
    try {
      const st = await api("/api/status");
      const by = st.datasets.by_status || {};
      $("#status-chips").innerHTML = [
        `<span class="pill pill-red">Live: $${st.live_account.equity.toFixed(2)} · disabled</span>`,
        `<span class="pill">Datasets: <b>${st.datasets.count}</b></span>`,
        ...Object.entries(by).map(([k, v]) => statusPill(k).replace("</span>", ` ${v}</span>`)),
        `<span class="pill">Paper accounts: <b>${st.paper_accounts.length}</b></span>`,
      ].join("");
    } catch (e) { /* ignore */ }
  }
  const PAGES = {};
  // Navigation runs one page at a time (a fast second click waits for the first render to finish).
  let goChain = Promise.resolve();
  let programmaticHash = null;
  function go(page) { goChain = goChain.then(() => renderPage(page)); return goChain; }
  async function renderPage(page) {
    state.page = page;
    programmaticHash = page;
    if (location.hash.slice(1) !== page) location.hash = page;
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === page));
    $("#page-title").textContent = $(`#nav a[data-page="${page}"]`).textContent;
    content.innerHTML = `<div class="empty">Loading…</div>`;
    try { await loadShared(); await PAGES[page](); } catch (e) { content.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    renderChips();
  }
  document.querySelectorAll("#nav a").forEach((a) => (a.onclick = () => go(a.dataset.page)));
  $("#glossary-btn").onclick = () => modal(`<h2>Glossary</h2>${Object.entries(state.glossary).map(([k, v]) => `<p><b>${esc(k)}</b><br><span class="muted">${esc(v)}</span></p>`).join("")}`);

  const noData = () => `<div class="empty">No datasets yet. Go to <button class="link" onclick="location.hash='data';">Data Sources</button> and click <b>Load bundled sample data</b>, or import a CSV.</div>`;

  // ------------------------------------------------------------ dashboard
  PAGES.dashboard = async function () {
    const [st, scan, perf] = await Promise.all([api("/api/status"), api("/api/scan/latest"), api("/api/performance")]);
    const accounts = perf.paper_accounts;
    content.innerHTML = `
      <div class="notice info">SmartTrade AI runs entirely on your computer. <b>No real money, broker, API key or subscription is involved.</b> Everything you see is research, historical simulation or paper trading. ${term("Data status", "Data status labels")} tell you whether prices are SIMULATED, HISTORICAL, DELAYED or LIVE.</div>
      <div class="spacer"></div>
      <div class="grid grid-4">
        <div class="card"><h3>Live account equity</h3><div class="stat">${money(st.live_account.equity)}</div><div class="sub">Live trading disabled · no broker connected · automated execution disabled</div></div>
        <div class="card"><h3>Paper-trading equity</h3><div class="stat">${accounts.length ? money(accounts.reduce((a, b) => a + b.equity, 0)) : "–"}</div><div class="sub">${accounts.length ? accounts.length + " simulated account(s), not real money" : "No paper account configured yet"}</div></div>
        <div class="card"><h3>Datasets</h3><div class="stat">${st.datasets.count}</div><div class="sub">${Object.entries(st.datasets.by_status).map(([k, v]) => `${k}: ${v}`).join(" · ") || "none loaded"}</div></div>
        <div class="card"><h3>Backtests run</h3><div class="stat">${st.backtests_run}</div><div class="sub">Historical simulations on your machine</div></div>
      </div>
      <div class="spacer"></div>
      <div class="grid grid-2">
        <div class="card">
          <h3>System status</h3>
          <table>
            <tr><td>Live market data</td><td>${statusPill("UNAVAILABLE")} <span class="muted">no real-time source; free providers give end-of-day data</span></td></tr>
            <tr><td>Paid API budget</td><td>$0.00</td></tr>
            <tr><td>Network providers</td><td>${st.network_providers_enabled ? "Enabled (optional, no keys)" : "Disabled"}</td></tr>
            <tr><td>Strategies</td><td>${st.strategies.map((s) => `<span class="pill ${s.status === "paused" ? "pill-red" : s.status === "watch" ? "pill-amber" : "pill-green"}">${esc(s.name)}: ${s.status}</span>`).join(" ")}</td></tr>
            <tr><td>Data directory</td><td class="muted">${esc(st.data_dir)}</td></tr>
          </table>
        </div>
        <div class="card">
          <h3>Latest opportunity scan</h3>
          ${scan.scan_id ? `<div class="stat small">${esc(scan.summary.message)}</div><div class="sub">As of ${esc(scan.as_of || "")} · ${scan.summary.datasets_scanned} datasets · ${scan.summary.rejections} rejections · benchmark: ${esc(scan.summary.benchmark_regime || "n/a")}</div>
            <ul class="plain">${(scan.candidates || []).slice(0, 5).map((c) => `<li><b>#${c.rank} ${esc(c.symbol)}</b> ${esc(c.strategy_name)} · R:R ${num(c.reward_to_risk)} · score ${c.evidence_score} ${statusPill(c.data_status)}</li>`).join("")}</ul>`
            : `<div class="empty">No scan yet. Open <b>Best Opportunities</b> and press Scan.</div>`}
        </div>
      </div>
      <div class="spacer"></div>
      <div class="card">
        <h3>Getting started (3 steps)</h3>
        <ol>
          <li><b>Data Sources</b> → Load bundled sample data (synthetic, labelled SIMULATED) or import your own CSV history.</li>
          <li><b>Backtesting</b> → pick an asset and strategy, run a historical test, read the plain-English metrics.</li>
          <li><b>Paper Trading</b> → create a simulated account (e.g. $50), then advance the clock day by day to watch the system decide, size and manage trades under the risk rules.</li>
        </ol>
        <div class="sub">The goal of a 10% day is a target, not a promise. The system is designed to say "No qualifying trades found" whenever the evidence is not there.</div>
      </div>`;
  };

  // -------------------------------------------------------- market research
  PAGES.research = async function () {
    if (!state.datasets.length) { content.innerHTML = noData(); return; }
    content.innerHTML = `
      <div class="card">
        <div class="row between"><h2>Assets available for analysis</h2><div class="sub">Quality score: 1.0 = no validation problems found</div></div>
        <table><thead><tr><th>Symbol</th><th>Name</th><th>Class</th><th>Status</th><th>Source</th><th class="num">Bars</th><th>Range</th><th class="num">Quality</th><th></th></tr></thead>
        <tbody>${state.datasets.map((d) => `<tr><td><b>${esc(d.symbol)}</b></td><td>${esc(d.name)}</td><td>${esc(d.asset_class)}</td><td>${statusPill(d.status)}</td><td class="muted">${esc(d.source)}</td><td class="num">${d.bar_count}</td><td class="muted">${date(d.first_ts)} → ${date(d.last_ts)}</td><td class="num">${num(d.quality_score)}</td><td><button class="btn small" data-analyse="${d.id}">Analyse</button></td></tr>`).join("")}</tbody></table>
      </div>
      <div class="spacer"></div>
      <div id="analysis"></div>`;
    content.querySelectorAll("[data-analyse]").forEach((b) => (b.onclick = () => showAnalysis(b.dataset.analyse)));
    showAnalysis(state.datasets[0].id);
  };
  async function showAnalysis(id) {
    const box = $("#analysis"); box.innerHTML = `<div class="empty">Analysing…</div>`;
    const a = await api(`/api/datasets/${id}/analysis`);
    if (a.error) { box.innerHTML = `<div class="notice bad">${esc(a.error)}</div>`; return; }
    const ind = a.indicators, d = a.dataset;
    box.innerHTML = `
      <div class="card">
        <div class="row between"><h2>${esc(d.symbol)} · ${esc(d.name)}</h2><div>${statusPill(d.status)} <span class="muted">${esc(a.data_note)}</span></div></div>
        <div class="sub">Last bar ${esc(a.as_of)} · last close ${num(a.last_close)} · source ${esc(d.source)} · retrieved ${esc(d.retrieved_at)}</div>
        <div class="spacer"></div>
        ${candleChart(a.chart.bars, [{ name: "EMA20", values: a.chart.ema20, color: "#60a5fa" }, { name: "SMA50", values: a.chart.sma50, color: "#f59e0b" }])}
        <div class="spacer"></div>
        <div class="grid grid-3">
          <div class="card"><h3>${term("Regime", "Market regime")}</h3><div class="stat small">${esc(a.regime.label)}</div><div class="sub">Confidence: ${esc(a.regime.confidence)} (qualitative)</div><ul class="plain">${a.regime.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul><details><summary>How regimes are assigned</summary><pre>${esc(a.regime_rules.trim())}</pre></details></div>
          <div class="card"><h3>Indicators (latest)</h3><div class="kv">
            <div><b>${term("SMA", "SMA 20 / 50 / 200")}</b>${num(ind.sma20)} / ${num(ind.sma50)} / ${num(ind.sma200)}</div>
            <div><b>${term("EMA", "EMA 20")}</b>${num(ind.ema20)}</div>
            <div><b>${term("RSI", "RSI 14")}</b>${num(ind.rsi14, 1)}</div>
            <div><b>${term("ATR", "ATR 14")}</b>${num(ind.atr14)}</div>
            <div><b>${term("Momentum", "Momentum 20 / 60")}</b>${pct(ind.momentum20)} / ${pct(ind.momentum60)}</div>
            <div><b>${term("Relative volume")}</b>${num(ind.rel_volume20)}x</div>
            <div><b>Hist. volatility (ann.)</b>${pct(ind.hist_vol20)}</div>
            <div><b>${term("Trend strength")}</b>${num(ind.trend_strength20)}</div>
            <div><b>Returns 5 / 20 / 120</b>${pct(ind.return_5)} / ${pct(ind.return_20)} / ${pct(ind.return_120)}</div>
          </div></div>
          <div class="card"><h3>${term("Support")} & ${term("Resistance")}</h3><div class="sub">Swing levels from the last 120 bars</div><p><b>Resistance above:</b> ${a.resistance.filter((r) => r > a.last_close).slice(0, 3).map((v) => num(v)).join(", ") || "none found"}</p><p><b>Support below:</b> ${a.support.filter((s) => s < a.last_close).slice(-3).map((v) => num(v)).join(", ") || "none found"}</p>
            <h3>Data quality</h3>${d.quality_notes.length ? `<ul class="plain">${d.quality_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : `<div class="muted">No validation issues found.</div>`}</div>
        </div>
      </div>`;
  }

  // ------------------------------------------------------- opportunities
  PAGES.opportunities = async function () {
    if (!state.datasets.length) { content.innerHTML = noData(); return; }
    const accounts = await api("/api/paper/accounts");
    const latest = await api("/api/scan/latest");
    content.innerHTML = `
      <div class="card">
        <div class="row">
          <label class="field">Size for<select id="scan-account"><option value="">Example account</option>${accounts.map((a) => `<option value="${a.account.id}">Paper: ${esc(a.account.name)} (${money(a.equity)})</option>`).join("")}</select></label>
          <label class="field">Example balance ($)<input type="number" id="scan-balance" value="${state.risk.settings.example_account_size}" min="1" step="1"></label>
          <label class="field">As of (optional, for historical what-if)<input type="date" id="scan-asof"></label>
          <button class="btn" id="scan-btn">Scan for opportunities</button>
        </div>
        <div class="sub">A scan evaluates every dataset with every active strategy, sizes each signal for the chosen account, applies the risk rules, gathers historical evidence and ranks what survives. "No qualifying trades found" is a normal, healthy result.</div>
      </div>
      <div class="spacer"></div>
      <div id="scan-result">${latest.scan_id ? renderScan(latest, true) : `<div class="empty">No scan yet. Press <b>Scan for opportunities</b>.</div>`}</div>`;
    $("#scan-btn").onclick = async () => {
      $("#scan-result").innerHTML = `<div class="empty">Scanning…</div>`;
      try {
        const asof = $("#scan-asof").value;
        const r = await api("/api/scan", { method: "POST", body: { account_id: $("#scan-account").value || null, example_balance: Number($("#scan-balance").value), as_of: asof ? asof + "T23:59:59+00:00" : null } });
        $("#scan-result").innerHTML = renderScan(r, false);
        wireCandidateButtons(r, accounts);
      } catch (e) { $("#scan-result").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    };
    if (latest.scan_id) wireCandidateButtons(latest, accounts);
  };
  function renderScan(r, stale) {
    const s = r.summary;
    const head = `<div class="card"><div class="row between"><div><div class="stat small">${esc(s.message)}</div><div class="sub">${stale ? "Previous scan from " + esc(r.created_at || "") + " · " : ""}as of ${esc(r.as_of || "latest")} · ${s.datasets_scanned} datasets · ${s.strategies_evaluated} strategies · ${s.rejections} rejections (${Object.entries(s.rejections_by_stage || {}).map(([k, v]) => k + ": " + v).join(", ")}) · benchmark: ${esc(s.benchmark_regime || "n/a")}</div>${r.account_note ? `<div class="sub">${esc(r.account_note)}</div>` : ""}</div><div>${(r.data_statuses || []).map(statusPill).join(" ")}</div></div>
      ${r.live_note ? `<div class="notice" style="margin-top:10px">${esc(r.live_note)}</div>` : ""}</div><div class="spacer"></div>`;
    const cands = (r.candidates || []).map(renderCandidate).join("");
    const rej = (r.rejected || []).filter((x) => x.stage !== "signal");
    const sig = (r.rejected || []).filter((x) => x.stage === "signal").length;
    const rejHtml = `<details><summary>Rejected candidates (${rej.length}) and no-signal checks (${sig})</summary>
      <table><thead><tr><th>Symbol</th><th>Strategy</th><th>Stage</th><th>Reason</th></tr></thead><tbody>${rej.map((x) => `<tr><td>${esc(x.symbol)}</td><td>${esc(x.strategy || "")}</td><td>${esc(x.stage)}</td><td>${esc(x.reason)}</td></tr>`).join("")}</tbody></table>
      <div class="sub">${sig} asset/strategy pair(s) simply had no entry signal on the latest bar.</div></details>`;
    return head + (cands || `<div class="empty"><b>${esc(s.message)}</b><br><span class="muted">The system does not force trades. Check again when new data arrives, or use the "as of" date to see what it would have flagged in the past.</span></div>`) + `<div class="spacer"></div>` + rejHtml;
  }
  function renderCandidate(c) {
    const checks = (c.risk_checks || []).map((k) => `<li class="${k.passed ? "" : "fail"}"><b>${esc(k.name)}:</b> ${esc(k.detail)}</li>`).join("");
    const comps = Object.entries(c.ranking.components).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, " "))}</td><td class="num">${(v.score * 100).toFixed(0)}</td><td class="num">${(c.ranking.weights[k] * 100).toFixed(0)}%</td><td class="muted">${esc(v.detail)}</td></tr>`).join("");
    const he = c.historical_evidence || {};
    return `<div class="candidate" data-cand="${c.rank}">
      <div class="head">
        <div><span class="rank">#${c.rank}</span> <b style="font-size:16px">${esc(c.symbol)}</b> <span class="muted">${esc(c.name)} · ${esc(c.asset_class)}</span><br>
          <span class="pill pill-blue">${esc(c.strategy_name)}</span> <span class="pill">${esc(c.direction.toUpperCase())}</span> ${statusPill(c.data_status)} <span class="pill">${term("Regime")}: ${esc(c.regime_label)}</span></div>
        <div style="min-width:180px"><div class="sub">${term("Evidence score")}</div><div class="stat small">${c.evidence_score} / 100</div><div class="score-bar"><div style="width:${c.evidence_score}%"></div></div></div>
      </div>
      <p>${esc(c.plain_summary)}</p>
      <div class="notice">${esc(c.data_status_note)} Data timestamp: ${esc(c.data_timestamp)} · source: ${esc(c.data_source)}</div>
      <div class="spacer"></div>
      <div class="kv">
        <div><b>${term("Entry", "Proposed entry")}</b>${num(c.entry_price, 4)} · ${esc(c.entry_conditions)}</div>
        <div><b>${term("Stop", "Stop")} / ${term("Target", "Target")}</b>${num(c.stop_price, 4)} / ${num(c.target_price, 4)}</div>
        <div><b>${term("Position size")}</b>${c.quantity} units ≈ ${money(c.notional)}</div>
        <div><b>Estimated costs</b>${money(c.estimated_costs, 4)} (fees ${money(c.estimated_fees, 4)} + ${term("Slippage", "slippage/spread")} ${money(c.estimated_slippage, 4)})</div>
        <div><b>Potential profit / loss</b>${signed(c.potential_profit)} / ${signed(-c.potential_loss)}</div>
        <div><b>${term("Reward-to-risk")}</b>${num(c.reward_to_risk)} after costs (${num(c.reward_to_risk_before_costs)} before)</div>
        <div><b>Max holding</b>${c.max_holding_bars ?? "–"} bars</div>
        <div><b>Probability of success</b>${esc(c.probability.text)}${c.probability.reason ? `<br><span class="muted">${esc(c.probability.reason)}</span>` : ""}</div>
      </div>
      <details><summary>Supporting evidence</summary><ul class="plain">${c.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul><p class="sub">Benchmark: ${esc(c.benchmark_regime || "n/a")}</p><ul class="plain">${(c.regime_evidence || []).map((e) => `<li class="muted">${esc(e)}</li>`).join("")}</ul></details>
      <details><summary>Why this trade could fail</summary><ul class="plain">${c.failure_reasons.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>
      <details><summary>Risk checks</summary><ul class="check-list">${checks}</ul></details>
      <details><summary>Historical evidence for this strategy on this asset</summary>
        <div class="kv"><div><b>Trades (total / out-of-sample)</b>${he.trades_total} / ${he.trades_oos}</div><div><b>${term("Expectancy")} (R) in-sample / OOS</b>${num(he.expectancy_r_in_sample)} / ${num(he.expectancy_r_out_of_sample)}</div><div><b>${term("Profit factor")} OOS</b>${num(he.profit_factor_out_of_sample)}</div><div><b>${term("Win rate")} OOS</b>${pct(he.win_rate_out_of_sample)}</div><div><b>Max drawdown OOS</b>${num(he.max_drawdown_out_of_sample_pct)}%</div><div><b>Deterioration</b>${he.deterioration ? "Yes" : "No"}</div></div>
        ${(he.notes || []).map((n) => `<div class="notice">${esc(n)}</div>`).join("")}
        <table><thead><tr><th>Regime</th><th class="num">Trades</th><th class="num">Win rate</th><th class="num">Exp. (R)</th></tr></thead><tbody>${Object.entries(he.by_regime || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v.trades}</td><td class="num">${pct(v.win_rate)}</td><td class="num">${num(v.expectancy_r)}</td></tr>`).join("")}</tbody></table></details>
      <details><summary>How the evidence score was built</summary><table><thead><tr><th>Component</th><th class="num">Score</th><th class="num">Weight</th><th>Detail</th></tr></thead><tbody>${comps}</tbody></table><div class="sub">${esc(c.ranking.note)}</div></details>
      <div class="spacer"></div>
      <div class="row"><span class="muted">This is a signal, not an order.</span> <button class="btn small secondary" data-open-cand="${c.rank}">Open in paper account…</button></div>
    </div>`;
  }
  function wireCandidateButtons(r, accounts) {
    content.querySelectorAll("[data-open-cand]").forEach((b) => (b.onclick = async () => {
      const c = r.candidates.find((x) => x.rank == b.dataset.openCand);
      if (!accounts.length) { toast("Create a paper account first (Paper Trading page).", "bad"); return; }
      const opts = accounts.map((a) => `<option value="${a.account.id}">${esc(a.account.name)} (${money(a.equity)}, clock ${date(a.account.clock_ts)})</option>`).join("");
      modal(`<h2>Open simulated position</h2><p>${esc(c.plain_summary)}</p><div class="notice">The fill uses the latest close available at the paper account's clock, plus estimated slippage. The risk manager re-checks the trade first.</div><div class="spacer"></div><label class="field">Paper account<select id="open-acc">${opts}</select></label><div class="spacer"></div><button class="btn" id="open-confirm">Open paper position</button>`);
      $("#open-confirm").onclick = async () => {
        try { await api(`/api/paper/accounts/${$("#open-acc").value}/open`, { method: "POST", body: { candidate: c } }); toast("Simulated position opened.", "ok"); $("#modal").classList.add("hidden"); }
        catch (e) { toast(e.message, "bad"); }
      };
    }));
  }

  // -------------------------------------------------------- strategy lab
  PAGES.lab = async function () {
    if (!state.datasets.length) { content.innerHTML = noData(); return; }
    const research = await api("/api/research");
    content.innerHTML = `
      <div class="tabs"><button class="active" data-tab="compare">Compare strategies</button><button data-tab="params">Strategy rules & parameters</button><button data-tab="grid">Parameter research</button></div>
      <div id="lab-body"></div>`;
    const tabs = {
      compare: () => `<div class="card"><div class="row"><label class="field">Asset<select id="cmp-ds">${datasetOptions()}</select></label><label class="field">Simulated balance ($)<input type="number" id="cmp-bal" value="1000"></label><label class="field">Start<input type="date" id="cmp-start"></label><label class="field">End<input type="date" id="cmp-end"></label><button class="btn" id="cmp-run">Compare all strategies</button></div><div class="sub">Runs every strategy over the identical period, balance and cost model. Historical simulation only.</div></div><div class="spacer"></div><div id="cmp-out"></div>`,
      params: () => state.strategies.map((s) => `<div class="card"><div class="row between"><h2>${esc(s.name)} <span class="pill ${s.status.status === "paused" ? "pill-red" : s.status.status === "watch" ? "pill-amber" : "pill-green"}">${esc(s.status.status)} · v${s.status.version}</span></h2>
          <div><button class="btn small secondary" data-status="${s.key}" data-to="${s.status.status === "paused" ? "active" : "paused"}">${s.status.status === "paused" ? "Re-activate" : "Pause"}</button> <button class="btn small secondary" data-versions="${s.key}">Version history</button></div></div>
        <p class="muted">${esc(s.description)}</p><div class="sub">${esc(s.status.reason || "")}</div>
        <div class="grid grid-2"><div><b>Entry rules</b><ul class="plain">${s.entry_rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div><div><b>Exit rules</b><ul class="plain">${s.exit_rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ul><div class="sub">Unfavourable regimes: ${s.unfavourable_regimes.join(", ")}</div></div></div>
        <details><summary>Parameters (current values)</summary><form data-params="${s.key}"><div class="grid grid-3">${s.params.map((p) => `<label class="field">${esc(p.label)}<input name="${p.name}" type="${p.type === "bool" ? "checkbox" : "number"}" ${p.type === "bool" ? (s.current_params[p.name] ? "checked" : "") : `value="${s.current_params[p.name]}" step="${p.step || "any"}" min="${p.minimum ?? ""}" max="${p.maximum ?? ""}"`}><span class="help">${esc(p.description)}</span></label>`).join("")}</div><div class="spacer"></div><button class="btn small" type="submit">Save as new version</button> <span class="sub">Saving records a new strategy version; the performance monitor evaluates versions on unseen data before trusting them.</span></form></details>
        <details><summary>Health (paper-trading evidence)</summary><div class="sub">${esc(s.health.label)}</div><ul class="plain">${s.health.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details></div>`).join(""),
      grid: () => `<div class="card"><div class="row"><label class="field">Asset<select id="rs-ds">${datasetOptions()}</select></label><label class="field">Strategy<select id="rs-strat">${strategyOptions()}</select></label><label class="field">Method<select id="rs-mode"><option value="split">Development / validation / test split</option><option value="walkforward">Walk-forward</option></select></label></div>
        <div class="spacer"></div><div id="rs-grid"></div><div class="spacer"></div><button class="btn" id="rs-run">Run research</button> <span class="sub">Enter comma-separated values for the parameters you want to test. Leave blank to keep the current value. Fewer combinations = less over-optimisation.</span></div><div class="spacer"></div><div id="rs-out"></div>
        <div class="spacer"></div><div class="card"><h3>Previous research runs</h3>${research.length ? `<table><thead><tr><th>When</th><th>Strategy</th><th>Symbol</th><th>Method</th><th class="num">Combos</th><th>Warnings</th></tr></thead><tbody>${research.map((r) => `<tr><td>${esc(r.created_at.slice(0, 16))}</td><td>${esc(r.strategy)}</td><td>${esc(r.symbol)}</td><td>${esc(r.mode)}</td><td class="num">${r.combinations}</td><td class="muted">${(r.warnings || []).length}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">None yet.</div>`}</div>`,
    };
    function showTab(name) {
      content.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
      $("#lab-body").innerHTML = tabs[name]();
      if (name === "compare") $("#cmp-run").onclick = runCompare;
      if (name === "params") wireParams();
      if (name === "grid") { renderGridInputs(); $("#rs-strat").onchange = renderGridInputs; $("#rs-run").onclick = runResearch; }
    }
    content.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
    showTab("compare");

    async function runCompare() {
      $("#cmp-out").innerHTML = `<div class="empty">Running…</div>`;
      try {
        const r = await api("/api/backtest/compare", { method: "POST", body: { dataset_id: Number($("#cmp-ds").value), starting_balance: Number($("#cmp-bal").value), start: $("#cmp-start").value || null, end: $("#cmp-end").value || null } });
        const colors = ["#3b82f6", "#22c55e", "#f59e0b", "#a78bfa"];
        const series = r.results.map((x, i) => ({ name: x.name, values: x.equity_curve.map((p) => p[1]), color: colors[i % 4] }));
        const labels = r.results[0] ? r.results[0].equity_curve.map((p) => p[0]) : [];
        $("#cmp-out").innerHTML = `<div class="card"><h3>Equity curves (same period & balance)</h3>${lineChart(series, { labels, baseline: Number($("#cmp-bal").value), fmt: (v) => "$" + v.toFixed(0) })}</div><div class="spacer"></div>
          <div class="card"><table><thead><tr><th>Strategy</th><th>Verdict</th><th class="num">Trades</th><th class="num">Net profit</th><th class="num">Return</th><th class="num">${term("Win rate")}</th><th class="num">${term("Profit factor")}</th><th class="num">${term("Expectancy")}</th><th class="num">${term("Max drawdown")}</th><th class="num">Fees+slip</th></tr></thead><tbody>
          ${r.results.map((x) => { const m = x.metrics; return `<tr><td><b>${esc(x.name)}</b></td><td>${verdictPill(m)}</td><td class="num">${m.trades}</td><td class="num">${signed(m.net_profit)}</td><td class="num">${num(m.total_net_return_pct)}%</td><td class="num">${pct(m.win_rate)}</td><td class="num">${num(m.profit_factor)}</td><td class="num">${money(m.expectancy)}</td><td class="num">${num(m.max_drawdown_pct)}%</td><td class="num">${money(m.total_fees + m.total_slippage)}</td></tr>`; }).join("")}</tbody></table><div class="sub">${esc(r.disclaimer)} Results with fewer than 20 trades are inconclusive.</div></div>`;
      } catch (e) { $("#cmp-out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    }
    function wireParams() {
      content.querySelectorAll("form[data-params]").forEach((f) => (f.onsubmit = async (e) => {
        e.preventDefault();
        const params = {};
        for (const inp of f.querySelectorAll("input")) params[inp.name] = inp.type === "checkbox" ? inp.checked : Number(inp.value);
        try { await api(`/api/strategies/${f.dataset.params}/params`, { method: "PUT", body: { params } }); toast("Saved as a new strategy version.", "ok"); go("lab"); } catch (err) { toast(err.message, "bad"); }
      }));
      content.querySelectorAll("[data-status]").forEach((b) => (b.onclick = async () => {
        try { await api(`/api/strategies/${b.dataset.status}/status`, { method: "PUT", body: { status: b.dataset.to, reason: "Changed manually by user in Strategy Lab." } }); go("lab"); } catch (err) { toast(err.message, "bad"); }
      }));
      content.querySelectorAll("[data-versions]").forEach((b) => (b.onclick = async () => {
        const v = await api(`/api/strategies/${b.dataset.versions}/versions`);
        modal(`<h2>Version history: ${esc(b.dataset.versions)}</h2>${v.length ? `<table><thead><tr><th>Version</th><th>Status</th><th>Reason</th><th>When</th><th>Params</th></tr></thead><tbody>${v.map((x) => `<tr><td>${x.version}</td><td>${esc(x.status)}</td><td>${esc(x.reason)}</td><td>${esc(x.created_at.slice(0, 16))}</td><td><pre>${esc(JSON.stringify(x.params))}</pre></td></tr>`).join("")}</tbody></table>` : "<p class='muted'>Only the built-in defaults so far (version 1).</p>"}`);
      }));
    }
    function renderGridInputs() {
      const s = state.strategies.find((x) => x.key === $("#rs-strat").value);
      $("#rs-grid").innerHTML = `<div class="grid grid-3">${s.params.filter((p) => p.type !== "bool").map((p) => `<label class="field">${esc(p.label)} <span class="help">current ${s.current_params[p.name]} · e.g. ${p.default}, ${(p.default * 1.5).toFixed(p.type === "int" ? 0 : 2)}</span><input data-grid="${p.name}" placeholder="comma-separated values"></label>`).join("")}</div>`;
    }
    async function runResearch() {
      const grid = {};
      content.querySelectorAll("[data-grid]").forEach((i) => { const v = i.value.split(",").map((x) => x.trim()).filter(Boolean).map(Number).filter((x) => !isNaN(x)); if (v.length) grid[i.dataset.grid] = v; });
      $("#rs-out").innerHTML = `<div class="empty">Researching… (every combination is a full backtest)</div>`;
      try {
        const r = await api("/api/research", { method: "POST", body: { dataset_id: Number($("#rs-ds").value), strategy: $("#rs-strat").value, grid, mode: $("#rs-mode").value } });
        const row = (m) => m ? `${m.trades} trades · exp ${num(m.expectancy_r)}R · PF ${num(m.profit_factor)} · win ${pct(m.win_rate)} · DD ${num(m.max_drawdown_pct)}%` : "–";
        let html = `<div class="card"><div class="row between"><h2>Research result · ${esc(r.strategy)} on ${esc(r.symbol)}</h2>${statusPill(r.data_status)}</div><div class="sub">${esc(r.method)}</div>${(r.warnings || []).map((w) => `<div class="notice">${esc(w)}</div>`).join("")}`;
        if (r.mode === "walkforward") {
          html += `<div class="spacer"></div><div class="grid grid-4"><div class="card"><h3>Folds</h3><div class="stat">${r.summary.folds}</div></div><div class="card"><h3>Profitable folds</h3><div class="stat">${r.summary.folds_positive}</div></div><div class="card"><h3>OOS trades</h3><div class="stat">${r.summary.oos_trades}</div></div><div class="card"><h3>Mean OOS expectancy</h3><div class="stat small">${num(r.summary.mean_oos_expectancy_r)}R ± ${num(r.summary.sd_oos_expectancy_r)}</div></div></div>
            <div class="spacer"></div><table><thead><tr><th>Train period</th><th>Test period</th><th>Chosen params</th><th>Train</th><th>Test (out of sample)</th></tr></thead><tbody>${r.folds.map((f) => `<tr><td>${date(f.train_period[0])} → ${date(f.train_period[1])}</td><td>${date(f.test_period[0])} → ${date(f.test_period[1])}</td><td><code>${esc(JSON.stringify(f.params))}</code></td><td class="muted">${row(f.train)}</td><td>${row(f.test)}</td></tr>`).join("")}</tbody></table>`;
        } else {
          const b = r.best;
          html += `<div class="sub">Combinations tested: <b>${r.combinations_tested}</b> · Development ${date(r.split.development[0])}→${date(r.split.development[1])} · Validation ${date(r.split.validation[0])}→${date(r.split.validation[1])} · Test ${date(r.split.test[0])}→${date(r.split.test[1])}</div>
            ${b ? `<div class="spacer"></div><div class="card"><h3>Best by validation</h3><code>${esc(JSON.stringify(b.params))}</code><div class="kv" style="margin-top:8px"><div><b>Development</b>${row(b.development)}</div><div><b>Validation</b>${row(b.validation)}</div><div><b>Final test (untouched)</b>${row(b.test)}</div><div><b>Stable across periods</b>${b.stable_across_periods ? "Yes" : "No"}</div></div></div>` : ""}
            <div class="spacer"></div><details><summary>All development results (top ${r.all_development_results.length})</summary><table><thead><tr><th>Params</th><th>Development</th></tr></thead><tbody>${r.all_development_results.map((x) => `<tr><td><code>${esc(JSON.stringify(x.params))}</code></td><td>${row(x.development)}</td></tr>`).join("")}</tbody></table></details>`;
        }
        html += `<div class="sub" style="margin-top:8px">Optimised parameters are never deployed automatically. Save them in the parameters tab only if the out-of-sample evidence is convincing.</div></div>`;
        $("#rs-out").innerHTML = html;
      } catch (e) { $("#rs-out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    }
  };

  // ------------------------------------------------------------ backtest
  PAGES.backtest = async function () {
    if (!state.datasets.length) { content.innerHTML = noData(); return; }
    const history = await api("/api/backtests");
    content.innerHTML = `
      <div class="card"><div class="row"><label class="field">Asset<select id="bt-ds">${datasetOptions()}</select></label><label class="field">Strategy<select id="bt-strat">${strategyOptions()}</select></label><label class="field">Simulated balance ($)<input type="number" id="bt-bal" value="1000" min="1"></label><label class="field">Start<input type="date" id="bt-start"></label><label class="field">End<input type="date" id="bt-end"></label><button class="btn" id="bt-run">Run backtest</button></div>
      <div class="sub">The backtest replays history bar by bar. Decisions only use data up to each bar's close; fills happen at the next open with slippage. Dollar risk limits scale to the balance.</div></div>
      <div class="spacer"></div><div id="bt-out"></div><div class="spacer"></div>
      <div class="card"><h3>Previous backtests</h3>${history.length ? `<table><thead><tr><th>When</th><th>Symbol</th><th>Strategy</th><th class="num">Balance</th><th class="num">Trades</th><th class="num">Net</th><th>Verdict</th><th></th></tr></thead><tbody>${history.map((h) => `<tr><td>${esc(h.created_at.slice(0, 16))}</td><td>${esc(h.symbol)}</td><td>${esc(h.strategy)}</td><td class="num">${money(h.starting_balance)}</td><td class="num">${h.metrics.trades}</td><td class="num">${signed(h.metrics.net_profit)}</td><td>${verdictPill(h.metrics)}</td><td><button class="btn small secondary" data-bt="${h.id}">View</button></td></tr>`).join("")}</tbody></table>` : `<div class="muted">None yet.</div>`}</div>`;
    $("#bt-run").onclick = async () => {
      $("#bt-out").innerHTML = `<div class="empty">Running…</div>`;
      try {
        const r = await api("/api/backtest", { method: "POST", body: { dataset_id: Number($("#bt-ds").value), strategy: $("#bt-strat").value, starting_balance: Number($("#bt-bal").value), start: $("#bt-start").value || null, end: $("#bt-end").value || null } });
        $("#bt-out").innerHTML = renderBacktest(r);
      } catch (e) { $("#bt-out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    };
    content.querySelectorAll("[data-bt]").forEach((b) => (b.onclick = async () => { const r = await api(`/api/backtests/${b.dataset.bt}`); $("#bt-out").innerHTML = renderBacktest(r); window.scrollTo(0, 0); }));
  };
  function renderBacktest(r) {
    const m = r.metrics, ex = m.explanations || {};
    const stat = (key, label, val) => `<div class="card"><h3>${ex[key] ? `<span class="term" title="${esc(ex[key])}">${esc(label)}</span>` : esc(label)}</h3><div class="stat small">${val}</div></div>`;
    const curve = r.equity_curve || [];
    return `<div class="card"><div class="row between"><h2>${esc(r.symbol)} · ${esc(r.strategy)} · ${money(r.starting_balance)} simulated</h2>${statusPill(r.data_status)}</div><div class="sub">${esc(r.start_ts || "")} → ${esc(r.end_ts || "")} · ${r.bars_tested} bars · ${r.rejected_count} signals rejected by risk rules</div>
      <div class="notice ${m.conclusive ? (m.expectancy > 0 ? "ok" : "bad") : ""}">${esc(m.verdict)}</div>
      ${(r.notes || []).map((n) => `<div class="sub">${esc(n)}</div>`).join("")}
      <div class="spacer"></div>${curve.length ? lineChart([{ name: "Equity", values: curve.map((p) => p[1]), color: "#3b82f6" }], { labels: curve.map((p) => p[0]), baseline: r.starting_balance, fmt: (v) => "$" + v.toFixed(0) }) : ""}
      <div class="spacer"></div>
      <div class="grid grid-4">
        ${stat("total_net_return_pct", "Total net return", num(m.total_net_return_pct) + "%")}${stat("net_profit", "Net profit (after costs)", signed(m.net_profit))}${stat("trades", "Trades", m.trades)}${stat("win_rate", "Win rate", pct(m.win_rate))}
        ${stat("avg_win", "Average win", money(m.avg_win))}${stat("avg_loss", "Average loss", money(m.avg_loss))}${stat("profit_factor", "Profit factor", num(m.profit_factor))}${stat("expectancy", "Expected value / trade", money(m.expectancy) + " (" + num(m.expectancy_r) + "R)")}
        ${stat("max_drawdown_pct", "Max drawdown", num(m.max_drawdown_pct) + "%")}${stat("longest_losing_streak", "Longest losing streak", m.longest_losing_streak)}${stat("total_fees", "Fees", money(m.total_fees))}${stat("total_slippage", "Slippage & spread", money(m.total_slippage))}
        ${stat("return_on_capital_pct", "Return on simulated capital", num(m.return_on_capital_pct) + "%")}${stat("sharpe_ratio", "Sharpe ratio", num(m.sharpe_ratio))}${stat("avg_bars_held", "Avg bars held", num(m.avg_bars_held, 1))}${stat("gross_profit", "Gross profit (before costs)", signed(m.gross_profit))}
      </div>
      <div class="spacer"></div>
      <div class="grid grid-2">
        <div class="card"><h3>Performance by market regime (at entry)</h3><table><thead><tr><th>Regime</th><th class="num">Trades</th><th class="num">Net</th><th class="num">Win rate</th><th class="num">Exp (R)</th></tr></thead><tbody>${Object.entries(m.by_regime || {}).map(([k, v]) => `<tr><td>${esc((r.regime_labels || {})[k] || k)}</td><td class="num">${v.trades}${v.conclusive ? "" : " <span class='muted'>(few)</span>"}</td><td class="num">${signed(v.net_profit)}</td><td class="num">${pct(v.win_rate)}</td><td class="num">${num(v.expectancy_r)}</td></tr>`).join("") || "<tr><td colspan=5 class='muted'>No trades</td></tr>"}</tbody></table><div class="sub">Share of bars by regime: ${Object.entries(m.regime_bar_share || {}).map(([k, v]) => `${k} ${pct(v, 0)}`).join(" · ")}</div></div>
        <div class="card"><h3>Performance by period</h3><table><thead><tr><th>Year</th><th class="num">Trades</th><th class="num">Net</th><th class="num">Win rate</th></tr></thead><tbody>${Object.entries(m.by_period || {}).map(([k, v]) => `<tr><td>${k}</td><td class="num">${v.trades}</td><td class="num">${signed(v.net_profit)}</td><td class="num">${pct(v.win_rate)}</td></tr>`).join("") || "<tr><td colspan=4 class='muted'>No trades</td></tr>"}</tbody></table></div>
      </div>
      <details><summary>Trades (${r.trades.length})</summary><table><thead><tr><th>Entry</th><th>Exit</th><th class="num">Qty</th><th class="num">Entry px</th><th class="num">Exit px</th><th class="num">Net P&L</th><th class="num">R</th><th class="num">Bars</th><th>Exit reason</th><th>Regime</th></tr></thead><tbody>${r.trades.map((t) => `<tr><td>${date(t.entry_ts)}</td><td>${date(t.exit_ts)}</td><td class="num">${t.quantity}</td><td class="num">${num(t.entry_price, 4)}</td><td class="num">${num(t.exit_price, 4)}</td><td class="num">${signed(t.net_pnl)}</td><td class="num">${num(t.r_multiple)}</td><td class="num">${t.bars_held}</td><td>${esc(t.exit_reason)}</td><td class="muted">${esc(t.regime_at_entry)}</td></tr>`).join("")}</tbody></table></details>
      <details><summary>Assumptions & limitations</summary><ul class="plain">${(r.assumptions || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ul><p class="sub">Risk limits used (scaled): daily loss ${money(r.risk_settings_used?.daily_loss_limit)}, cumulative ${money(r.risk_settings_used?.cumulative_loss_limit)}, floor ${money(r.risk_settings_used?.equity_floor)}, risk/trade ${r.risk_settings_used?.risk_per_trade_pct}%.</p></details>
      <details><summary>Signals rejected by risk rules (${r.rejected_count})</summary><table><thead><tr><th>When</th><th>Reasons</th></tr></thead><tbody>${(r.rejected_signals || []).slice(0, 100).map((x) => `<tr><td>${date(x.ts)}</td><td class="muted">${esc((x.reasons || [x.reason]).join("; "))}</td></tr>`).join("")}</tbody></table></details>
    </div>`;
  }

  // --------------------------------------------------------------- paper
  PAGES.paper = async function () {
    const accounts = await api("/api/paper/accounts");
    content.innerHTML = `
      <div class="notice">Paper trading uses <b>simulated money only</b>. Nothing here is a deposit, a broker account or real profit. The example $50 account shows how the system would behave with small capital.</div>
      <div class="spacer"></div>
      <div class="card"><h2>Create a paper account</h2><div class="row"><label class="field">Name<input id="pa-name" value="Example $50 account"></label><label class="field">Simulated starting balance ($)<input type="number" id="pa-bal" value="50" min="1"></label><label class="field">Clock start (optional)<input type="date" id="pa-start"></label><button class="btn" id="pa-create" ${state.datasets.length ? "" : "disabled"}>Create</button></div><div class="sub">${state.datasets.length ? "The clock starts about 200 bars before the end of the benchmark dataset so you can replay forward; leave the date blank for that default." : "Load data first (Data Sources)."}</div></div>
      <div class="spacer"></div>
      <div id="pa-list">${accounts.length ? accounts.map(renderAccountCard).join("") : `<div class="empty">No paper accounts yet.</div>`}</div>`;
    $("#pa-create").onclick = async () => {
      try { await api("/api/paper/accounts", { method: "POST", body: { name: $("#pa-name").value, starting_balance: Number($("#pa-bal").value), start_ts: $("#pa-start").value ? $("#pa-start").value + "T00:00:00+00:00" : null } }); toast("Paper account created (simulated).", "ok"); go("paper"); } catch (e) { toast(e.message, "bad"); }
    };
    wireAccountCards();
  };
  function renderAccountCard(a) {
    const acc = a.account;
    return `<div class="card" data-acc="${acc.id}">
      <div class="row between"><h2>${esc(acc.name)} <span class="pill ${acc.status === "halted" ? "pill-red" : "pill-green"}">${esc(acc.status)}</span> <span class="pill">clock: ${date(acc.clock_ts)}</span></h2>
        <div class="row"><button class="btn small" data-adv="1">Advance 1 bar</button><button class="btn small" data-adv="5">Advance 5</button><button class="btn small" data-adv="20">Advance 20</button><button class="btn small" data-adv="120">Advance 120</button>${acc.status === "halted" ? `<button class="btn small danger" data-resume="1">I have reviewed – resume</button>` : ""}<button class="btn small secondary" data-del="1">Delete</button></div></div>
      <div class="grid grid-4"><div><div class="sub">${term("Equity", "Simulated equity")}</div><div class="stat small">${money(a.equity)}</div></div><div><div class="sub">Cash</div><div class="stat small">${money(a.cash)}</div></div><div><div class="sub">${term("Realized P&L", "Realized P&L")}</div><div class="stat small">${signed(a.realized_pnl)}</div></div><div><div class="sub">${term("Unrealized P&L", "Unrealized P&L")}</div><div class="stat small">${signed(a.unrealized_pnl)}</div></div></div>
      <div class="sub">Return ${num(a.return_pct)}% · ${a.closed_trades} closed trades · win rate ${pct(a.win_rate)} · today's realized ${money(a.realized_today)} · started with ${money(acc.starting_balance)}</div>
      <div class="spacer"></div>
      <h3>Open positions (${a.open_positions.length})</h3>${a.open_positions.length ? `<table><thead><tr><th>Symbol</th><th>Strategy</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Mark</th><th class="num">Stop</th><th class="num">Target</th><th class="num">Unrealized</th><th>Data</th><th></th></tr></thead><tbody>${a.open_positions.map((p) => `<tr><td><b>${esc(p.symbol)}</b></td><td>${esc(p.strategy)}</td><td class="num">${p.quantity}</td><td class="num">${num(p.entry_price, 4)}</td><td class="num">${num(p.mark_price, 4)}</td><td class="num">${num(p.stop_price, 4)}</td><td class="num">${num(p.target_price, 4)}</td><td class="num">${signed(p.unrealized_pnl)}</td><td>${statusPill(p.data_status)}</td><td><button class="btn small secondary" data-close="${p.id}">Close</button></td></tr>`).join("")}</tbody></table>` : `<div class="muted">None.</div>`}
      <div id="events-${acc.id}"></div>
      <details><summary>Trade history</summary><div id="hist-${acc.id}"><button class="btn small secondary" data-hist="${acc.id}">Load history</button></div></details>
    </div>`;
  }
  function wireAccountCards() {
    content.querySelectorAll("[data-acc]").forEach((card) => {
      const id = card.dataset.acc;
      card.querySelectorAll("[data-adv]").forEach((b) => (b.onclick = async () => {
        b.disabled = true; $(`#events-${id}`).innerHTML = `<div class="muted">Advancing…</div>`;
        try {
          const r = await api(`/api/paper/accounts/${id}/advance`, { method: "POST", body: { steps: Number(b.dataset.adv) } });
          const ev = r.events;
          const icon = { entry: "🟢", exit: "🔴", signal: "📌", halt: "⛔", no_candidates: "·", no_new_entries: "⏸", exit_scheduled: "⏳", entry_skipped: "⚠", end_of_data: "⏹" };
          const summary = {}; ev.forEach((e) => (summary[e.type] = (summary[e.type] || 0) + 1));
          const html = `<div class="spacer"></div><h3>What happened (${ev.length} events)</h3><div class="sub">${Object.entries(summary).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div><div style="max-height:260px;overflow:auto;font-size:12px">${ev.filter((e) => e.type !== "no_candidates" && e.type !== "no_new_entries").map((e) => `<div class="log-row"><span>${esc(date(e.ts))}</span><span>${icon[e.type] || ""} ${esc(e.type)}</span><span>${esc(e.symbol || "")}</span><span>${esc(e.message || e.reason || (e.price !== undefined ? `${e.quantity ?? ""} @ ${e.price}` : ""))} ${e.pnl !== undefined ? signed(e.pnl) : ""}</span></div>`).join("") || "<div class='muted'>No entries or exits in this step (no qualifying trades, or risk limits in force).</div>"}</div>`;
          const fresh = await api("/api/paper/accounts");
          const a = fresh.find((x) => x.account.id == id);
          card.outerHTML = renderAccountCard(a);
          $(`#events-${id}`).innerHTML = html;
          wireAccountCards();
          renderChips();
        } catch (e) { toast(e.message, "bad"); b.disabled = false; }
      }));
      card.querySelectorAll("[data-close]").forEach((b) => (b.onclick = async () => { try { await api(`/api/paper/accounts/${id}/positions/${b.dataset.close}/close`, { method: "POST" }); toast("Simulated position closed.", "ok"); go("paper"); } catch (e) { toast(e.message, "bad"); } }));
      card.querySelectorAll("[data-resume]").forEach((b) => (b.onclick = async () => { try { await api(`/api/paper/accounts/${id}/resume`, { method: "POST" }); go("paper"); } catch (e) { toast(e.message, "bad"); } }));
      card.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { if (!armed(b, "Delete account?")) return; await api(`/api/paper/accounts/${id}`, { method: "DELETE" }); go("paper"); }));
      card.querySelectorAll("[data-hist]").forEach((b) => (b.onclick = async () => {
        const d = await api(`/api/paper/accounts/${id}`);
        $(`#hist-${id}`).innerHTML = d.closed_positions.length ? `<table><thead><tr><th>Symbol</th><th>Strategy</th><th>Entry</th><th>Exit</th><th class="num">Qty</th><th class="num">Entry px</th><th class="num">Exit px</th><th class="num">P&L</th><th>Reason</th><th>Data</th></tr></thead><tbody>${d.closed_positions.map((p) => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.strategy)}</td><td>${date(p.entry_ts)}</td><td>${date(p.exit_ts)}</td><td class="num">${p.quantity}</td><td class="num">${num(p.entry_price, 4)}</td><td class="num">${num(p.exit_price, 4)}</td><td class="num">${signed(p.realized_pnl)}</td><td>${esc(p.exit_reason)}</td><td>${statusPill(p.data_status)}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">No closed trades yet.</div>`;
      }));
    });
  }

  // --------------------------------------------------------- performance
  PAGES.performance = async function () {
    const p = await api("/api/performance");
    const ex = p.metric_explanations || {};
    content.innerHTML = `
      <div class="notice">${esc(p.disclaimer)}</div><div class="spacer"></div>
      <div class="grid grid-3">
        <div class="card"><h3>Live account</h3><div class="stat">${money(p.live.equity)}</div><div class="sub">${esc(p.live.note)}</div></div>
        ${p.paper_accounts.map((a) => `<div class="card"><h3>Paper: ${esc(a.account.name)}</h3><div class="stat">${money(a.equity)}</div><div class="sub">Return ${num(a.return_pct)}% · realized ${money(a.realized_pnl)} · ${a.closed_trades} trades · win rate ${pct(a.win_rate)} · simulated</div></div>`).join("")}
      </div>
      <div class="spacer"></div>
      <div class="card"><h2>Strategy scorecard</h2><div class="sub">Paper-trading evidence drives the performance monitor; backtests are historical simulations shown separately.</div>
        <table><thead><tr><th>Strategy</th><th>Status</th><th class="num">Paper trades</th><th class="num"><span class="term" title="${esc(ex.win_rate)}">Win rate</span></th><th class="num"><span class="term" title="${esc(ex.expectancy_r)}">Expectancy (R)</span></th><th class="num"><span class="term" title="${esc(ex.profit_factor)}">Profit factor</span></th><th class="num">Drawdown (R)</th><th>Monitor verdict</th></tr></thead><tbody>
        ${p.per_strategy.map((s) => { const m = s.paper.health.metrics || {}; return `<tr><td><b>${esc(s.name)}</b></td><td><span class="pill ${s.status.status === "paused" ? "pill-red" : s.status.status === "watch" ? "pill-amber" : "pill-green"}">${esc(s.status.status)}</span></td><td class="num">${m.trades ?? 0}</td><td class="num">${pct(m.win_rate)}</td><td class="num">${num(m.expectancy_r)}</td><td class="num">${num(m.profit_factor)}</td><td class="num">${num(m.max_drawdown_r)}</td><td class="muted">${s.paper.health.reasons.map(esc).join(" ")}</td></tr>`; }).join("")}</tbody></table></div>
      <div class="spacer"></div>
      <div class="card"><h2>Recent backtests by strategy (historical simulation)</h2>${p.per_strategy.map((s) => `<h3>${esc(s.name)}</h3>${s.recent_backtests.length ? `<table><thead><tr><th>Symbol</th><th class="num">Trades</th><th class="num">Net</th><th class="num">Win rate</th><th class="num">PF</th><th class="num">Exp (R)</th><th class="num">Max DD</th><th>Verdict</th></tr></thead><tbody>${s.recent_backtests.map((b) => `<tr><td>${esc(b.symbol)}</td><td class="num">${b.metrics.trades}</td><td class="num">${signed(b.metrics.net_profit)}</td><td class="num">${pct(b.metrics.win_rate)}</td><td class="num">${num(b.metrics.profit_factor)}</td><td class="num">${num(b.metrics.expectancy_r)}</td><td class="num">${num(b.metrics.max_drawdown_pct)}%</td><td>${verdictPill({ ...b.metrics, expectancy: b.metrics.expectancy_r })}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">No backtests yet.</div>`}`).join("")}</div>
      <div class="spacer"></div>
      <div class="card"><h3>What the metrics mean</h3><div class="grid grid-2">${Object.entries(ex).map(([k, v]) => `<div><b>${esc(k.replace(/_/g, " "))}</b><div class="sub">${esc(v)}</div></div>`).join("")}</div></div>`;
  };

  // ---------------------------------------------------------------- risk
  PAGES.risk = async function () {
    const r = state.risk;
    const s = r.settings, ex = r.explanations;
    const field = (k) => {
      const v = s[k], e = ex[k] || { label: k, help: "" };
      const locked = ["leverage_enabled", "short_selling_enabled", "options_enabled"].includes(k);
      if (typeof v === "boolean") return `<label class="field">${esc(e.label)}<span><input type="checkbox" name="${k}" ${v ? "checked" : ""} ${locked ? "disabled" : ""}> ${locked ? "<span class='muted'>locked off in this version</span>" : ""}</span><span class="help">${esc(e.help)}</span></label>`;
      return `<label class="field">${esc(e.label)}<input type="number" name="${k}" value="${v}" step="any"><span class="help">${esc(e.help)}</span></label>`;
    };
    content.innerHTML = `
      <div class="notice info">${esc(r.example_note)}</div><div class="spacer"></div>
      <form id="risk-form" class="card"><h2>Risk limits</h2><div class="grid grid-3">${Object.keys(s).map(field).join("")}</div><div class="spacer"></div><button class="btn" type="submit">Save risk settings</button></form>
      <div class="spacer"></div>
      <form id="cost-form" class="card"><h2>Transaction cost models (estimates)</h2><div class="grid grid-3">${Object.entries(r.cost_models).map(([cls, cm]) => `<div><h3>${esc(cls)}</h3>${Object.entries(cm).filter(([k]) => k !== "min_quantity").map(([k, v]) => `<label class="field">${esc(k.replace(/_/g, " "))}<input type="number" step="any" name="${cls}.${k}" value="${v}"><span class="help">${esc(r.cost_explanations[k] || "")}</span></label>`).join("")}</div>`).join("")}</div><div class="spacer"></div><button class="btn" type="submit">Save cost models</button></form>
      <div class="spacer"></div>
      <div class="card"><h2>Rules that cannot be switched off</h2><ul class="plain">${r.hard_rules.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
      <div class="spacer"></div>
      <div class="card"><h2>Worked example</h2><div class="sub">With a $${s.example_account_size} account and ${s.risk_per_trade_pct}% risk per trade, the most a single trade may lose (after costs) is <b>${money(s.example_account_size * s.risk_per_trade_pct / 100)}</b>. A trade must offer at least <b>${s.min_reward_risk}x</b> that in potential profit after costs, so the target would need to be worth about ${money(s.example_account_size * s.risk_per_trade_pct / 100 * s.min_reward_risk)}. After <b>${money(s.daily_loss_limit)}</b> of losses in a day, no new trades are opened. If equity falls to <b>${money(s.equity_floor)}</b>, everything stops until you review.</div></div>`;
    $("#risk-form").onsubmit = async (e) => {
      e.preventDefault(); const body = {};
      for (const inp of e.target.querySelectorAll("input")) body[inp.name] = inp.type === "checkbox" ? inp.checked : Number(inp.value);
      try { await api("/api/risk", { method: "PUT", body: { settings: body } }); toast("Risk settings saved.", "ok"); go("risk"); } catch (err) { toast(err.message, "bad"); }
    };
    $("#cost-form").onsubmit = async (e) => {
      e.preventDefault(); const body = {};
      for (const inp of e.target.querySelectorAll("input")) { const [cls, k] = inp.name.split("."); (body[cls] = body[cls] || {})[k] = Number(inp.value); }
      try { await api("/api/risk", { method: "PUT", body: { cost_models: body } }); toast("Cost models saved.", "ok"); go("risk"); } catch (err) { toast(err.message, "bad"); }
    };
  };

  // ---------------------------------------------------------------- data
  PAGES.data = async function () {
    const providers = await api("/api/providers");
    content.innerHTML = `
      <div class="grid grid-2">
        <div class="card"><h2>1. Bundled sample data</h2><p class="muted">Seven synthetic daily series (stocks, ETF, crypto) generated offline from a documented model. Labelled SIMULATED everywhere. Ideal for learning the app without any account or download.</p><button class="btn" id="load-samples">Load bundled sample data</button></div>
        <div class="card"><h2>2. Import historical CSV</h2><p class="muted">Accepts Yahoo Finance, Stooq, Binance, broker and spreadsheet exports with Date/Open/High/Low/Close/Volume columns. Rows are validated; problems are reported, never hidden.</p>
          <form id="csv-form"><div class="grid grid-2"><label class="field">File<input type="file" name="file" accept=".csv,.txt" required></label><label class="field">Symbol<input name="symbol" placeholder="e.g. AAPL" required></label><label class="field">Name<input name="name" placeholder="Apple Inc."></label><label class="field">Asset class<select name="asset_class"><option value="stock">Stock</option><option value="etf">ETF</option><option value="crypto">Crypto</option></select></label><label class="field">Interval<select name="interval"><option value="1d">Daily</option><option value="1h">Hourly</option><option value="4h">4-hour</option><option value="15m">15-minute</option><option value="5m">5-minute</option><option value="1m">1-minute</option></select></label><label class="field">Source time zone<input name="timezone_name" value="UTC"></label></div><div class="spacer"></div><button class="btn" type="submit">Import</button></form><div id="csv-out"></div></div>
      </div>
      <div class="spacer"></div>
      <div class="card"><h2>3. Optional free providers (no API key)</h2><p class="muted">These need internet access and are subject to the provider's terms and rate limits. They deliver end-of-day data at best, so the freshest label is DELAYED. If a download fails, everything else keeps working.</p>
        <div class="grid grid-2">${providers.filter((p) => p.key !== "bundled-sample").map((p) => `<div class="card"><h3>${esc(p.name)} <span class="pill ${p.enabled ? "pill-green" : "pill-red"}">${esc(p.status_message)}</span></h3><p class="muted">${esc(p.description)}</p><ul class="plain">${p.notes.map((n) => `<li class="muted">${esc(n)}</li>`).join("")}</ul>
          <div class="row"><input data-fetch-symbol="${p.key}" placeholder="Symbol (e.g. ${esc((p.symbols[0] || {}).symbol || "")})" list="syms-${p.key}"><datalist id="syms-${p.key}">${p.symbols.map((s) => `<option value="${esc(s.symbol)}">${esc(s.name)}</option>`).join("")}</datalist><select data-fetch-class="${p.key}">${p.asset_classes.map((c) => `<option value="${c}">${c}</option>`).join("")}</select><button class="btn small" data-fetch="${p.key}" ${p.enabled ? "" : "disabled"}>Download</button></div><div data-fetch-out="${p.key}"></div></div>`).join("")}</div></div>
      <div class="spacer"></div>
      <div class="card"><h2>Loaded datasets</h2>${state.datasets.length ? `<table><thead><tr><th>Symbol</th><th>Status</th><th>Source</th><th>Retrieved (UTC)</th><th>Last bar</th><th class="num">Bars</th><th class="num">Quality</th><th>Notes</th><th></th></tr></thead><tbody>${state.datasets.map((d) => `<tr><td><b>${esc(d.symbol)}</b> <span class="muted">${esc(d.name)}</span></td><td>${statusPill(d.status)}<div class="sub">${esc(d.status_note)}</div></td><td>${esc(d.source)}</td><td class="muted">${esc(d.retrieved_at.slice(0, 19))}</td><td>${date(d.last_ts)}</td><td class="num">${d.bar_count}</td><td class="num">${num(d.quality_score)}</td><td class="muted">${d.quality_notes.map(esc).join("<br>") || "none"}</td><td><button class="btn small secondary" data-bench="${d.id}">Set benchmark</button> <button class="btn small danger" data-del-ds="${d.id}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">Nothing loaded yet.</div>`}</div>`;
    $("#load-samples").onclick = async () => { try { const r = await api("/api/datasets/load-samples", { method: "POST" }); toast(`Loaded ${r.loaded.length} sample datasets (SIMULATED).`, "ok"); go("data"); } catch (e) { toast(e.message, "bad"); } };
    $("#csv-form").onsubmit = async (e) => {
      e.preventDefault(); const fd = new FormData(e.target); $("#csv-out").innerHTML = `<div class="muted">Importing…</div>`;
      try { const r = await api("/api/datasets/import-csv", { method: "POST", body: fd }); const v = r.validation; $("#csv-out").innerHTML = `<div class="notice ok">Imported ${r.dataset.symbol}: ${v.accepted_rows} of ${v.total_rows} rows accepted · quality ${num(v.quality_score)}.</div>${v.issues.length ? `<ul class="plain">${v.issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : ""}`; loadShared(); }
      catch (err) { $("#csv-out").innerHTML = `<div class="notice bad">${esc(err.message)}</div>`; }
    };
    content.querySelectorAll("[data-fetch]").forEach((b) => (b.onclick = async () => {
      const key = b.dataset.fetch, out = $(`[data-fetch-out="${key}"]`);
      const symbol = $(`[data-fetch-symbol="${key}"]`).value.trim();
      if (!symbol) { toast("Enter a symbol first.", "bad"); return; }
      out.innerHTML = `<div class="muted">Downloading…</div>`;
      try { const r = await api("/api/datasets/fetch", { method: "POST", body: { provider: key, symbol, asset_class: $(`[data-fetch-class="${key}"]`).value } }); out.innerHTML = `<div class="notice ok">${esc(r.dataset.symbol)}: ${r.dataset.bar_count} bars, status ${r.dataset.status}, last bar ${date(r.dataset.last_ts)}.</div>`; loadShared(); }
      catch (e) { out.innerHTML = `<div class="notice bad">${esc(e.message)}<br><span class="muted">The app keeps working with bundled and imported data.</span></div>`; }
    }));
    content.querySelectorAll("[data-del-ds]").forEach((b) => (b.onclick = async () => { if (!armed(b, "Delete?")) return; await api(`/api/datasets/${b.dataset.delDs}`, { method: "DELETE" }); go("data"); }));
    content.querySelectorAll("[data-bench]").forEach((b) => (b.onclick = async () => { await api("/api/datasets/benchmark", { method: "POST", body: { dataset_id: Number(b.dataset.bench) } }); toast("Benchmark set. It is used for the broad-market regime filter.", "ok"); }));
  };

  // ---------------------------------------------------------------- logs
  PAGES.logs = async function () {
    const cats = ["", "scan", "rejected", "risk", "paper", "backtest", "research", "strategy", "data"];
    content.innerHTML = `<div class="card"><div class="row"><label class="field">Category<select id="log-cat">${cats.map((c) => `<option value="${c}">${c || "all"}</option>`).join("")}</select></label><button class="btn small secondary" id="log-refresh">Refresh</button></div><div class="spacer"></div><div id="log-list"></div></div>`;
    async function load() {
      const cat = $("#log-cat").value;
      const rows = await api(`/api/logs?limit=300${cat ? "&category=" + cat : ""}`);
      $("#log-list").innerHTML = rows.length ? rows.map((r) => `<div class="log-row"><span class="muted">${esc(r.ts.slice(0, 19).replace("T", " "))}</span><span class="lvl-${r.level}">${esc(r.level)}</span><span>${esc(r.category)}</span><span>${esc(r.message)}${Object.keys(r.details || {}).length ? ` <details style="display:inline-block;margin:0;padding:0 6px"><summary style="font-size:11px">details</summary><pre>${esc(JSON.stringify(r.details, null, 1))}</pre></details>` : ""}</span></div>`).join("") : `<div class="empty">No log entries yet.</div>`;
    }
    $("#log-cat").onchange = load; $("#log-refresh").onclick = load; load();
  };

  // ---------------------------------------------------------------- boot
  const initial = (location.hash || "#dashboard").slice(1);
  go(PAGES[initial] ? initial : "dashboard");
  window.addEventListener("hashchange", () => { const p = location.hash.slice(1); if (p === programmaticHash) { programmaticHash = null; return; } if (PAGES[p] && p !== state.page) go(p); });
})();
