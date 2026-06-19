"""Free end-of-day price adapter (Lane 1, no API key).

Loads daily OHLCV into the vendor-neutral `prices` table. Uses Yahoo Finance's
public chart endpoint, which returns clean JSON with both a split-adjusted close
and a fully (split+dividend) adjusted close.

HONEST LIMITATIONS (these are why Lane 1 backtests are "indicative only"):
  - Survivorship bias: Yahoo serves currently/known-listed symbols; delisted names
    are not reliably retrievable. A backtest universe from this source is therefore
    survivorship-biased — fine for screening current names, NOT valid for v3
    historical backtests until a delisted-price source (EODHD / Sharadar) is added.
  - `closeunadj` is Yahoo's split-adjusted `close`, not the true raw print. For the
    latest date it equals the raw price (so current market caps are correct);
    historical pre-split unadjusted prices are approximated. Documented, not hidden.
  - Unofficial endpoint: undocumented and subject to change / rate limits, and its
    terms restrict redistribution (we only store locally for personal research).

When Lane 2 (Sharadar SEP) is switched on, it supplies true adjusted + unadjusted
series and delisted coverage, and this module is simply no longer used.
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import http, storage

_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
)
_REQUEST_DELAY_S = 0.3
_DEFAULT_RANGE = "2y"  # enough for 12-month momentum + liquidity windows


def fetch_prices(ticker: str, cfg: Config = config, rng: str = _DEFAULT_RANGE) -> pd.DataFrame:
    """Daily history for one ticker -> ticker/date/closeadj/closeunadj/volume."""
    empty = pd.DataFrame(columns=["ticker", "date", "closeadj", "closeunadj", "volume"])
    url = _CHART_URL.format(sym=ticker.upper(), rng=rng)
    payload = http.get_json(url, cfg.sec_user_agent)

    result = (payload.get("chart") or {}).get("result")
    if not result:
        return empty
    res = result[0]
    timestamps = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    adjnode = (res.get("indicators", {}).get("adjclose") or [{}])[0]
    adjcloses = adjnode.get("adjclose") or closes
    if not timestamps or not closes:
        return empty

    out = pd.DataFrame(
        {
            "ticker": ticker.upper(),
            "date": pd.to_datetime(timestamps, unit="s").normalize(),
            "closeadj": pd.to_numeric(pd.Series(adjcloses), errors="coerce"),
            "closeunadj": pd.to_numeric(pd.Series(closes), errors="coerce"),
            "volume": pd.to_numeric(pd.Series(volumes), errors="coerce"),
        }
    ).dropna(subset=["closeunadj"])
    return out


def ingest(
    tickers: list[str],
    cfg: Config = config,
    snapshot_date: dt.date | None = None,
) -> dict:
    """Fetch + load daily prices for `tickers` into the `prices` table."""
    snapshot_date = snapshot_date or dt.date.today()

    frames: list[pd.DataFrame] = []
    resolved: list[str] = []
    no_data: list[str] = []
    for t in tickers:
        df = fetch_prices(t, cfg)
        if df.empty:
            no_data.append(t)
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
            conn, prices, "prices", source="Yahoo Finance chart",
            vendor="yahoo", snapshot_date=snapshot_date, cfg=cfg,
        )
        storage.upsert_by_ticker(conn, prices, "prices")
    finally:
        conn.close()
    summary["rows"] = len(prices)
    return summary
