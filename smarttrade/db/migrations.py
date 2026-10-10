"""Versioned schema migrations for the local SQLite database.

Each entry is applied once, in order, and recorded in ``schema_migrations``.
Add a new tuple to MIGRATIONS to change the schema; never edit old ones.
"""

from __future__ import annotations

import sqlite3

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            interval TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'UTC',
            bar_count INTEGER NOT NULL DEFAULT 0,
            first_ts TEXT,
            last_ts TEXT,
            quality_score REAL,
            quality_notes TEXT NOT NULL DEFAULT '[]',
            is_sample INTEGER NOT NULL DEFAULT 0,
            UNIQUE(symbol, interval, source)
        );

        CREATE TABLE IF NOT EXISTS bars (
            dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            ts TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (dataset_id, ts)
        );

        CREATE TABLE IF NOT EXISTS settings_kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategy_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            version INTEGER NOT NULL,
            params TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(strategy, version)
        );

        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            dataset_id INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            params TEXT NOT NULL,
            start_ts TEXT,
            end_ts TEXT,
            starting_balance REAL NOT NULL,
            result TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            as_of TEXT,
            summary TEXT NOT NULL,
            candidates TEXT NOT NULL,
            rejected TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            strategy TEXT NOT NULL,
            dataset_id INTEGER NOT NULL,
            result TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            starting_balance REAL NOT NULL,
            cash REAL NOT NULL,
            created_at TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'replay',
            clock_ts TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            notes TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
            dataset_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,
            quantity REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_ts TEXT NOT NULL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            entry_fees REAL NOT NULL,
            max_holding_bars INTEGER,
            bars_held INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            exit_price REAL,
            exit_ts TEXT,
            exit_fees REAL,
            exit_reason TEXT,
            realized_pnl REAL,
            planned_risk REAL NOT NULL,
            data_status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
        CREATE INDEX IF NOT EXISTS idx_bars_dataset ON bars(dataset_id, ts);
        """,
    ),
]


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    newly: list[int] = []
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, datetime('now'))",
            (version,),
        )
        newly.append(version)
    conn.commit()
    return newly
