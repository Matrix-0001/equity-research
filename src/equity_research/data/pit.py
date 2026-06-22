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


ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")


def _in_filter(values: list[str] | None, params: list, col: str = "ticker") -> str:
    if not values:
        return ""
    params.extend(values)
    return f" AND {col} IN ({','.join('?' * len(values))})"


def _ticker_filter(tickers: list[str] | None, params: list) -> str:
    return _in_filter(tickers, params, "ticker")


def fundamentals_asof(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    tickers: list[str] | None = None,
    forms: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Latest as-reported fundamentals known on or before `as_of`, one row/ticker.

    `forms` optionally restricts to specific filing forms (e.g. annual 10-K/20-F/40-F)
    so callers can take a consistent annual basis instead of the most recent quarter.
    """
    params: list = [as_of]
    where = "datekey <= ?" + _ticker_filter(tickers, params)
    if forms:
        params.extend(list(forms))
        where += f" AND form IN ({','.join('?' * len(forms))})"
    return conn.execute(
        f"""
        WITH ranked AS (
            SELECT *,
                   row_number() OVER (PARTITION BY ticker ORDER BY datekey DESC) AS rn
            FROM fundamentals
            WHERE {where}
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


def last_price(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, ticker: str
) -> float | None:
    """Most recent unadjusted close known on/before `as_of` (None if none)."""
    px = prices_asof(conn, as_of, 15, [ticker])
    return None if px.empty else float(px.sort_values("date")["closeunadj"].iloc[-1])


def macro_asof(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    series_ids: list[str] | None = None,
) -> pd.DataFrame:
    """Latest macro value per series KNOWN on `as_of`, using ALFRED vintages (no lookahead).

    For each series we take, per observation date, the vintage whose `realtime_start <= as_of`
    with the largest realtime_start (the figure in force on `as_of`, not a later revision),
    then return the most recent observation date whose value had actually been released by
    `as_of`. One row per series: series_id, date (observation), value, vintage (realtime_start).
    Series with no value released by `as_of` simply do not appear.
    """
    params: list = [as_of]
    where = "realtime_start <= ?" + _in_filter(series_ids, params, "series_id")
    return conn.execute(
        f"""
        WITH known AS (SELECT * FROM macro WHERE {where}),
        current_vintage AS (
            SELECT *, row_number() OVER (
                PARTITION BY series_id, date ORDER BY realtime_start DESC) AS vrn
            FROM known
        ),
        latest_obs AS (
            SELECT * EXCLUDE (vrn), row_number() OVER (
                PARTITION BY series_id ORDER BY date DESC) AS orn
            FROM current_vintage
            WHERE vrn = 1 AND value IS NOT NULL AND isfinite(value)
        )
        SELECT series_id, date, value, realtime_start AS vintage
        FROM latest_obs WHERE orn = 1 ORDER BY series_id
        """,
        params,
    ).df()


def sectors_for(
    conn: duckdb.DuckDBPyConnection, tickers: list[str] | None = None
) -> dict[str, str]:
    """ticker -> sector from the `securities` table. Empty dict if the table isn't loaded yet.

    (Sector classification is current SIC, not point-in-time; see data/sectors.py.)
    """
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'securities'"
    ).fetchone()
    if not exists:
        return {}
    params: list = []
    where = "1=1" + _in_filter([t.upper() for t in tickers] if tickers else None, params, "ticker")
    rows = conn.execute(f"SELECT ticker, sector FROM securities WHERE {where}", params).fetchall()
    return {r[0]: r[1] for r in rows}


def macro_series_asof(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    series_id: str,
    lookback_days: int | None = None,
) -> pd.DataFrame:
    """Vintage-correct macro time series KNOWN on `as_of` -> DataFrame [date, value], date-sorted.

    For each observation date <= `as_of` we take the vintage in force on `as_of` (the latest
    `realtime_start <= as_of`), so the whole series reflects what was knowable on `as_of`, not
    later revisions. `lookback_days` optionally restricts to observations after as_of-lookback.
    """
    params: list = [series_id, as_of]
    where = "series_id = ? AND realtime_start <= ?"
    if lookback_days is not None:
        params.append(as_of - dt.timedelta(days=lookback_days))
        where += " AND date > ?"
    return conn.execute(
        f"""
        WITH known AS (SELECT * FROM macro WHERE {where}),
        current_vintage AS (
            SELECT *, row_number() OVER (PARTITION BY date ORDER BY realtime_start DESC) AS vrn
            FROM known
        )
        SELECT date, value FROM current_vintage
        WHERE vrn = 1 AND value IS NOT NULL AND isfinite(value)
        ORDER BY date
        """,
        params,
    ).df()


def annual_history(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    tickers: list[str] | None = None,
    n: int = 2,
) -> pd.DataFrame:
    """Latest `n` annual filings per ticker (distinct fiscal years), known by `as_of`.

    Adds `yr_rank` (1 = most recent fiscal year). Amendments of the same fiscal year
    are collapsed to their latest-filed version, so yr_rank 1 vs 2 are genuinely
    consecutive years — what year-over-year flags (Piotroski, Beneish) require.
    """
    params: list = [as_of]
    forms_ph = ",".join("?" * len(ANNUAL_FORMS))
    params.extend(ANNUAL_FORMS)
    where = f"datekey <= ? AND form IN ({forms_ph})" + _ticker_filter(tickers, params)
    return conn.execute(
        f"""
        WITH per_period AS (
            SELECT *,
                   row_number() OVER (PARTITION BY ticker, reportperiod
                                      ORDER BY datekey DESC) AS pp
            FROM fundamentals WHERE {where}
        ),
        ranked AS (
            SELECT * EXCLUDE (pp),
                   row_number() OVER (PARTITION BY ticker ORDER BY reportperiod DESC) AS yr_rank
            FROM per_period WHERE pp = 1
        )
        SELECT * FROM ranked WHERE yr_rank <= {int(n)} ORDER BY ticker, yr_rank
        """,
        params,
    ).df()
