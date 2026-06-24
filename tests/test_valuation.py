"""Tests for valuation: reverse-DCF round-trip and relative multiples."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from equity_research import valuation
from equity_research.config import Config
from equity_research.data import storage

AS_OF = dt.date(2026, 6, 15)


def test_reverse_dcf_recovers_growth():
    # Build a market cap from a known growth, then check the solver recovers it.
    vcfg = valuation.ValuationConfig(discount_rates=(0.09,))
    mcap = valuation._two_stage_pv(100.0, 0.05, 0.09, vcfg.terminal_growth, vcfg.horizon_years)
    out = valuation.implied_growth(mcap, 100.0, vcfg)
    assert out is not None
    assert out["by_rate"][0.09] == pytest.approx(0.05, abs=1e-3)


def test_reverse_dcf_range_widens_with_discount_rate():
    out = valuation.implied_growth(2000.0, 100.0)  # default rates 8/9/10%
    # A higher required return implies the price bakes in more growth.
    assert out["by_rate"][0.10] > out["by_rate"][0.08]
    assert out["low"] <= out["high"]


def test_reverse_dcf_not_estimable_without_positive_fcf():
    assert valuation.implied_growth(2000.0, -50.0) is None
    assert valuation.implied_growth(None, 100.0) is None


@pytest.fixture
def conn(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    dates = pd.bdate_range(end="2026-06-12", periods=50)
    specs = {  # ticker: (price, net_income, equity, revenue) ; shares 1e9 -> mcap = price*1e9
        "A": (10.0, 5.0e8, 2.0e9, 1.0e10),   # pe 20, pb 5,  ps 1
        "B": (10.0, 1.0e9, 1.0e9, 5.0e9),    # pe 10, pb 10, ps 2
        "C": (10.0, 2.5e8, 5.0e9, 2.0e10),   # pe 40, pb 2,  ps 0.5
    }
    prices = pd.DataFrame([
        {"ticker": t, "date": d, "closeadj": px, "closeunadj": px, "volume": 1_000_000}
        for t, (px, *_rest) in specs.items() for d in dates
    ])
    storage.load_table(c, prices, "prices")
    fund = pd.DataFrame([
        {"ticker": t, "datekey": pd.Timestamp("2025-12-15"),
         "reportperiod": pd.Timestamp("2025-09-30"), "form": "10-K",
         "net_income": ni, "total_equity": eq, "revenue": rev, "shares_outstanding": 1e9}
        for t, (_px, ni, eq, rev) in specs.items()
    ])
    storage.load_table(c, fund, "fundamentals")
    yield c
    c.close()


def test_relative_multiples(conn):
    cfg = Config(data_dir="/unused")
    df, stats = valuation.relative_multiples(conn, AS_OF, cfg)
    assert df.loc["A", "pe"] == pytest.approx(20.0)   # 1e10 / 5e8
    assert df.loc["B", "pe"] == pytest.approx(10.0)
    assert df.loc["C", "pe"] == pytest.approx(40.0)
    assert stats["pe"]["median"] == pytest.approx(20.0)
    assert df.loc["C", "ps"] == pytest.approx(0.5)


def test_reverse_dcf_flags_material_leverage(tmp_path):
    # A firm with positive FCF but liabilities = 80% of assets: the implied-growth read must carry
    # the honesty caveat that the FCF-vs-equity proxy is especially loose under material leverage.
    c = storage.connect(Config(data_dir=tmp_path))
    dates = pd.bdate_range(end="2026-06-12", periods=30)
    px = pd.DataFrame([
        {"ticker": "LEV", "date": d, "closeadj": 10.0, "closeunadj": 10.0, "volume": 1_000_000}
        for d in dates
    ])
    storage.load_table(c, px, "prices")
    fund = pd.DataFrame([{
        "ticker": "LEV", "datekey": pd.Timestamp("2025-12-15"),
        "reportperiod": pd.Timestamp("2025-09-30"), "form": "10-K",
        "operating_cash_flow": 2.0e8, "capex": 5.0e7,           # fcf0 = 1.5e8 > 0
        "shares_outstanding": 1.0e8,                            # mcap = 10 * 1e8 = 1e9
        "total_assets": 1.0e10, "total_liabilities": 8.0e9,    # liab/assets = 0.8 > 0.6
    }])
    storage.load_table(c, fund, "fundamentals")
    try:
        res = valuation.valuation_for(c, AS_OF, "LEV", Config(data_dir=tmp_path))
    finally:
        c.close()
    assert res.implied_growth is not None
    assert "leverage" in res.note.lower()


def test_reverse_dcf_low_leverage_has_indicative_caveat_only(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    dates = pd.bdate_range(end="2026-06-12", periods=30)
    px = pd.DataFrame([
        {"ticker": "LOW", "date": d, "closeadj": 10.0, "closeunadj": 10.0, "volume": 1_000_000}
        for d in dates
    ])
    storage.load_table(c, px, "prices")
    fund = pd.DataFrame([{
        "ticker": "LOW", "datekey": pd.Timestamp("2025-12-15"),
        "reportperiod": pd.Timestamp("2025-09-30"), "form": "10-K",
        "operating_cash_flow": 2.0e8, "capex": 5.0e7, "shares_outstanding": 1.0e8,
        "total_assets": 1.0e10, "total_liabilities": 2.0e9,    # liab/assets = 0.2 (low)
    }])
    storage.load_table(c, fund, "fundamentals")
    try:
        res = valuation.valuation_for(c, AS_OF, "LOW", Config(data_dir=tmp_path))
    finally:
        c.close()
    assert res.implied_growth is not None
    assert "indicative" in res.note.lower()
    assert "leverage" not in res.note.lower()      # not flagged when leverage is modest
