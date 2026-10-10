"""SQLite connection wrapper with a small typed repository layer."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..models import AssetClass, Bar, DataStatus, Dataset, utcnow
from .migrations import apply_migrations


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Database:
    """Thread-safe (serialized) access to the SQLite file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL") if str(self.path) != ":memory:" else None
        self._lock = threading.RLock()
        self.applied_migrations = apply_migrations(self._conn)

    # ------------------------------------------------------------------ core
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, [tuple(r) for r in rows])
            self._conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings_kv WHERE key=?", (key,))
        if row is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings_kv(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), utcnow().isoformat()),
        )

    # -------------------------------------------------------------- datasets
    def _row_to_dataset(self, row: sqlite3.Row) -> Dataset:
        return Dataset(
            id=row["id"],
            symbol=row["symbol"],
            name=row["name"],
            asset_class=AssetClass(row["asset_class"]),
            interval=row["interval"],
            source=row["source"],
            status=DataStatus(row["status"]),
            retrieved_at=_parse_ts(row["retrieved_at"]),
            timezone=row["timezone"],
            bar_count=row["bar_count"],
            first_ts=_parse_ts(row["first_ts"]) if row["first_ts"] else None,
            last_ts=_parse_ts(row["last_ts"]) if row["last_ts"] else None,
            quality_score=row["quality_score"],
            quality_notes=json.loads(row["quality_notes"] or "[]"),
            is_sample=bool(row["is_sample"]),
        )

    def list_datasets(self) -> list[Dataset]:
        rows = self.query("SELECT * FROM datasets ORDER BY asset_class, symbol")
        return [self._row_to_dataset(r) for r in rows]

    def get_dataset(self, dataset_id: int) -> Dataset | None:
        row = self.query_one("SELECT * FROM datasets WHERE id=?", (dataset_id,))
        return self._row_to_dataset(row) if row else None

    def find_dataset(self, symbol: str, interval: str, source: str) -> Dataset | None:
        row = self.query_one(
            "SELECT * FROM datasets WHERE symbol=? AND interval=? AND source=?",
            (symbol, interval, source),
        )
        return self._row_to_dataset(row) if row else None

    def upsert_dataset(self, ds: Dataset, bars: list[Bar]) -> Dataset:
        """Insert or replace a dataset and all of its bars (bars are sorted)."""
        bars = sorted(bars, key=lambda b: b.ts)
        ds.bar_count = len(bars)
        ds.first_ts = bars[0].ts if bars else None
        ds.last_ts = bars[-1].ts if bars else None
        with self._lock:
            existing = self.find_dataset(ds.symbol, ds.interval, ds.source)
            if existing:
                ds.id = existing.id
                self._conn.execute("DELETE FROM bars WHERE dataset_id=?", (ds.id,))
                self._conn.execute(
                    """UPDATE datasets SET name=?, asset_class=?, status=?, retrieved_at=?, timezone=?,
                       bar_count=?, first_ts=?, last_ts=?, quality_score=?, quality_notes=?, is_sample=?
                       WHERE id=?""",
                    (
                        ds.name, ds.asset_class.value, ds.status.value, ds.retrieved_at.isoformat(),
                        ds.timezone, ds.bar_count,
                        ds.first_ts.isoformat() if ds.first_ts else None,
                        ds.last_ts.isoformat() if ds.last_ts else None,
                        ds.quality_score, json.dumps(ds.quality_notes), int(ds.is_sample), ds.id,
                    ),
                )
            else:
                cur = self._conn.execute(
                    """INSERT INTO datasets(symbol, name, asset_class, interval, source, status, retrieved_at,
                       timezone, bar_count, first_ts, last_ts, quality_score, quality_notes, is_sample)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ds.symbol, ds.name, ds.asset_class.value, ds.interval, ds.source, ds.status.value,
                        ds.retrieved_at.isoformat(), ds.timezone, ds.bar_count,
                        ds.first_ts.isoformat() if ds.first_ts else None,
                        ds.last_ts.isoformat() if ds.last_ts else None,
                        ds.quality_score, json.dumps(ds.quality_notes), int(ds.is_sample),
                    ),
                )
                ds.id = cur.lastrowid
            self._conn.executemany(
                "INSERT INTO bars(dataset_id, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                [(ds.id, b.ts.isoformat(), b.open, b.high, b.low, b.close, b.volume) for b in bars],
            )
            self._conn.commit()
        return ds

    def delete_dataset(self, dataset_id: int) -> None:
        self.execute("DELETE FROM bars WHERE dataset_id=?", (dataset_id,))
        self.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))

    def get_bars(
        self,
        dataset_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[Bar]:
        sql = "SELECT ts, open, high, low, close, volume FROM bars WHERE dataset_id=?"
        params: list[Any] = [dataset_id]
        if start is not None:
            sql += " AND ts >= ?"
            params.append(start.astimezone(timezone.utc).isoformat())
        if end is not None:
            sql += " AND ts <= ?"
            params.append(end.astimezone(timezone.utc).isoformat())
        sql += " ORDER BY ts"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.query(sql, params)
        return [
            Bar(_parse_ts(r["ts"]), r["open"], r["high"], r["low"], r["close"], r["volume"])
            for r in rows
        ]

    # -------------------------------------------------------------- logging
    def add_log(self, level: str, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        self.execute(
            "INSERT INTO logs(ts, level, category, message, details) VALUES (?,?,?,?,?)",
            (utcnow().isoformat(), level, category, message, json.dumps(details or {}, default=str)),
        )

    def list_logs(self, limit: int = 200, category: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM logs"
        params: list[Any] = []
        if category:
            sql += " WHERE category=?"
            params.append(category)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [
            {
                "id": r["id"], "ts": r["ts"], "level": r["level"], "category": r["category"],
                "message": r["message"], "details": json.loads(r["details"] or "{}"),
            }
            for r in self.query(sql, params)
        ]

    # ------------------------------------------------------- generic JSON rows
    def insert_json_row(self, table: str, **columns: Any) -> int:
        keys = list(columns.keys())
        placeholders = ",".join("?" for _ in keys)
        values = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in columns.values()]
        cur = self.execute(f"INSERT INTO {table}({','.join(keys)}) VALUES ({placeholders})", values)
        return int(cur.lastrowid or 0)


_db_instance: Database | None = None
_db_lock = threading.Lock()


def get_database(path: Path | str | None = None) -> Database:
    """Process-wide singleton used by the API layer."""
    global _db_instance
    with _db_lock:
        if _db_instance is None:
            if path is None:
                from ..config import settings

                path = settings.db_path
            _db_instance = Database(path)
        return _db_instance


def reset_database_singleton() -> None:
    global _db_instance
    with _db_lock:
        if _db_instance is not None:
            _db_instance.close()
        _db_instance = None
