"""Survivorship-free index membership (DIY lane).

Free price lanes (Yahoo) only know *currently* listed names, so to define a survivorship-free
backtest universe we need the set of names that were in the index over time -- including ones that
later left or delisted. This module ingests point-in-time S&P 500 membership into an
`index_membership` table of (ticker, start_date, end_date) WINDOWS.

Primary source -- ``fja05680/sp500`` (MIT-licensed, Wikipedia-derived), file
``sp500_ticker_start_end.csv``: one row per membership window ``ticker,start_date,end_date``
(empty end_date = still in the index). A ticker can have multiple windows (added, removed,
re-added), all of which are kept. This gives EXACT point-in-time membership back to 1996
(reliable from ~2001). It is membership only -- it contains NO prices.

Honesty notes:
  - We use only public, openly-licensed FACTS (index membership is reconstructable from Wikipedia).
    No proprietary price series are ingested. The raw pull is snapshotted for reproducibility.
  - ``end_date`` is when a ticker left the INDEX, which is not always a delisting (a name can be
    removed yet keep trading, e.g. AAL 1997). Delisting OUTCOMES (acquired vs bankrupt) are a
    separate concern handled at price time; ``parse_symbol`` / ``delist_reasons`` can read those
    from a Norgate-style export if one is supplied, but they are optional enrichment.
"""

from __future__ import annotations

import datetime as dt
import io
import re

import duckdb
import pandas as pd

from equity_research.config import Config, config
from equity_research.data import storage

# fja05680/sp500 membership windows (MIT). Default universe source.
FJA_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
)
# Optional community Norgate-style export (delist dates + reason codes) -- enrichment only.
GVIZ_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1hSvyhUYPZ1BLwMiNp_WZy-bbCbPlYrSL-LgAvsgF7Xs/gviz/tq?tqx=out:csv"
)

# Norgate-style delisting suffix BASE.YYYYMMDD.REASON (e.g. AAMRQ.20131206.C). Optional enrichment.
DELIST_RE = re.compile(r"^(?P<base>.+)\.(?P<date>\d{8})\.(?P<reason>[A-Za-z0-9]+)$")
REASON_CODES = {  # best-effort interpretation, NOT authoritative
    "A": "acquired/merged or cash distribution",
    "C": "bankrupt/liquidated",
}


# --------------------------------------------------------------------------- #
# Source I/O
# --------------------------------------------------------------------------- #
def fetch_csv(source: str, cfg: Config = config) -> pd.DataFrame:
    """Read a membership CSV from a URL or a local file path."""
    if str(source).startswith("http"):
        import requests

        resp = requests.get(source, timeout=60, headers={"User-Agent": "equity-research/0.0.1"})
        resp.raise_for_status()
        return pd.read_csv(io.StringIO(resp.text))
    return pd.read_csv(source)


# --------------------------------------------------------------------------- #
# Primary: fja05680 membership windows
# --------------------------------------------------------------------------- #
def normalize_windows(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize an fja05680 `ticker,start_date,end_date` frame into the membership schema.

    One row per window; empty/blank end_date -> NaT (still a member). Rows without a ticker or a
    valid start date are dropped (never imputed).
    """
    cols = {c.lower().strip(): c for c in df.columns}
    t_c, s_c, e_c = cols.get("ticker"), cols.get("start_date"), cols.get("end_date")
    if t_c is None or s_c is None:
        raise ValueError("membership file must have 'ticker' and 'start_date' columns")
    out = pd.DataFrame({
        "ticker": df[t_c].astype(str).str.strip().str.upper(),
        "start_date": pd.to_datetime(df[s_c], errors="coerce"),
        "end_date": (pd.to_datetime(df[e_c], errors="coerce") if e_c else pd.NaT),
    })
    out["source"] = "fja05680/sp500 (MIT; Wikipedia-derived)"
    out = out[(out["ticker"] != "") & out["ticker"].notna() & out["start_date"].notna()]
    return out.sort_values(["ticker", "start_date"]).reset_index(drop=True)


def ingest(source: str = FJA_URL, cfg: Config = config, snapshot_date: dt.date | None = None) -> dict:
    """Fetch + load point-in-time S&P 500 membership WINDOWS into `index_membership` (full replace)."""
    mem = normalize_windows(fetch_csv(source, cfg))
    if mem.empty:
        return {"windows": 0, "tickers": 0, "current": 0}
    conn = storage.connect(cfg)
    try:
        storage.write_snapshot(
            conn, mem, "index_membership", source="fja05680/sp500 ticker_start_end",
            vendor="fja05680", snapshot_date=snapshot_date, cfg=cfg,
        )
        storage.load_table(conn, mem, "index_membership")  # full snapshot -> replace
    finally:
        conn.close()
    return {
        "windows": len(mem),
        "tickers": int(mem["ticker"].nunique()),
        "current": int(mem["end_date"].isna().sum()),
    }


# --------------------------------------------------------------------------- #
# Optional enrichment: delist reasons from a Norgate-style export (e.g. the shared sheet)
# --------------------------------------------------------------------------- #
def parse_symbol(symbol: str) -> tuple[str, dt.date | None, str | None]:
    """``BASE.YYYYMMDD.R`` -> (base, delist date, reason); plain ``BASE`` -> (BASE, None, None)."""
    s = str(symbol).strip()
    m = DELIST_RE.match(s)
    if not m:
        return s.upper(), None, None
    try:
        delist = dt.datetime.strptime(m.group("date"), "%Y%m%d").date()
    except ValueError:
        return s.upper(), None, None
    code = m.group("reason")
    return m.group("base").upper(), delist, REASON_CODES.get(code.upper(), f"delisted (code {code})")


def delist_reasons(df: pd.DataFrame) -> pd.DataFrame:
    """Extract (ticker, delist_date, delist_reason) from a Norgate-style export's Symbol column."""
    cols = {c.lower().strip(): c for c in df.columns}
    sym_c = cols.get("symbol")
    if sym_c is None:
        raise ValueError("export must have a 'Symbol' column")
    rows = []
    for raw in df[sym_c].astype(str):
        base, d, reason = parse_symbol(raw)
        if d is not None:
            rows.append({"ticker": base, "delist_date": pd.Timestamp(d), "delist_reason": reason})
    return pd.DataFrame(rows)
