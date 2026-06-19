"""Local storage: DuckDB warehouse + versioned raw parquet snapshots.

Reproducibility rule (Phase 0 Decision Log #4): every raw vendor pull is saved
to an immutable, date-stamped parquet snapshot BEFORE it is loaded into DuckDB.
Vendors may silently revise history; snapshots let any past result be
reconstructed against the exact bytes it was computed from.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd

from equity_research.config import Config, config

_SNAPSHOT_REGISTRY = "data_snapshots"


def connect(cfg: Config = config) -> duckdb.DuckDBPyConnection:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(cfg.db_path))
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_SNAPSHOT_REGISTRY} (
            table_name   VARCHAR,
            snapshot_date DATE,
            source       VARCHAR,
            row_count    BIGINT,
            parquet_path VARCHAR,
            pulled_at    TIMESTAMP
        )
        """
    )
    return conn


def snapshot_path(
    table_name: str, snapshot_date: dt.date, vendor: str, cfg: Config = config
) -> Path:
    d = cfg.raw_dir / vendor / table_name / snapshot_date.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{table_name}.parquet"


def write_snapshot(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    table_name: str,
    source: str,
    vendor: str,
    snapshot_date: dt.date | None = None,
    cfg: Config = config,
) -> Path:
    """Persist a raw pull to immutable parquet and register it. Returns the path."""
    snapshot_date = snapshot_date or dt.date.today()
    path = snapshot_path(table_name, snapshot_date, vendor, cfg)
    df.to_parquet(path, index=False)
    conn.execute(
        f"INSERT INTO {_SNAPSHOT_REGISTRY} VALUES (?, ?, ?, ?, ?, ?)",
        [table_name, snapshot_date, source, len(df), str(path), dt.datetime.now()],
    )
    return path


def load_table(
    conn: duckdb.DuckDBPyConnection,
    df: pd.DataFrame,
    table_name: str,
    replace: bool = True,
) -> None:
    """Load a dataframe into a DuckDB table (working copy; snapshots are the source of truth)."""
    conn.register("_incoming", df)
    if replace:
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM _incoming")
    else:
        conn.execute(f"INSERT INTO {table_name} SELECT * FROM _incoming")
    conn.unregister("_incoming")


def table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [table_name]
    ).fetchone()
    return row is not None


def upsert_by_ticker(
    conn: duckdb.DuckDBPyConnection, df: pd.DataFrame, table_name: str
) -> None:
    """Replace-by-ticker upsert: keep rows for other tickers, swap in the new ones.

    Lets the universe grow incrementally across pulls (re-pulling AAPL refreshes
    AAPL without wiping MSFT). Reconciles column drift via pandas concat, so adding
    new EDGAR concepts later doesn't break the append.
    """
    if df.empty:
        return
    tickers = df["ticker"].unique().tolist()
    if table_exists(conn, table_name):
        placeholders = ",".join("?" * len(tickers))
        existing = conn.execute(
            f"SELECT * FROM {table_name} WHERE ticker NOT IN ({placeholders})", tickers
        ).df()
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    load_table(conn, combined, table_name)


def latest_snapshot_date(conn: duckdb.DuckDBPyConnection, table_name: str) -> dt.date | None:
    row = conn.execute(
        f"SELECT max(snapshot_date) FROM {_SNAPSHOT_REGISTRY} WHERE table_name = ?",
        [table_name],
    ).fetchone()
    return row[0] if row and row[0] is not None else None
