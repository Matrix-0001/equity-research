"""Sharadar (via Nasdaq Data Link) ingestion.

Pulls the Lane-2 tables, snapshots each raw pull, and loads it into DuckDB.

⚠️ VALIDATE AGAINST THE LIVE API before relying on this. The table names, column
names, and the `dimension`/date semantics below reflect Sharadar's documented
schema, but they cannot be exercised without an API key. The first run with a
real key may surface column-name or pagination details to fix. Until then, treat
this module as a reviewed skeleton, not verified code.

Tables (Sharadar):
  SHARADAR/SF1      As-reported fundamentals. Key cols: ticker, dimension,
                    datekey (date the filing became known -> THE point-in-time key),
                    calendardate, reportperiod, + ~150 indicators.
  SHARADAR/SEP      End-of-day prices incl. delisted. Cols: ticker, date, open,
                    high, low, close, volume, closeadj (split+div adj), closeunadj,
                    dividends.
  SHARADAR/DAILY    Daily market metrics: ticker, date, marketcap, ev, pe, ps, pb,
                    evebit, evebitda. (marketcap here is point-in-time per day.)
  SHARADAR/TICKERS  Metadata: ticker, name, exchange, sector, industry, scalemarketcap,
                    isdelisted, firstpricedate, lastpricedate, category.
  SHARADAR/ACTIONS  Corporate actions incl. delisting events and reasons.
  SHARADAR/SP500    Historical (point-in-time) S&P 500 constituents (add/remove events).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from equity_research.config import Config, config

# Vendor-neutral DuckDB table name  ->  Sharadar table code on Nasdaq Data Link.
# (Deferred Lane-2 adapter: feeds the same `fundamentals` / `prices` schema the
#  EDGAR and Stooq adapters use, so switching to Sharadar is a drop-in.)
SHARADAR_TABLES: dict[str, str] = {
    "fundamentals": "SHARADAR/SF1",
    "prices": "SHARADAR/SEP",
    "daily_metrics": "SHARADAR/DAILY",
    "tickers": "SHARADAR/TICKERS",
    "actions": "SHARADAR/ACTIONS",
    "sp500": "SHARADAR/SP500",
}


def _client(cfg: Config):
    import nasdaqdatalink

    nasdaqdatalink.ApiConfig.api_key = cfg.require_api_key()
    return nasdaqdatalink


def fetch_table(table_code: str, cfg: Config = config, **filters) -> pd.DataFrame:
    """Fetch a full Sharadar table (paginated) as a DataFrame.

    `filters` are passed through as column filters, e.g. dimension="ARQ".
    `paginate=True` lets the client walk all pages for bulk tables.
    """
    ndl = _client(cfg)
    return ndl.get_table(table_code, paginate=True, **filters)


def ingest_all(cfg: Config = config, snapshot_date: dt.date | None = None) -> dict[str, int]:
    """Pull every Lane-2 table, snapshot it, load it into DuckDB. Returns row counts.

    For SF1 we pull ONLY the As-Reported dimension configured (default ARQ) — the
    point-in-time-correct view. Most-Recent (restated) dimensions are deliberately
    not ingested, to make lookahead bias structurally impossible at the source.
    """
    from equity_research.data import storage

    snapshot_date = snapshot_date or dt.date.today()
    conn = storage.connect(cfg)
    counts: dict[str, int] = {}
    try:
        for name, code in SHARADAR_TABLES.items():
            filters = {"dimension": cfg.fundamentals_dimension} if name == "fundamentals" else {}
            df = fetch_table(code, cfg=cfg, **filters)
            storage.write_snapshot(
                conn, df, name, source=code, vendor="sharadar",
                snapshot_date=snapshot_date, cfg=cfg,
            )
            storage.load_table(conn, df, name)
            counts[name] = len(df)
    finally:
        conn.close()
    return counts
