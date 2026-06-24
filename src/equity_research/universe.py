"""Universe construction.

Two distinct universes, never confused:

  backtest_universe(as_of)  -> every ticker with a price knowable on `as_of`,
                               INCLUDING delisted names. Used for all historical
                               research so results are survivorship-bias-free.

  screening_universe(as_of) -> the liquid, investable subset (size/liquidity/price
                               floors from config.UniverseFloor). Used for live
                               screens and portfolio construction only.

Market cap is computed point-in-time as shares_outstanding (latest as-reported
filing known on `as_of`) x last price known on `as_of`. This keeps the universe
source-agnostic: it works whether or not the price vendor ships a market-cap feed.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

from equity_research._cache import cache_get
from equity_research.config import Config, config
from equity_research.data import pit, storage


def backtest_universe(conn: duckdb.DuckDBPyConnection, as_of: dt.date) -> list[str]:
    """All tickers with a price on/before `as_of` (delisted included)."""
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM prices WHERE date <= ? ORDER BY ticker", [as_of]
    ).fetchall()
    return [r[0] for r in rows]


def survivorship_universe(conn: duckdb.DuckDBPyConnection, as_of: dt.date) -> list[str]:
    """Survivorship-free, point-in-time index membership as of `as_of` (from `index_membership`).

    Exact membership: a ticker is in the universe on `as_of` if `as_of` falls within ANY of its
    (start_date, end_date) windows (end_date NULL = still a member). Names that later left or
    delisted are INCLUDED for the past dates when they were members -- that is the whole point.
    Returns [] if the membership table has not been loaded (run `pull-index-history`).
    """
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'index_membership'"
    ).fetchone()
    if not exists:
        return []
    rows = conn.execute(
        """
        SELECT DISTINCT ticker FROM index_membership
        WHERE start_date <= ? AND (end_date IS NULL OR end_date >= ?)
        ORDER BY ticker
        """,
        [as_of, as_of],
    ).fetchall()
    return [r[0] for r in rows]


def screening_universe(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    cfg: Config = config,
    _cache: dict | None = None,
) -> pd.DataFrame:
    """Liquid investable universe as of `as_of`, with the columns used to filter it.

    Returns ticker + market_cap + avg_dollar_volume + last_price for names that pass
    every floor, so the filter is fully auditable (you can see why each name made it
    in, and inspect names just below a threshold). `_cache` (run-scoped, optional) memoizes
    the result by `as_of` + config so a backtest doesn't recompute it several times per rebalance.
    """
    return cache_get(
        _cache, ("screen", as_of.isoformat(), id(cfg)),
        lambda: _screening_universe(conn, as_of, cfg),
    )


def _screening_universe(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, cfg: Config
) -> pd.DataFrame:
    floor = cfg.universe_floor
    window = floor.avg_dollar_volume_window_days
    empty = pd.DataFrame(columns=["ticker", "market_cap", "avg_dollar_volume", "last_price"])

    # The liquid screen needs both prices and fundamentals; the survivorship (SimFin price-only)
    # lane may have no fundamentals table at all -> return empty rather than raising.
    if not (storage.table_exists(conn, "prices") and storage.table_exists(conn, "fundamentals")):
        return empty

    prices = pit.prices_asof(conn, as_of, lookback_days=window * 2)
    funds = pit.fundamentals_asof(conn, as_of)
    if prices.empty or funds.empty or "shares_outstanding" not in funds.columns:
        return empty

    prices = prices.sort_values(["ticker", "date"])
    recent = prices.groupby("ticker").tail(window).copy()
    recent["dollar_volume"] = recent["closeunadj"] * recent["volume"]
    adv = recent.groupby("ticker")["dollar_volume"].mean().rename("avg_dollar_volume")
    last_price = (
        prices.groupby("ticker").tail(1).set_index("ticker")["closeunadj"].rename("last_price")
    )
    shares = funds.set_index("ticker")["shares_outstanding"].rename("shares")

    df = pd.concat([shares, adv, last_price], axis=1, join="inner").reset_index(names="ticker")
    df = df.dropna(subset=["shares", "avg_dollar_volume", "last_price"])
    df["market_cap"] = df["shares"] * df["last_price"]

    passed = df[
        (df["market_cap"] >= floor.min_market_cap_usd)
        & (df["avg_dollar_volume"] >= floor.min_avg_dollar_volume_usd)
        & (df["last_price"] >= floor.min_price_usd)
    ].sort_values("market_cap", ascending=False)

    return passed[["ticker", "market_cap", "avg_dollar_volume", "last_price"]].reset_index(drop=True)
