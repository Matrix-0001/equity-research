"""Sector classification adapter (Lane 1 -- free, from SEC SIC codes).

The SEC `submissions` endpoint exposes each company's `sic` + `sicDescription`. We fetch it and
map the SIC to a coarse, ~11-bucket GICS-like sector, stored in the vendor-neutral `securities`
table for the portfolio sector cap to use.

Honest limitations (stated, not hidden):
  - **Current, not point-in-time.** SEC submissions report today's SIC; we have no history of
    re-classifications. A company's SIC rarely changes, but this is NOT PIT-clean -- true
    point-in-time GICS membership is a Sharadar/Lane-2 upgrade.
  - **Coarse + approximate.** SIC predates GICS; the range->sector map below is best-effort
    (e.g. 357x/367x -> Information Technology, 283x -> Health Care). Unmapped/absent -> "Unknown",
    which the sector cap treats as exempt rather than forcing into a bucket.
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import edgar, http, storage

_REQUEST_DELAY_S = 0.15  # stay well under SEC's ~10 req/s
UNKNOWN = "Unknown"


def _in(s: int, *ranges: tuple[int, int]) -> bool:
    return any(lo <= s <= hi for lo, hi in ranges)


def sic_to_sector(sic: int | None) -> str:
    """Map a 4-digit SIC code to a coarse sector label. Ordered most-specific first.

    Approximate by design (SIC predates GICS); unknown/unmapped -> "Unknown".
    """
    if sic is None:
        return UNKNOWN
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return UNKNOWN
    if _in(s, (3570, 3579), (3670, 3679), (7370, 7379)):
        return "Information Technology"
    if _in(s, (2833, 2836), (3826, 3829), (3840, 3851), (8000, 8099)):
        return "Health Care"
    if _in(s, (1200, 1399), (2900, 2999)):
        return "Energy"
    if _in(s, (4900, 4999)):
        return "Utilities"
    if _in(s, (4800, 4899), (2700, 2799), (7800, 7841)):
        return "Communication Services"
    if _in(s, (6500, 6599)) or s == 6798:
        return "Real Estate"
    if _in(s, (6000, 6499), (6700, 6799)):
        return "Financials"
    if _in(s, (2000, 2199), (5400, 5499)) or s == 5912:
        return "Consumer Staples"
    if _in(s, (1000, 1099), (1400, 1499), (2600, 2699), (2800, 2832), (2840, 2899), (3200, 3399)):
        return "Materials"
    if _in(s, (2200, 2399), (2500, 2599), (3000, 3199), (3700, 3716), (3900, 3999),
           (5000, 5999), (7000, 7299), (7900, 7999)):
        return "Consumer Discretionary"
    if _in(s, (1500, 1799), (3400, 3569), (3580, 3669), (3680, 3699), (3720, 3799),
           (4000, 4799), (8700, 8744)):
        return "Industrials"
    return UNKNOWN


def fetch_sic(cik: int, cfg: Config = config) -> tuple[int | None, str]:
    """(sic, sic_description) from the SEC submissions endpoint; (None, '') if absent."""
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    data = http.get_json(url, cfg.sec_user_agent)
    raw = data.get("sic")
    try:
        sic = int(raw) if raw not in (None, "", "0") else None
    except (TypeError, ValueError):
        sic = None
    return sic, (data.get("sicDescription") or "")


def ingest_sectors(
    tickers: list[str], cfg: Config = config, snapshot_date: dt.date | None = None,
    skip_existing: bool = False,
) -> dict:
    """Fetch SIC + sector for `tickers` into the `securities` table (accumulates by ticker).

    With `skip_existing`, tickers already classified in `securities` are skipped (resumable for a
    1500-name universe). A single failing fetch is recorded as missing rather than aborting the run.
    Returns a summary dict: rows written, tickers resolved, tickers with no CIK / failed lookup.
    """
    snapshot_date = snapshot_date or dt.date.today()
    cik_map = edgar.get_cik_map(cfg)

    if skip_existing:
        conn0 = storage.connect(cfg)
        try:
            if storage.table_exists(conn0, "securities"):
                already = {r[0] for r in
                           conn0.execute("SELECT DISTINCT ticker FROM securities").fetchall()}
                tickers = [t for t in tickers if t.upper() not in already]
        finally:
            conn0.close()

    rows: list[dict] = []
    resolved: list[str] = []
    missing_cik: list[str] = []
    for t in tickers:
        tu = t.upper()
        cik = cik_map.get(tu)
        if cik is None:
            missing_cik.append(tu)
            continue
        try:
            sic, desc = fetch_sic(cik, cfg)
        except Exception:
            missing_cik.append(tu)            # transient SEC error -> skip this name, keep going
            time.sleep(_REQUEST_DELAY_S)
            continue
        rows.append({"ticker": tu, "cik": int(cik), "sic": sic,
                     "sic_description": desc, "sector": sic_to_sector(sic)})
        resolved.append(tu)
        time.sleep(_REQUEST_DELAY_S)

    summary = {"rows": 0, "tickers_resolved": resolved, "tickers_missing_cik": missing_cik}
    if not rows:
        return summary

    df = pd.DataFrame(rows)
    conn = storage.connect(cfg)
    try:
        storage.write_snapshot(conn, df, "securities", source="SEC EDGAR submissions (SIC)",
                               vendor="edgar", snapshot_date=snapshot_date, cfg=cfg)
        storage.upsert_by_key(conn, df, "securities", key_col="ticker")
    finally:
        conn.close()
    summary["rows"] = len(df)
    return summary
