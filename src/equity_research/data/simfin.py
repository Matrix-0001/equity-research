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
_REQUEST_DELAY_S = 0.7  # gentler pacing to stay under the free-tier rate limit
_FREE_HISTORY_START = "2019-01-01"  # free tier serves from ~2019-06; ask from Jan to be safe


class SimFinRateLimit(RuntimeError):
    """Raised when SimFin's free-tier rate limit (HTTP 429) persists; ingest stops gracefully so the
    pull can be resumed later (already-pulled names are skipped)."""

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
            if resp.status_code == 429:  # rate limited
                if attempt < retries - 1:
                    wait = float(resp.headers.get("Retry-After") or backoff_s * (attempt + 1) * 4)
                    time.sleep(min(wait, 90.0))
                    continue
                raise SimFinRateLimit("SimFin free-tier rate limit (429)")
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
    batch_size: int = 50,
    skip_existing: bool = True,
) -> dict:
    """Fetch + load SimFin daily prices (delisted-inclusive) for `tickers` into the `prices` table.

    RESUMABLE: with `skip_existing` (default), tickers already in the `prices` table are skipped, so a
    big pull that the free-tier rate limit cut short can simply be RE-RUN to continue. Progress is
    upserted every `batch_size` names, and a persistent 429 stops the run gracefully (`rate_limited`
    in the summary) rather than raising. `shares_outstanding` is kept so a survivorship-free universe
    can compute market cap. NOTE: run this into a dedicated `EQR_DATA_DIR` (e.g. ./data_simfin) so
    SimFin prices don't mix with Yahoo's (different adjustment methodologies).
    """
    snapshot_date = snapshot_date or dt.date.today()
    conn = storage.connect(cfg)
    try:
        already: set[str] = set()
        if skip_existing and storage.table_exists(conn, "prices"):
            already = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices").fetchall()}
        todo = [t for t in tickers if t.upper() not in already]
        skipped = len(tickers) - len(todo)

        frames: list[pd.DataFrame] = []
        resolved: list[str] = []
        no_data: list[str] = []
        rate_limited = False
        total = len(todo)
        for i, t in enumerate(todo, start=1):
            try:
                df = fetch_prices(t, cfg, start=start)
            except SimFinRateLimit:
                rate_limited = True
                break                                    # stop gracefully; re-run to resume
            except Exception:
                no_data.append(t.upper())                # one bad name shouldn't abort the pull
                time.sleep(_REQUEST_DELAY_S)
                continue
            if df.empty:
                no_data.append(t.upper())
            else:
                frames.append(df)
                resolved.append(t.upper())
            time.sleep(_REQUEST_DELAY_S)
            if frames and i % batch_size == 0:           # persist progress periodically
                storage.upsert_by_ticker(conn, pd.concat(frames, ignore_index=True), "prices")
                print(f"  ... {i}/{total} pulled ({len(resolved)} resolved) - progress saved",
                      flush=True)

        rows = 0
        if frames:
            prices = pd.concat(frames, ignore_index=True)
            storage.write_snapshot(
                conn, prices, "prices", source="SimFin v3 compact prices",
                vendor="simfin", snapshot_date=snapshot_date, cfg=cfg,
            )
            storage.upsert_by_ticker(conn, prices, "prices")
            rows = len(prices)
        return {"rows": rows, "tickers_resolved": resolved, "tickers_no_data": no_data,
                "skipped": skipped, "rate_limited": rate_limited}
    finally:
        conn.close()
