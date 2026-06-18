"""Validate the point-in-time logic and universe filter against a real DuckDB,
using synthetic fixtures (test data, not analytical output — no vendor key needed).

These tests guard the two properties the whole platform rests on:
  1. No lookahead: fundamentals dated AFTER the as-of date are never returned.
  2. No survivorship bias: delisted names remain in the backtest universe.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from equity_research import universe
from equity_research.config import Config, UniverseFloor
from equity_research.data import pit, storage

AS_OF = dt.date(2012, 3, 31)


@pytest.fixture
def conn(tmp_path):
    cfg = Config(data_dir=tmp_path)
    c = storage.connect(cfg)

    # --- fundamentals: datekey = when the filing became public -----------------
    fundamentals = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA", "BBB", "CCC", "DDD"],
            "datekey": pd.to_datetime(
                ["2012-02-15", "2012-05-15", "2011-11-10", "2011-12-01", "2011-12-15"]
            ),
            "roe": [0.20, 0.30, 0.10, 0.05, 0.08],  # AAA 0.30 is FUTURE (filed 2012-05-15)
            "shares_outstanding": [100e6, 120e6, 100e6, 10e6, 500e6],
        }
    )
    storage.load_table(c, fundamentals, "fundamentals")

    # --- prices: 40 trading days ending pre-as-of -----------------------------
    dates = pd.bdate_range(end="2012-03-30", periods=40)
    specs = {  # ticker: (price, volume)  -> drives market cap and ADV
        "AAA": (50.0, 1_000_000),   # big & liquid                  -> PASS
        "BBB": (20.0, 250_000),     # mid & liquid                  -> PASS
        "CCC": (8.0, 200_000),      # mcap 8e7 < floor              -> FAIL (size)
        "DDD": (2.0, 10_000),       # ADV 2e4 < floor               -> FAIL (liquidity)
    }
    rows = [
        {"ticker": t, "date": d, "closeadj": px, "closeunadj": px, "volume": v}
        for t, (px, v) in specs.items()
        for d in dates
    ]
    # ZZZ delisted in 2011: rows only in the past, none in 2012.
    rows += [
        {"ticker": "ZZZ", "date": d, "closeadj": 5.0, "closeunadj": 5.0, "volume": 500_000}
        for d in pd.bdate_range(end="2011-06-30", periods=40)
    ]
    storage.load_table(c, pd.DataFrame(rows), "prices")
    yield c
    c.close()


def test_fundamentals_have_no_lookahead(conn):
    f = pit.fundamentals_asof(conn, AS_OF).set_index("ticker")
    # AAA's later (2012-05-15) filing must be invisible as of 2012-03-31.
    assert f.loc["AAA", "roe"] == 0.20
    assert f.loc["AAA", "datekey"] == pd.Timestamp("2012-02-15")
    assert (f["datekey"] <= pd.Timestamp(AS_OF)).all()


def test_backtest_universe_includes_delisted(conn):
    bt = universe.backtest_universe(conn, AS_OF)
    assert "ZZZ" in bt  # survivorship-free: delisted name still present
    assert {"AAA", "BBB", "CCC", "DDD"} <= set(bt)


def test_screening_universe_applies_floors(conn):
    cfg = Config(
        data_dir="/unused",
        universe_floor=UniverseFloor(
            min_market_cap_usd=300_000_000,
            min_avg_dollar_volume_usd=1_000_000,
            min_price_usd=1.0,
        ),
    )
    u = universe.screening_universe(conn, AS_OF, cfg=cfg)
    tickers = set(u["ticker"])
    assert tickers == {"AAA", "BBB"}      # liquid + large enough
    assert "CCC" not in tickers           # fails market-cap floor
    assert "DDD" not in tickers           # fails dollar-volume floor
    assert "ZZZ" not in tickers           # delisted: absent from investable set
    # Market cap is computed shares x price (AAA: 100e6 x 50 = 5e9).
    assert u.set_index("ticker").loc["AAA", "market_cap"] == pytest.approx(5e9)
