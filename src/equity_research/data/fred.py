"""FRED / ALFRED macro adapter (Lane 1 -- free).

Pulls macro series from the St. Louis Fed and loads them into the vendor-neutral
`macro` table. Crucially it pulls **ALFRED vintages** (the value of each observation
as it was known at each point in time), not just the latest revised series, so the
point-in-time layer can answer "what was knowable on date D" without macro lookahead.

Why this matters (non-negotiable #2): series like GDP, CPI and unemployment are
revised repeatedly after first release. Using today's revised value in a backtest
dated years ago is lookahead bias. ALFRED returns, for every observation date, each
vintage with the realtime window [realtime_start, realtime_end] during which it was
the current figure -- so `pit.macro_asof` can pick the vintage in force on `as_of`.
(Daily market rates like DGS10 are not revised; their vintage == the value.)

Live pulls need a free FRED API key (config.fred_api_key). The parsing and the
point-in-time logic are fully offline-testable.

Attribution: data courtesy of the Federal Reserve Bank of St. Louis (FRED/ALFRED).
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import http, storage

# Curated default series for rate / inflation / recession exposures. series_id -> label.
DEFAULT_SERIES: dict[str, str] = {
    "DGS10": "10-Year Treasury constant-maturity yield",
    "DGS2": "2-Year Treasury constant-maturity yield",
    "T10Y2Y": "10Y-2Y Treasury spread (yield-curve / recession signal)",
    "FEDFUNDS": "Effective federal funds rate",
    "CPIAUCSL": "CPI-U, all items (seasonally adjusted)",
    "UNRATE": "Civilian unemployment rate",
    "USREC": "NBER recession indicator (1 = recession)",
    "BAMLH0A0HYM2": "ICE BofA US high-yield option-adjusted spread (risk premium)",
}

# Series whose values get REVISED after first release -> need ALFRED vintages to be
# point-in-time honest. The rest (daily market rates / computed spreads) are not revised,
# so we pull plain observations and stamp each as knowable on its observation date.
# (FRED caps a vintage request at 2000 distinct vintage dates; a daily series has far more,
# which is the other reason not to request vintages for non-revised daily rates.)
REVISED_SERIES: set[str] = {"CPIAUCSL", "UNRATE", "USREC"}

_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
_VINTAGE_REALTIME = ("1990-01-01", "9999-12-31")  # ALFRED revision history (bounded, stays < cap)
_REQUEST_DELAY_S = 0.2
_UA = "equity-research/0.0.1 (macro)"


def _series_url(series_id: str, api_key: str, vintage: bool) -> str:
    url = f"{_BASE_URL}?series_id={series_id}&api_key={api_key}&file_type=json"
    if vintage:
        rt_start, rt_end = _VINTAGE_REALTIME
        url += f"&realtime_start={rt_start}&realtime_end={rt_end}"
    return url


def _parse_observations(
    payload: dict, series_id: str, stamp_obs_date_vintage: bool = False
) -> pd.DataFrame:
    """ALFRED/FRED observations JSON -> rows [series_id, date, value, realtime_start, realtime_end].

    A FRED "." value means missing for that period -> NaN (never fabricated). For a vintage pull
    each observation appears once per vintage, carrying the [realtime_start, realtime_end] window
    during which it was the current figure. For a non-revised series pulled plain,
    `stamp_obs_date_vintage` rewrites the realtime window to start on the observation date itself
    (the value of day d is knowable from day d on; ~1-business-day publication lag is ignored).
    """
    obs = payload.get("observations", [])
    rows: list[dict] = []
    for o in obs:
        raw = o.get("value")
        val = None if raw in (None, ".", "") else float(raw)
        if stamp_obs_date_vintage:
            rt_start, rt_end = o.get("date"), "9999-12-31"
        else:
            rt_start, rt_end = o.get("realtime_start"), o.get("realtime_end")
        rows.append({
            "series_id": series_id,
            "date": o.get("date"),
            "value": val,
            "realtime_start": rt_start,
            "realtime_end": rt_end,
        })
    cols = ["series_id", "date", "value", "realtime_start", "realtime_end"]
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        for c in ("date", "realtime_start", "realtime_end"):
            df[c] = pd.to_datetime(df[c], errors="coerce")
        df = df.sort_values(["date", "realtime_start"]).reset_index(drop=True)
    return df


def fetch_series(series_id: str, cfg: Config = config, vintage: bool | None = None) -> pd.DataFrame:
    """Fetch one series. `vintage=None` auto-selects: ALFRED vintages for revised series,
    plain observations stamped as-of-observation-date for non-revised ones."""
    if vintage is None:
        vintage = series_id in REVISED_SERIES
    api_key = cfg.require_fred_api_key()
    payload = http.get_json(_series_url(series_id, api_key, vintage), _UA)
    return _parse_observations(payload, series_id, stamp_obs_date_vintage=not vintage)


def ingest(
    series_ids: list[str] | None = None,
    cfg: Config = config,
    snapshot_date: dt.date | None = None,
) -> dict:
    """Fetch + load macro series into the `macro` table (accumulates by series_id).

    Each series auto-selects vintage vs plain handling (see `fetch_series`). Returns a summary
    dict: row count and which series resolved / had no data, surfaced honestly not silently dropped.
    """
    series_ids = series_ids or list(DEFAULT_SERIES)
    snapshot_date = snapshot_date or dt.date.today()

    frames: list[pd.DataFrame] = []
    resolved: list[str] = []
    no_data: list[str] = []
    for sid in series_ids:
        df = fetch_series(sid, cfg)
        if df.empty:
            no_data.append(sid)
        else:
            frames.append(df)
            resolved.append(sid)
        time.sleep(_REQUEST_DELAY_S)

    summary = {"rows": 0, "series_resolved": resolved, "series_no_data": no_data}
    if not frames:
        return summary

    allf = pd.concat(frames, ignore_index=True)
    conn = storage.connect(cfg)
    try:
        storage.write_snapshot(
            conn, allf, "macro", source="FRED/ALFRED", vendor="fred",
            snapshot_date=snapshot_date, cfg=cfg,
        )
        storage.upsert_by_key(conn, allf, "macro", key_col="series_id")
    finally:
        conn.close()
    summary["rows"] = len(allf)
    return summary
