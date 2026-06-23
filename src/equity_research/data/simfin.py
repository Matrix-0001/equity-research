"""SimFin price adapter (free tier) -- the survivorship-free price source for Phase B.

Unlike Yahoo (which drops delisted names and recycles their tickers for new companies), SimFin keys
data by a stable entity id and KEEPS delisted names with their real history (e.g. SIVB runs to a
$0.01 final adjusted close -- SVB's actual collapse). This closes the one gap free data otherwise
can't: delisted prices.

Honest limitations (free tier):
  - History starts ~2019-06 (no GFC / dot-com). Backtests are survivorship-free only WITHIN the
    covered window; earlier dates are not.
  - Coverage is good but not perfect (some names/dates missing); pulls report what resolved.
  - SimFin's FUNDAMENTALS are restated (not point-in-time), so we DO NOT use them -- EDGAR remains
    the point-in-time fundamentals source. This adapter ingests PRICES (+ shares for market cap).

Output matches the vendor-neutral `prices` schema (ticker, date, closeadj, closeunadj, volume),
so `pit.py` / `universe.py` / the backtester consume it unchanged. `shares_outstanding` is also
captured so a survivorship-free universe can compute market cap without EDGAR CIK-mapping the
delisted names.
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import storage

_BASE = "https://backend.simfin.com/api/v3"
_PRICES_PATH = "companies/prices/compact"
_REQUEST_DELAY_S = 0.3
_FREE_HISTORY_START = "2019-01-01"  # free tier serves from ~2019-06; ask from Jan to be safe

# SimFin "compact" column name -> our schema.
_COL_DATE = "Date"
_COL_ADJ = "Adjusted Closing Price"
_COL_UNADJ = "Last Closing Price"
_COL_VOL = "Trading Volume"
_COL_SHARES = "Common Shares Outstanding"


def _parse_prices(payload: object, ticker: str) -> pd.DataFrame:
    """Parse a SimFin compact-prices payload into ticker/date/closeadj/closeunadj/volume/shares.

    Pure (no I/O) so it is hand-testable. Returns an empty frame for an empty/ill-shaped payload.
    Rows without an adjusted close are dropped (never imputed).
    """
    empty = pd.DataFrame(
        columns=["ticker", "date", "closeadj", "closeunadj", "volume", "shares_outstanding"]
    )
    if not isinstance(payload, list) or not payload:
        return empty
    rec = payload[0]
    if not isinstance(rec, dict):
        return empty
    cols = rec.get("columns") or []
    data = rec.get("data") or []
    if not cols or not data:
        return empty
    raw = pd.DataFrame(data, columns=cols)
    sym = str(rec.get("ticker") or ticker).upper()

    def col(name: str) -> pd.Series:
        return pd.to_numeric(raw[name], errors="coerce") if name in raw.columns else pd.Series(
            [None] * len(raw), dtype="float64"
        )

    out = pd.DataFrame({
        "ticker": sym,
        "date": pd.to_datetime(raw[_COL_DATE], errors="coerce"),
        "closeadj": col(_COL_ADJ),
        "closeunadj": col(_COL_UNADJ),
        "volume": col(_COL_VOL),
        "shares_outstanding": col(_COL_SHARES),
    })
    return out.dropna(subset=["date", "closeadj"]).reset_index(drop=True)


def fetch_prices(
    ticker: str,
    cfg: Config = config,
    start: str = _FREE_HISTORY_START,
    end: str | None = None,
    retries: int = 4,
    backoff_s: float = 2.0,
) -> pd.DataFrame:
    """Daily history for one ticker from SimFin (delisted-inclusive). Retries on the flaky network."""
    import requests

    key = cfg.require_simfin_api_key()
    end = end or dt.date.today().isoformat()
    headers = {"User-Agent": "equity-research/0.0.1", "accept": "application/json",
               "Authorization": key}
    params = {"ticker": ticker.upper(), "start": start, "end": end}
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(f"{_BASE}/{_PRICES_PATH}", headers=headers, params=params,
                                timeout=60)
            if resp.status_code == 429:  # rate limited -> back off and retry
                last_exc = RuntimeError("SimFin rate limit (429)")
                time.sleep(backoff_s * (attempt + 2))
                continue
            resp.raise_for_status()
            return _parse_prices(resp.json(), ticker)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff_s * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def ingest(
    tickers: list[str],
    cfg: Config = config,
    start: str = _FREE_HISTORY_START,
    snapshot_date: dt.date | None = None,
) -> dict:
    """Fetch + load SimFin daily prices (delisted-inclusive) for `tickers` into the `prices` table.

    Upserts by ticker (accumulates), like the other price adapters. `shares_outstanding` is kept in
    the snapshot/prices frame so a survivorship-free universe can compute market cap.
    """
    snapshot_date = snapshot_date or dt.date.today()
    frames: list[pd.DataFrame] = []
    resolved: list[str] = []
    no_data: list[str] = []
    for t in tickers:
        df = fetch_prices(t, cfg, start=start)
        if df.empty:
            no_data.append(t.upper())
        else:
            frames.append(df)
            resolved.append(t.upper())
        time.sleep(_REQUEST_DELAY_S)

    summary = {"rows": 0, "tickers_resolved": resolved, "tickers_no_data": no_data}
    if not frames:
        return summary
    prices = pd.concat(frames, ignore_index=True)
    conn = storage.connect(cfg)
    try:
        storage.write_snapshot(
            conn, prices, "prices", source="SimFin v3 compact prices",
            vendor="simfin", snapshot_date=snapshot_date, cfg=cfg,
        )
        storage.upsert_by_ticker(conn, prices, "prices")
    finally:
        conn.close()
    summary["rows"] = len(prices)
    return summary
