"""Company profile adapter (Lane 1 -- free, from the SEC `submissions` endpoint).

The same `submissions` JSON we read for SIC also carries durable company metadata: the listing
exchange(s), state/country of incorporation, fiscal-year end, former names, the business address,
and the full filing history. We extract a flat profile into the `company_profile` table for the
report's "company overview".

Honest scope (Phase 1 -- EDGAR only, fully shareable):
  - **No business description here.** The narrative description (10-K "Item 1. Business") is a later
    phase; this adapter is structured metadata only.
  - **"Public since" is a proxy.** We report the earliest filing date present in the submissions
    `recent` index, labelled as "earliest SEC filing on record" -- NOT a fabricated founding date
    (SEC submissions has no incorporation/founding date). Very old filers can have earlier filings
    in paginated shards we don't page through, so it is a lower bound, stated as such.
  - **Current, not point-in-time** (exchange/incorporation can change); matches how `securities`
    already treats SIC. Absent fields are stored blank and render as "Not estimated".
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import edgar, http, storage

_REQUEST_DELAY_S = 0.15  # stay well under SEC's ~10 req/s


def _submissions(cik: int, cfg: Config) -> dict:
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    return http.get_json(url, cfg.sec_user_agent)


def parse_profile(data: dict) -> dict:
    """Extract the flat profile fields from a SEC submissions JSON (pure -> unit-tested)."""
    exchanges = data.get("exchanges") or []
    addr = (data.get("addresses") or {}).get("business") or {}
    former = data.get("formerNames") or []
    former_str = "; ".join(str(f.get("name") or "").strip() for f in former if f.get("name"))

    recent = (data.get("filings") or {}).get("recent") or {}
    filing_dates = [d for d in (recent.get("filingDate") or []) if d]
    # ISO date strings sort lexicographically, so min() is the earliest filing in the recent index.
    first_filing = min(filing_dates) if filing_dates else ""

    return {
        "name": str(data.get("name") or "").strip(),
        "exchanges": ", ".join(str(e) for e in exchanges if e),
        "state_of_incorp": str(data.get("stateOfIncorporation") or "").strip(),
        "fiscal_year_end": str(data.get("fiscalYearEnd") or "").strip(),  # "MMDD"
        "category": str(data.get("category") or "").strip(),
        "entity_type": str(data.get("entityType") or "").strip(),
        "hq_city": str(addr.get("city") or "").strip(),
        "hq_state": str(addr.get("stateOrCountry") or "").strip(),
        "former_names": former_str,
        "first_filing_date": first_filing,  # ISO date string or ""
    }


def parse_recent_filings(data: dict, cik: int, n: int = 15) -> list[dict]:
    """Extract the most recent `n` SEC filings from a submissions JSON (pure -> unit-tested).

    The submissions `recent` arrays are parallel and newest-first; each filing becomes a flat row
    with a direct link to the primary document on EDGAR (a freely shareable primary source).
    """
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accns = recent.get("accessionNumber") or []
    primary = recent.get("primaryDocument") or []
    descs = recent.get("primaryDocDescription") or []
    out: list[dict] = []
    for i in range(min(len(forms), len(dates))):
        accn = accns[i] if i < len(accns) else ""
        doc = primary[i] if i < len(primary) else ""
        accn_nodash = str(accn).replace("-", "")
        if accn_nodash and doc:
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{doc}"
        else:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik):010d}"
        out.append({"filed_date": str(dates[i]), "form": str(forms[i]),
                    "description": str(descs[i] if i < len(descs) else "") or "",
                    "url": url, "accession": str(accn)})
        if len(out) >= n:
            break
    return out


def fetch_profile(cik: int, cfg: Config = config) -> dict:
    """Fetch + parse one company's profile from the SEC submissions endpoint."""
    return parse_profile(_submissions(cik, cfg))


def ingest_profiles(
    tickers: list[str], cfg: Config = config, snapshot_date: dt.date | None = None,
    skip_existing: bool = False,
) -> dict:
    """Fetch company profiles for `tickers` into the `company_profile` table (accumulates by ticker).

    Resumable like `ingest_sectors`: with `skip_existing`, tickers already in `company_profile` are
    skipped; one failing fetch is recorded as missing rather than aborting the run.
    """
    snapshot_date = snapshot_date or dt.date.today()
    cik_map = edgar.get_cik_map(cfg)
    try:
        names = edgar.get_company_names(cfg)
    except Exception:
        names = {}

    if skip_existing:
        conn0 = storage.connect(cfg)
        try:
            if storage.table_exists(conn0, "company_profile"):
                already = {r[0] for r in conn0.execute(
                    "SELECT DISTINCT ticker FROM company_profile").fetchall()}
                tickers = [t for t in tickers if t.upper() not in already]
        finally:
            conn0.close()

    rows: list[dict] = []
    filing_rows: list[dict] = []
    resolved: list[str] = []
    missing_cik: list[str] = []
    for t in tickers:
        tu = t.upper()
        cik = cik_map.get(tu)
        if cik is None:
            missing_cik.append(tu)
            continue
        try:
            data = _submissions(int(cik), cfg)   # one request -> both profile + recent filings
        except Exception:
            missing_cik.append(tu)            # transient SEC error -> skip this name, keep going
            time.sleep(_REQUEST_DELAY_S)
            continue
        prof = parse_profile(data)
        rows.append({"ticker": tu, "cik": int(cik),
                     "company": prof["name"] or names.get(tu, ""), **{
                         k: v for k, v in prof.items() if k != "name"}})
        for f in parse_recent_filings(data, int(cik)):
            filing_rows.append({"ticker": tu, "cik": int(cik), **f})
        resolved.append(tu)
        time.sleep(_REQUEST_DELAY_S)

    summary = {"rows": 0, "tickers_resolved": resolved, "tickers_missing_cik": missing_cik}
    if not rows:
        return summary

    df = pd.DataFrame(rows)
    conn = storage.connect(cfg)
    try:
        storage.write_snapshot(conn, df, "company_profile", source="SEC EDGAR submissions",
                               vendor="edgar", snapshot_date=snapshot_date, cfg=cfg)
        storage.upsert_by_key(conn, df, "company_profile", key_col="ticker")
        if filing_rows:
            fdf = pd.DataFrame(filing_rows)
            storage.write_snapshot(conn, fdf, "company_filings", source="SEC EDGAR submissions",
                                   vendor="edgar", snapshot_date=snapshot_date, cfg=cfg)
            # Many rows per ticker; upsert_by_ticker replaces a ticker's whole filing set on re-pull.
            storage.upsert_by_ticker(conn, fdf, "company_filings")
    finally:
        conn.close()
    summary["rows"] = len(df)
    summary["filings"] = len(filing_rows)
    return summary
