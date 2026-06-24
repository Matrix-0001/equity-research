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

# We request by explicit epoch window (period1/period2) + interval=1d. This reliably returns DAILY
# bars over the whole span; the `range=max` keyword silently downsamples to quarterly, which would
# break the daily covariance, so we never use it. Deep daily history lets v3 backtests span real
# regimes (GFC 2008, COVID 2020, 2022) instead of sitting in cash during a covariance warm-up. Data
# is still survivorship-biased (current names only) -> backtests stay "indicative only".
_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    "?period1={p1}&period2={p2}&interval=1d"
)
_REQUEST_DELAY_S = 0.3
_DEFAULT_YEARS: float | None = None  # None = full available daily history


def fetch_prices(
    ticker: str, cfg: Config = config, years: float | None = _DEFAULT_YEARS
) -> pd.DataFrame:
    """Daily history for one ticker -> ticker/date/closeadj/closeunadj/volume.

    `years` bounds how far back to fetch (None = full history). Always DAILY (interval=1d).
    """
    empty = pd.DataFrame(columns=["ticker", "date", "closeadj", "closeunadj", "volume"])
    p2 = int(time.time()) + 86_400  # through today (pad a day for tz)
    p1 = 0 if years is None else max(0, p2 - int(years * 365.25 * 86_400))
    url = _CHART_URL.format(sym=ticker.upper(), p1=p1, p2=p2)
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
    years: float | None = _DEFAULT_YEARS,
    skip_existing: bool = False,
    batch_size: int = 50,
) -> dict:
    """Fetch + load daily prices for `tickers` into the `prices` table.

    `years` bounds the history depth (None = full daily history; deep by default for backtesting).
    RESUMABLE for large universes (S&P 1500): with `skip_existing`, tickers already in the `prices`
    table are skipped, and progress is persisted every `batch_size` names (per-batch snapshot +
    upsert, then the batch is freed to bound memory -- deep daily history for 1500 names is large).
    A single failing fetch is recorded (no_data) rather than aborting the pull.
    """
    snapshot_date = snapshot_date or dt.date.today()
    conn = storage.connect(cfg)
    try:
        already: set[str] = set()
        if skip_existing and storage.table_exists(conn, "prices"):
            already = {r[0] for r in
                       conn.execute("SELECT DISTINCT ticker FROM prices").fetchall()}
        todo = [t for t in tickers if t.upper() not in already]
        skipped = len(tickers) - len(todo)

        resolved: list[str] = []
        no_data: list[str] = []
        frames: list[pd.DataFrame] = []
        rows = 0
        batch_no = 0

        def _flush() -> None:
            nonlocal frames, rows, batch_no
            if not frames:
                return
            batch = pd.concat(frames, ignore_index=True)
            storage.write_snapshot(
                conn, batch, "prices", source="Yahoo Finance chart", vendor="yahoo",
                snapshot_date=snapshot_date, cfg=cfg, suffix=f"_b{batch_no:03d}",
            )
            storage.upsert_by_ticker(conn, batch, "prices")
            rows += len(batch)
            batch_no += 1
            frames = []

        for i, t in enumerate(todo, start=1):
            try:
                df = fetch_prices(t, cfg, years=years)
            except Exception:
                no_data.append(t.upper())
                time.sleep(_REQUEST_DELAY_S)
                continue
            if df.empty:
                no_data.append(t.upper())
            else:
                frames.append(df)
                resolved.append(t.upper())
            time.sleep(_REQUEST_DELAY_S)
            if i % batch_size == 0:
                _flush()
                print(f"  ... prices {i}/{len(todo)} ({len(resolved)} resolved) - progress saved",
                      flush=True)
        _flush()
        return {"rows": rows, "tickers_resolved": resolved, "tickers_no_data": no_data,
                "skipped": skipped}
    finally:
        conn.close()
