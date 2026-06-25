"""Multi-factor library — value, quality, profitability, momentum, low-volatility.

Everything here is computed point-in-time and expressed as **percentile ranks
within the chosen universe** (0-100, higher = more attractive on that factor), per
the spec. These are *cross-sectional ranks*, not predictions and not buy signals.

Design choices (deliberately few, defensible factors with published out-of-sample
evidence):
  - value:          earnings yield (NI/mktcap), book-to-market (equity/mktcap)
  - quality:        return on equity (NI/equity)
  - profitability:  gross profitability (gross profit/assets; Novy-Marx), ROA
  - momentum:       12-1 month total return (skip the most recent month)
  - low-volatility: trailing daily-return volatility (inverted, so calmer = higher)
  - size:           market cap, INVERTED (smaller ranks higher) -- a tilt toward smaller,
                    less-followed names where mispricing is likelier. HONESTY CAVEAT: the
                    standalone small-cap premium is weak and has decayed since its discovery
                    and concentrates in illiquid micro-caps; it is NOT a return predictor on its
                    own. It earns its place only combined with value + quality (the composite
                    does this, and the completeness shrinkage keeps small *junk* with missing
                    fundamentals from ranking high) -- "small AND cheap AND good", never small alone.

Fundamentals use a consistent **annual (10-K) basis** taken point-in-time, so trailing
earnings line up with the latest audited year. Market cap and price factors use the
latest data known on `as_of`. Any metric whose inputs are missing is left NaN and is
simply absent from that name's ranks (it never becomes a fabricated 0).

**Data-completeness handling (so sparse data can't masquerade as strength):** the composite is the
mean of a name's AVAILABLE factor groups, then **shrunk toward the neutral 50th percentile by the
fraction of UNMEASURED groups** (`completeness`). A bank/REIT with no quality/profitability tags
therefore can't top the screen on just value+momentum+low-vol -- its missing factors count as no
signal, not strength. Fully-measured names are unchanged; `composite_raw` is kept for transparency
and raw group ranks stay "Not estimated" when absent (this is a ranking convention, not imputation
of any metric value).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import duckdb
import pandas as pd

from equity_research import universe
from equity_research._cache import cache_get
from equity_research.config import Config, config
from equity_research.data import pit

ANNUAL_FORMS = pit.ANNUAL_FORMS  # single source of truth lives in data.pit

# metric -> +1 if higher is more attractive, -1 if lower is (volatility, size).
METRIC_DIRECTION: dict[str, int] = {
    "earnings_yield": 1,
    "book_to_market": 1,
    "roe": 1,
    "gross_profitability": 1,
    "roa": 1,
    "momentum_12_1": 1,
    "volatility": -1,
    "size": -1,            # smaller market cap ranks higher (small-cap tilt)
}

# factor group -> contributing metrics (group rank = mean of available member ranks).
FACTOR_GROUPS: dict[str, list[str]] = {
    "value": ["earnings_yield", "book_to_market"],
    "quality": ["roe"],
    "profitability": ["gross_profitability", "roa"],
    "momentum": ["momentum_12_1"],
    "low_vol": ["volatility"],
    "size": ["size"],
}


@dataclass(frozen=True)
class FactorConfig:
    momentum_skip_days: int = 21       # skip the most recent ~month (12-1 momentum)
    momentum_lookback_days: int = 252  # ~12 trading months
    vol_lookback_days: int = 252
    min_vol_obs: int = 60              # need enough returns to estimate volatility


def _momentum(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, tickers: list[str], fcfg: FactorConfig
) -> pd.Series:
    px = pit.prices_asof(conn, as_of, fcfg.momentum_lookback_days + 45, tickers)
    end_cut = pd.Timestamp(as_of) - pd.Timedelta(days=fcfg.momentum_skip_days)
    start_cut = pd.Timestamp(as_of) - pd.Timedelta(days=fcfg.momentum_lookback_days)
    out: dict[str, float] = {}
    for tkr, g in px.sort_values("date").groupby("ticker"):
        recent = g[g["date"] <= end_cut]["closeadj"]
        base = g[g["date"] <= start_cut]["closeadj"]
        if recent.empty or base.empty or base.iloc[-1] <= 0:
            continue
        out[tkr] = recent.iloc[-1] / base.iloc[-1] - 1.0
    return pd.Series(out, dtype="float64")


def _volatility(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, tickers: list[str], fcfg: FactorConfig
) -> pd.Series:
    px = pit.prices_asof(conn, as_of, fcfg.vol_lookback_days + 15, tickers)
    out: dict[str, float] = {}
    for tkr, g in px.sort_values("date").groupby("ticker"):
        rets = g["closeadj"].pct_change().dropna()
        if len(rets) < fcfg.min_vol_obs:
            continue
        out[tkr] = float(rets.std() * (252 ** 0.5))
    return pd.Series(out, dtype="float64")


def compute_raw_metrics(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    cfg: Config = config,
    fcfg: FactorConfig = FactorConfig(),
    _cache: dict | None = None,
) -> pd.DataFrame:
    """Raw (un-ranked) factor metrics for the liquid screening universe as of `as_of`."""
    scr = universe.screening_universe(conn, as_of, cfg, _cache=_cache)
    if scr.empty:
        return pd.DataFrame()
    tickers = scr["ticker"].tolist()
    mcap = scr.set_index("ticker")["market_cap"]

    funds = pit.fundamentals_asof(conn, as_of, tickers, forms=ANNUAL_FORMS)
    funds = funds.set_index("ticker") if not funds.empty else pd.DataFrame(index=[])

    def col(name: str) -> pd.Series:
        return funds[name].reindex(tickers) if name in funds.columns else pd.Series(
            index=tickers, dtype="float64"
        )

    ni, eq, gp, ta = col("net_income"), col("total_equity"), col("gross_profit"), col("total_assets")

    raw = pd.DataFrame(index=pd.Index(tickers, name="ticker"))
    raw["market_cap"] = mcap
    raw["size"] = mcap                  # ranked inverted (smaller = higher) via METRIC_DIRECTION
    raw["earnings_yield"] = ni / mcap
    raw["book_to_market"] = eq / mcap
    raw["roe"] = ni / eq.where(eq > 0)          # ROE undefined for non-positive equity
    raw["roa"] = ni / ta.where(ta > 0)
    raw["gross_profitability"] = gp / ta.where(ta > 0)
    raw["momentum_12_1"] = _momentum(conn, as_of, tickers, fcfg).reindex(tickers)
    raw["volatility"] = _volatility(conn, as_of, tickers, fcfg).reindex(tickers)
    return raw


def percentile_ranks(raw: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile ranks (0-100, higher = more attractive)."""
    ranks = pd.DataFrame(index=raw.index)
    for metric, direction in METRIC_DIRECTION.items():
        if metric in raw.columns:
            ranks[metric] = (raw[metric] * direction).rank(pct=True) * 100.0
    return ranks


def factor_profile(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    cfg: Config = config,
    fcfg: FactorConfig = FactorConfig(),
    _cache: dict | None = None,
) -> pd.DataFrame:
    """Per-ticker factor-group percentile ranks + composite, for the universe.

    Columns: the five factor groups (value/quality/profitability/momentum/low_vol),
    a `composite` (mean of available groups), `n_metrics` (how many of the eight raw
    metrics were computable — feeds the report's data-completeness/confidence), and
    `market_cap`. Sorted by composite descending. `_cache` (run-scoped, optional) memoizes
    the whole profile by `as_of` + config so repeated rebalances/strategies don't recompute it.
    """
    return cache_get(
        _cache, ("factors", as_of.isoformat(), id(cfg), id(fcfg)),
        lambda: _factor_profile(conn, as_of, cfg, fcfg, _cache),
    )


def _factor_profile(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, cfg: Config, fcfg: FactorConfig,
    _cache: dict | None,
) -> pd.DataFrame:
    raw = compute_raw_metrics(conn, as_of, cfg, fcfg, _cache=_cache)
    if raw.empty:
        return raw
    ranks = percentile_ranks(raw)

    out = pd.DataFrame(index=raw.index)
    out["market_cap"] = raw["market_cap"]
    metric_cols = [m for m in METRIC_DIRECTION if m in ranks.columns]
    out["n_metrics"] = ranks[metric_cols].notna().sum(axis=1)
    for group, members in FACTOR_GROUPS.items():
        present = [m for m in members if m in ranks.columns]
        out[group] = ranks[present].mean(axis=1) if present else pd.NA

    # Data-completeness handling. The raw composite is the mean of a name's AVAILABLE factor groups,
    # which lets a sparse-data name (e.g. a bank/REIT with no quality/profitability tags) float to the
    # top on just value+momentum+low-vol -- a false hidden gem. So we shrink the composite toward the
    # NEUTRAL 50th percentile by the fraction of unmeasured groups: an unmeasured factor contributes no
    # signal (neither strength nor weakness), and the score regresses to neutral the less we can see.
    # Fully-measured names (completeness 1.0) are unchanged; raw values are never fabricated (the
    # missing group ranks stay NaN / "Not estimated" -- this is a ranking convention, not imputation
    # of any metric value). `composite_raw` is kept alongside for full transparency.
    group_cols = list(FACTOR_GROUPS.keys())
    out["completeness"] = out[group_cols].notna().sum(axis=1) / float(len(group_cols))
    out["composite_raw"] = out[group_cols].mean(axis=1)            # mean of available groups
    out["composite"] = (out["completeness"] * out["composite_raw"]
                        + (1.0 - out["completeness"]) * 50.0)
    return out.sort_values("composite", ascending=False)
