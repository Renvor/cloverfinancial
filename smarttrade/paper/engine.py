"""Paper-trading engine.

A paper account has its own simulated cash, completely separate from any
live equity (which is always $0 in this version) and from backtest balances.

The account has a *clock*. ``advance`` moves the clock forward one bar at a
time across all selected datasets, replaying the same decision pipeline the
scanner uses: manage exits on the new bar, fill pending entries at the open,
then scan for new signals on the close. This works both for historical
replays and for "catch up to the latest downloaded bar" after refreshing a
free provider (in which case the data is DELAYED end-of-day data, and the
UI says so). All decisions and simulated executions are logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from ..models import AssetClass, Bar, DataStatus, Dataset, utcnow
from ..ranking import scan_opportunities
from ..risk import AccountState, OpenRisk, RiskManager, RiskSettings
from ..strategies import Strategy, build_view, get_strategy
from ..strategies.base import OpenPositionState


@dataclass
class PaperAccount:
    id: int
    name: str
    starting_balance: float
    cash: float
    created_at: datetime
    mode: str
    clock_ts: datetime | None
    status: str
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "starting_balance": self.starting_balance, "cash": round(self.cash, 4),
            "created_at": self.created_at.isoformat(), "mode": self.mode,
            "clock_ts": self.clock_ts.isoformat() if self.clock_ts else None, "status": self.status, "notes": self.notes,
        }


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PaperTradingEngine:
    def __init__(self, db: Database):
        self.db = db

    # ----------------------------------------------------------------- accounts
    def create_account(self, name: str, starting_balance: float, mode: str = "replay", start_ts: datetime | None = None) -> PaperAccount:
        if starting_balance <= 0:
            raise ValueError("Starting balance must be positive (this is simulated money, not a deposit).")
        account_id = self.db.insert_json_row(
            "paper_accounts", name=name, starting_balance=starting_balance, cash=starting_balance,
            created_at=utcnow().isoformat(), mode=mode, clock_ts=start_ts.isoformat() if start_ts else None,
            status="active", notes="Simulated paper account. Not real money.",
        )
        self.db.add_log("INFO", "paper", f"Created paper account '{name}' with simulated ${starting_balance:.2f}.", {"account_id": account_id})
        return self.get_account(account_id)

    def get_account(self, account_id: int) -> PaperAccount:
        row = self.db.query_one("SELECT * FROM paper_accounts WHERE id=?", (account_id,))
        if row is None:
            raise KeyError(f"Paper account {account_id} not found")
        return PaperAccount(
            id=row["id"], name=row["name"], starting_balance=row["starting_balance"], cash=row["cash"],
            created_at=_ts(row["created_at"]) or utcnow(), mode=row["mode"], clock_ts=_ts(row["clock_ts"]),
            status=row["status"], notes=row["notes"],
        )

    def list_accounts(self) -> list[PaperAccount]:
        return [self.get_account(r["id"]) for r in self.db.query("SELECT id FROM paper_accounts ORDER BY id")]

    def delete_account(self, account_id: int) -> None:
        self.db.execute("DELETE FROM paper_positions WHERE account_id=?", (account_id,))
        self.db.execute("DELETE FROM paper_accounts WHERE id=?", (account_id,))

    def resume_after_review(self, account_id: int) -> PaperAccount:
        acc = self.get_account(account_id)
        self.db.execute("UPDATE paper_accounts SET status='active' WHERE id=?", (account_id,))
        self.db.add_log("WARNING", "risk", f"User acknowledged the halt on paper account {acc.name} and resumed it.", {"account_id": account_id})
        return self.get_account(account_id)

    # ---------------------------------------------------------------- positions
    def positions(self, account_id: int, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_positions WHERE account_id=?"
        params: list[Any] = [account_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id"
        return [dict(r) for r in self.db.query(sql, params)]

    def _latest_close(self, dataset_id: int, at: datetime | None) -> tuple[float, datetime] | None:
        bars = self.db.get_bars(dataset_id, end=at)
        if not bars:
            return None
        return bars[-1].close, bars[-1].ts

    def summary(self, account_id: int) -> dict[str, Any]:
        acc = self.get_account(account_id)
        open_pos = self.positions(account_id, "open")
        closed = self.positions(account_id, "closed")
        unrealized = 0.0
        enriched = []
        for p in open_pos:
            mark = self._latest_close(p["dataset_id"], acc.clock_ts)
            price = mark[0] if mark else p["entry_price"]
            u = (price - p["entry_price"]) * p["quantity"] - p["entry_fees"]
            unrealized += u
            q = dict(p)
            q["mark_price"] = price
            q["mark_ts"] = mark[1].isoformat() if mark else None
            q["unrealized_pnl"] = round(u, 4)
            enriched.append(q)
        realized = sum(p["realized_pnl"] or 0.0 for p in closed)
        equity = acc.cash + sum((p["mark_price"] * p["quantity"]) for p in enriched)
        day = acc.clock_ts.date().isoformat() if acc.clock_ts else None
        realized_today = sum((p["realized_pnl"] or 0.0) for p in closed if day and p["exit_ts"] and p["exit_ts"][:10] == day)
        wins = [p for p in closed if (p["realized_pnl"] or 0) > 0]
        return {
            "account": acc.as_dict(),
            "equity": round(equity, 4),
            "cash": round(acc.cash, 4),
            "unrealized_pnl": round(unrealized, 4),
            "realized_pnl": round(realized, 4),
            "realized_today": round(realized_today, 4),
            "return_pct": round((equity / acc.starting_balance - 1) * 100, 3) if acc.starting_balance else 0.0,
            "open_positions": enriched,
            "closed_trades": len(closed),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "disclaimer": "Paper-trading results are simulated and are not real money or a forecast of real results.",
        }

    def _account_state(self, account_id: int, risk_settings: RiskSettings) -> AccountState:
        s = self.summary(account_id)
        acc = self.get_account(account_id)
        open_risks = [
            OpenRisk(symbol=p["symbol"], asset_class=AssetClass(p["asset_class"]), planned_risk=p["planned_risk"],
                     notional=p["entry_price"] * p["quantity"], strategy=p["strategy"])
            for p in s["open_positions"]
        ]
        return AccountState(
            equity=s["equity"], cash=s["cash"], starting_balance=acc.starting_balance, day_start_equity=s["equity"],
            realized_today=s["realized_today"], unrealized=s["unrealized_pnl"],
            current_day=acc.clock_ts.date() if acc.clock_ts else None, open_positions=open_risks,
            halted_for_review=acc.status == "halted",
        )

    # --------------------------------------------------------------- execution
    def open_position_from_candidate(self, account_id: int, candidate: dict[str, Any], fill_price: float, fill_ts: datetime,
                                     risk_settings: RiskSettings) -> dict[str, Any]:
        """Open a position for an approved candidate (already risk-checked at scan time; re-checked here)."""
        acc = self.get_account(account_id)
        ds = self.db.get_dataset(int(candidate["dataset_id"]))
        if ds is None:
            raise KeyError("Dataset for candidate not found")
        rm = RiskManager(risk_settings)
        costs = rm.cost_model(ds.asset_class)
        qty = float(candidate["quantity"])
        notional = qty * fill_price
        fee = costs.fees_only(notional)
        if notional + fee > acc.cash + 1e-9:
            qty = costs.round_quantity((acc.cash - costs.commission_fixed) / (fill_price * (1 + costs.commission_pct)), True)
            notional = qty * fill_price
            fee = costs.fees_only(notional)
        if qty <= 0:
            raise ValueError("Insufficient simulated cash to open the position.")
        self.db.execute("UPDATE paper_accounts SET cash=cash-? WHERE id=?", (notional + fee, account_id))
        pos_id = self.db.insert_json_row(
            "paper_positions", account_id=account_id, dataset_id=ds.id, symbol=ds.symbol, asset_class=ds.asset_class.value,
            strategy=candidate["strategy"], direction="long", quantity=qty, entry_price=fill_price, entry_ts=fill_ts.isoformat(),
            stop_price=float(candidate["stop_price"]), target_price=float(candidate["target_price"]), entry_fees=fee,
            max_holding_bars=candidate.get("max_holding_bars"), bars_held=0, status="open",
            planned_risk=float(candidate["potential_loss"]), data_status=ds.status.value,
        )
        self.db.add_log("INFO", "paper", f"Opened simulated {ds.symbol} position: {qty:g} @ {fill_price:.4f} ({candidate['strategy']}).",
                        {"account_id": account_id, "position_id": pos_id, "data_status": ds.status.value, "stop": candidate["stop_price"], "target": candidate["target_price"]})
        return dict(self.db.query_one("SELECT * FROM paper_positions WHERE id=?", (pos_id,)))

    def close_position(self, position_id: int, exit_price: float, exit_ts: datetime, reason: str, risk_settings: RiskSettings) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM paper_positions WHERE id=?", (position_id,))
        if row is None or row["status"] != "open":
            raise KeyError("Open position not found")
        rm = RiskManager(risk_settings)
        costs = rm.cost_model(AssetClass(row["asset_class"]))
        notional = exit_price * row["quantity"]
        fee = costs.fees_only(notional)
        pnl = (exit_price - row["entry_price"]) * row["quantity"] - row["entry_fees"] - fee
        self.db.execute("UPDATE paper_accounts SET cash=cash+? WHERE id=?", (notional - fee, row["account_id"]))
        self.db.execute(
            "UPDATE paper_positions SET status='closed', exit_price=?, exit_ts=?, exit_fees=?, exit_reason=?, realized_pnl=? WHERE id=?",
            (exit_price, exit_ts.isoformat(), fee, reason, pnl, position_id),
        )
        self.db.add_log("INFO", "paper", f"Closed simulated {row['symbol']} position @ {exit_price:.4f}: {reason} (P&L ${pnl:+.2f}).",
                        {"account_id": row["account_id"], "position_id": position_id, "pnl": pnl})
        return dict(self.db.query_one("SELECT * FROM paper_positions WHERE id=?", (position_id,)))

    def manual_close(self, position_id: int, risk_settings: RiskSettings) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM paper_positions WHERE id=?", (position_id,))
        if row is None:
            raise KeyError("Position not found")
        acc = self.get_account(row["account_id"])
        mark = self._latest_close(row["dataset_id"], acc.clock_ts)
        if mark is None:
            raise ValueError("No price available to close at.")
        rm = RiskManager(risk_settings)
        costs = rm.cost_model(AssetClass(row["asset_class"]))
        price = mark[0] * (1 - costs.spread_pct - costs.slippage_pct)
        return self.close_position(position_id, price, mark[1], "Closed manually by user at the latest available close.", risk_settings)

    # ------------------------------------------------------------------- clock
    def _next_clock(self, dataset_ids: list[int], after: datetime | None) -> datetime | None:
        """The earliest bar timestamp strictly after ``after`` across datasets."""
        candidates = []
        for did in dataset_ids:
            if after is None:
                row = self.db.query_one("SELECT MIN(ts) AS ts FROM bars WHERE dataset_id=?", (did,))
            else:
                row = self.db.query_one("SELECT MIN(ts) AS ts FROM bars WHERE dataset_id=? AND ts>?", (did, after.isoformat()))
            if row and row["ts"]:
                candidates.append(_ts(row["ts"]))
        return min(candidates) if candidates else None

    def advance(
        self,
        account_id: int,
        dataset_ids: list[int],
        strategies: list[tuple[Strategy, dict[str, Any]]],
        risk_settings: RiskSettings,
        steps: int = 1,
        benchmark_dataset_id: int | None = None,
        paused_strategies: set[str] | None = None,
        max_new_positions_per_step: int = 1,
    ) -> dict[str, Any]:
        """Advance the account clock ``steps`` bars, replaying exits, entries and scans."""
        acc = self.get_account(account_id)
        events: list[dict[str, Any]] = []
        bars_cache: dict[int, list[Bar]] = {did: self.db.get_bars(did) for did in dataset_ids}
        datasets: dict[int, Dataset] = {did: self.db.get_dataset(did) for did in dataset_ids}  # type: ignore[misc]
        rm = RiskManager(risk_settings)
        pending: list[dict[str, Any]] = list(self.db.get_setting(f"paper_pending_{account_id}", []) or [])

        for _ in range(steps):
            nxt = self._next_clock(dataset_ids, acc.clock_ts)
            if nxt is None:
                events.append({"type": "end_of_data", "message": "No more bars available. Import or download newer data to continue."})
                break
            acc_state_before = self._account_state(account_id, risk_settings)
            # 1) Fill pending entries at this bar's open (signals were generated on the previous close).
            still_pending = []
            for cand in pending:
                did = int(cand["dataset_id"])
                bar = next((b for b in bars_cache.get(did, []) if b.ts == nxt), None)
                if bar is None:
                    still_pending.append(cand)  # this dataset has no bar at this clock (e.g. weekend for stocks)
                    continue
                costs = rm.cost_model(datasets[did].asset_class)
                fill = bar.open * (1 + costs.spread_pct + costs.slippage_pct)
                if fill <= cand["stop_price"]:
                    events.append({"type": "entry_skipped", "symbol": cand["symbol"], "message": "Open gapped below the stop; entry skipped."})
                    self.db.add_log("INFO", "paper", f"Entry for {cand['symbol']} skipped: open gapped below the stop.", {"account_id": account_id})
                    continue
                # Re-run the risk gate at fill time (account may have changed).
                decision_state = self._account_state(account_id, risk_settings)
                if decision_state.halted_for_review or len(decision_state.open_positions) >= risk_settings.max_positions:
                    events.append({"type": "entry_skipped", "symbol": cand["symbol"], "message": "Risk gate blocked the fill (halt or max positions)."})
                    continue
                try:
                    pos = self.open_position_from_candidate(account_id, cand, fill, bar.ts, risk_settings)
                    events.append({"type": "entry", "symbol": cand["symbol"], "strategy": cand["strategy"], "price": round(fill, 4),
                                   "quantity": pos["quantity"], "ts": bar.ts.isoformat(), "data_status": pos["data_status"]})
                except ValueError as exc:
                    events.append({"type": "entry_skipped", "symbol": cand["symbol"], "message": str(exc)})
            pending = still_pending

            # 2) Manage open positions on this bar.
            for p in self.positions(account_id, "open"):
                did = p["dataset_id"]
                bars = bars_cache.get(did) or self.db.get_bars(did)
                idx = next((i for i, b in enumerate(bars) if b.ts == nxt), None)
                if idx is None:
                    continue
                bar = bars[idx]
                if p["entry_ts"] == bar.ts.isoformat():
                    # Entered at this bar's open; stop/target can still trigger intrabar.
                    pass
                costs = rm.cost_model(AssetClass(p["asset_class"]))
                slip = 1 - costs.spread_pct - costs.slippage_pct
                exit_price = None
                reason = ""
                pending_reason = p.get("exit_reason") if p["status"] == "open" and p.get("exit_reason") else None
                if pending_reason:
                    exit_price, reason = bar.open * slip, pending_reason
                elif bar.open <= p["stop_price"]:
                    exit_price, reason = bar.open * slip, "Stop hit on a gap down (filled at the open)."
                elif bar.low <= p["stop_price"]:
                    exit_price, reason = p["stop_price"] * slip, "Protective stop hit."
                elif bar.open >= p["target_price"]:
                    exit_price, reason = bar.open * slip, "Target exceeded on a gap up (filled at the open)."
                elif bar.high >= p["target_price"]:
                    exit_price, reason = p["target_price"] * slip, "Profit target hit."
                if exit_price is not None:
                    closed = self.close_position(p["id"], exit_price, bar.ts, reason, risk_settings)
                    events.append({"type": "exit", "symbol": p["symbol"], "price": round(exit_price, 4), "reason": reason,
                                   "pnl": round(closed["realized_pnl"], 4), "ts": bar.ts.isoformat()})
                    continue
                # Strategy exit rules and time stop decided on the close -> exit at next open.
                bars_held = idx - next((i for i, b in enumerate(bars) if b.ts.isoformat() == p["entry_ts"]), idx)
                self.db.execute("UPDATE paper_positions SET bars_held=? WHERE id=?", (bars_held, p["id"]))
                try:
                    strategy = get_strategy(p["strategy"])
                except KeyError:
                    continue
                params = next((pr for st, pr in strategies if st.key == strategy.key), {})
                params = strategy.merge_params(params)
                hist = bars[: idx + 1]
                indicators = strategy.prepare(hist, params)
                view = build_view(hist, indicators, idx)
                state = OpenPositionState(entry_price=p["entry_price"], stop_price=p["stop_price"], target_price=p["target_price"],
                                          entry_idx=idx - bars_held, bars_held=bars_held, extra={})
                exit_reason = strategy.exit_signal(view, state, params)
                if not exit_reason and p["max_holding_bars"] and bars_held >= p["max_holding_bars"]:
                    exit_reason = f"Time stop: held {bars_held} bars."
                if exit_reason:
                    self.db.execute("UPDATE paper_positions SET exit_reason=? WHERE id=?", (exit_reason, p["id"]))
                    events.append({"type": "exit_scheduled", "symbol": p["symbol"], "reason": exit_reason, "ts": bar.ts.isoformat()})

            # 3) Scan on the close for new signals -> pending entries for the next bar.
            acc.clock_ts = nxt
            self.db.execute("UPDATE paper_accounts SET clock_ts=? WHERE id=?", (nxt.isoformat(), account_id))
            state = self._account_state(account_id, risk_settings)
            gates = rm.account_gates(state)
            halted_now = any(g.get("halt") and not g["passed"] for g in gates)
            if halted_now and acc.status != "halted":
                self.db.execute("UPDATE paper_accounts SET status='halted' WHERE id=?", (account_id,))
                acc.status = "halted"
                reason = "; ".join(g["detail"] for g in gates if g.get("halt") and not g["passed"])
                events.append({"type": "halt", "message": f"Account halted: {reason} Review required before resuming."})
                self.db.add_log("WARNING", "risk", f"Paper account {acc.name} halted: {reason}", {"account_id": account_id})
            blocked = [g["detail"] for g in gates if not g["passed"]]
            if blocked:
                events.append({"type": "no_new_entries", "ts": nxt.isoformat(), "message": "; ".join(blocked)})
                self.db.add_log("INFO", "risk", f"No new entries for {acc.name} at {nxt.date()}: " + "; ".join(blocked), {"account_id": account_id})
            else:
                scan_sets = [(datasets[did], bars_cache[did]) for did in dataset_ids if datasets.get(did)]
                bench = None
                if benchmark_dataset_id is not None and benchmark_dataset_id in datasets:
                    bench = (datasets[benchmark_dataset_id], bars_cache[benchmark_dataset_id])
                result = scan_opportunities(scan_sets, strategies, state, risk_settings, as_of=nxt, benchmark=bench,
                                            paused_strategies=paused_strategies)
                taken = 0
                held = {p["symbol"] for p in self.positions(account_id, "open")} | {c["symbol"] for c in pending}
                for cand in result.candidates:
                    if taken >= max_new_positions_per_step or cand["symbol"] in held:
                        continue
                    pending.append(cand)
                    held.add(cand["symbol"])
                    taken += 1
                    events.append({"type": "signal", "symbol": cand["symbol"], "strategy": cand["strategy"], "ts": nxt.isoformat(),
                                   "evidence_score": cand["evidence_score"], "message": "Queued to buy at the next open."})
                    self.db.add_log("INFO", "scan", f"{acc.name}: {cand['strategy']} signal on {cand['symbol']} queued for next open (score {cand['evidence_score']}).",
                                    {"account_id": account_id, "candidate": {k: cand[k] for k in ("entry_price", "stop_price", "target_price", "quantity", "reward_to_risk")}})
                if not result.candidates:
                    events.append({"type": "no_candidates", "ts": nxt.isoformat(), "message": result.summary["message"]})
                for rej in result.rejected:
                    if rej.get("stage") in ("risk", "regime", "evidence") and "Already holding" not in rej["reason"]:
                        self.db.add_log("INFO", "rejected", f"{rej['symbol']} {rej.get('strategy', '')}: {rej['reason']}", {"account_id": account_id, "stage": rej["stage"]})
        self.db.set_setting(f"paper_pending_{account_id}", pending)
        return {"account": self.summary(account_id), "events": events, "pending_entries": pending}
