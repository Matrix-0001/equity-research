"""Point-in-time access — the heart of non-negotiable #2.

Every query answers "what was actually knowable on `as_of`?" so no downstream
screen, valuation, or backtest can see the future:

  - Fundamentals: filter `fundamentals` on `datekey <= as_of` (datekey = the date
    the filing became public) and take the latest such record per ticker. We only
    ingest as-reported figures, so restatements never leak in.
  - Prices: filter `prices` on `date <= as_of`.
  - Delisted names are NEVER excluded — a ticker simply has no rows after it stops
    trading, which is the correct (survivorship-free) behaviour.

Tables are vendor-neutral: any adapter (EDGAR, Stooq, Sharadar, ...) populates the
same `fundamentals` / `prices` schema, so this layer never changes when the data
source changes.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd


def _ticker_filter(tickers: list[str] | None, params: list) -> str:
    if not tickers:
        return ""
    params.extend(tickers)
    return f" AND ticker IN ({','.join('?' * len(tickers))})"


def fundamentals_asof(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Latest as-reported fundamentals known on or before `as_of`, one row/ticker."""
    params: list = [as_of]
    extra = _ticker_filter(tickers, params)
    return conn.execute(
        f"""
        WITH ranked AS (
            SELECT *,
                   row_number() OVER (PARTITION BY ticker ORDER BY datekey DESC) AS rn
            FROM fundamentals
            WHERE datekey <= ?{extra}
        )
        SELECT * EXCLUDE (rn) FROM ranked WHERE rn = 1
        """,
        params,
    ).df()


def prices_asof(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    lookback_days: int,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Price history in (as_of - lookback_days, as_of], delisted included."""
    start = as_of - dt.timedelta(days=lookback_days)
    params: list = [start, as_of]
    extra = _ticker_filter(tickers, params)
    return conn.execute(
        f"SELECT ticker, date, closeadj, closeunadj, volume FROM prices "
        f"WHERE date > ? AND date <= ?{extra} ORDER BY ticker, date",
        params,
    ).df()
