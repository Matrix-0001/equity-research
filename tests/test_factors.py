"""Offline tests for the factor library (synthetic fixtures, no network).

Pins the math that matters: fundamental-metric ranking, the low-volatility
inversion, momentum direction, and missing-input handling (NaN, never a fake 0).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from equity_research import factors
from equity_research.config import Config
from equity_research.data import storage

AS_OF = dt.date(2026, 6, 15)


def _flat_prices(tickers: list[str], n: int = 300, price: float = 10.0) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-06-12", periods=n)
    return pd.DataFrame(
        [
            {"ticker": t, "date": d, "closeadj": price, "closeunadj": price, "volume": 1_000_000}
            for t in tickers
            for d in dates
        ]
    )


def _annual(rows: list[dict]) -> pd.DataFrame:
    """Build an annual (10-K) fundamentals frame; missing keys -> NaN column."""
    df = pd.DataFrame(rows)
    df["datekey"] = pd.Timestamp("2025-12-15")
    df["reportperiod"] = pd.Timestamp("2025-09-30")
    df["form"] = "10-K"
    return df


# --------------------------------------------------------------------------- #
# Fundamental-metric ranking + missing-input handling
# --------------------------------------------------------------------------- #
@pytest.fixture
def fundamental_conn(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    # T1..T5 strictly decreasing net income; T6 has net_income MISSING.
    fund = _annual([
        {"ticker": "T1", "net_income": 1.0e9, "total_equity": 1e9, "gross_profit": 5.0e9, "total_assets": 1e10, "shares_outstanding": 1e9},
        {"ticker": "T2", "net_income": 0.8e9, "total_equity": 1e9, "gross_profit": 4.0e9, "total_assets": 1e10, "shares_outstanding": 1e9},
        {"ticker": "T3", "net_income": 0.6e9, "total_equity": 1e9, "gross_profit": 3.0e9, "total_assets": 1e10, "shares_outstanding": 1e9},
        {"ticker": "T4", "net_income": 0.4e9, "total_equity": 1e9, "gross_profit": 2.0e9, "total_assets": 1e10, "shares_outstanding": 1e9},
        {"ticker": "T5", "net_income": 0.2e9, "total_equity": 1e9, "gross_profit": 1.0e9, "total_assets": 1e10, "shares_outstanding": 1e9},
        {"ticker": "T6", "net_income": np.nan, "total_equity": 1e9, "gross_profit": 2.5e9, "total_assets": 1e10, "shares_outstanding": 1e9},
    ])
    storage.load_table(c, fund, "fundamentals")
    storage.load_table(c, _flat_prices(["T1", "T2", "T3", "T4", "T5", "T6"]), "prices")
    yield c
    c.close()


def test_earnings_yield_rank_tracks_net_income(fundamental_conn):
    cfg = Config(data_dir="/unused")
    raw = factors.compute_raw_metrics(fundamental_conn, AS_OF, cfg)
    ranks = factors.percentile_ranks(raw)
    # Highest net income -> highest earnings-yield percentile; lowest -> lowest.
    assert ranks.loc["T1", "earnings_yield"] == pytest.approx(100.0)
    assert ranks.loc["T5", "earnings_yield"] == pytest.approx(20.0)
    assert raw.loc["T1", "earnings_yield"] > raw.loc["T5", "earnings_yield"]


def test_missing_input_is_nan_not_zero(fundamental_conn):
    cfg = Config(data_dir="/unused")
    raw = factors.compute_raw_metrics(fundamental_conn, AS_OF, cfg)
    ranks = factors.percentile_ranks(raw)
    # T6 has no net income -> earnings yield / ROE absent (NaN), never a fabricated 0.
    assert pd.isna(raw.loc["T6", "earnings_yield"])
    assert pd.isna(ranks.loc["T6", "earnings_yield"])
    prof = factors.factor_profile(fundamental_conn, AS_OF, cfg)
    assert prof.loc["T6", "n_metrics"] < prof.loc["T1", "n_metrics"] == 7


def test_composite_orders_universe(fundamental_conn):
    cfg = Config(data_dir="/unused")
    prof = factors.factor_profile(fundamental_conn, AS_OF, cfg)
    assert prof.index[0] == "T1"  # dominates every fundamental metric
    assert prof.loc["T1", "composite"] > prof.loc["T5", "composite"]


# --------------------------------------------------------------------------- #
# Price factors: momentum direction + low-volatility inversion
# --------------------------------------------------------------------------- #
@pytest.fixture
def price_conn(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    n = 300
    dates = pd.bdate_range(end="2026-06-12", periods=n)
    series = {
        "UP_CALM": np.full(n, 1.001),                 # steady up, zero volatility
        "FLAT_MID": np.tile([1.01, 0.99], n // 2),    # flat, moderate volatility
        "DN_WILD": np.tile([1.04, 0.95], n // 2),     # down, high vol, stays above $1 floor
    }
    rows = []
    for t, factors_arr in series.items():
        price = 50.0
        for d, f in zip(dates, factors_arr):
            price *= f
            rows.append({"ticker": t, "date": d, "closeadj": price,
                         "closeunadj": price, "volume": 1_000_000})
    storage.load_table(c, pd.DataFrame(rows), "prices")
    # Identical fundamentals so only momentum / low-vol differentiate them.
    fund = _annual([
        {"ticker": t, "net_income": 1e8, "total_equity": 1e9, "gross_profit": 5e8,
         "total_assets": 1e10, "shares_outstanding": 1e9}
        for t in series
    ])
    storage.load_table(c, fund, "fundamentals")
    yield c
    c.close()


def test_momentum_direction(price_conn):
    cfg = Config(data_dir="/unused")
    raw = factors.compute_raw_metrics(price_conn, AS_OF, cfg)
    assert raw.loc["UP_CALM", "momentum_12_1"] > 0 > raw.loc["DN_WILD", "momentum_12_1"]
    ranks = factors.percentile_ranks(raw)
    assert ranks["momentum_12_1"].idxmax() == "UP_CALM"


def test_low_volatility_is_inverted(price_conn):
    cfg = Config(data_dir="/unused")
    raw = factors.compute_raw_metrics(price_conn, AS_OF, cfg)
    # Raw volatility: calm < wild ...
    assert raw.loc["UP_CALM", "volatility"] < raw.loc["DN_WILD", "volatility"]
    # ... but the calmest name gets the HIGHEST low-vol rank (inversion).
    prof = factors.factor_profile(price_conn, AS_OF, cfg)
    assert prof.loc["UP_CALM", "low_vol"] > prof.loc["DN_WILD", "low_vol"]
    assert prof.index[0] == "UP_CALM"  # best on both momentum and low-vol
