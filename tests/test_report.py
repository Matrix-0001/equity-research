"""Test the SecurityReport assembly offline (use_llm=False, synthetic fixtures)."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from equity_research import report
from equity_research.config import Config
from equity_research.data import storage
from equity_research.disclaimer import NOT_ESTIMATED, PERSISTENT_DISCLAIMER

AS_OF = dt.date(2025, 6, 15)


def _annual(ticker, reportperiod, datekey, **overrides):
    base = dict(
        ticker=ticker, reportperiod=pd.Timestamp(reportperiod),
        datekey=pd.Timestamp(datekey), form="10-K",
        net_income=3e8, total_equity=2e9, revenue=4e9, gross_profit=1.5e9,
        total_assets=6e9, operating_income=5e8, operating_cash_flow=6e8, capex=1e8,
        current_assets=2e9, current_liabilities=1e9, long_term_debt=1e9,
        total_liabilities=3e9, retained_earnings=1.5e9, receivables=4e8, ppe_net=2e9,
        depreciation=2e8, sga=5e8, shares_outstanding=1e8,
    )
    base.update(overrides)
    return base


def _flat_prices(specs):  # specs: {ticker: price}
    dates = pd.bdate_range(end="2025-06-12", periods=300)
    return pd.DataFrame([
        {"ticker": t, "date": d, "closeadj": px, "closeunadj": px, "volume": 1_000_000}
        for t, px in specs.items() for d in dates
    ])


def _conn(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    fund = pd.DataFrame([
        # AAA: this year improves on last year (drives Piotroski), shares 1e8.
        _annual("AAA", "2024-09-30", "2024-12-15"),
        _annual("AAA", "2023-09-30", "2023-12-15", net_income=2e8, total_equity=1.9e9,
                revenue=3.6e9, gross_profit=1.3e9, shares_outstanding=1.01e8),
        _annual("BBB", "2024-09-30", "2024-12-15", net_income=1.5e8, total_equity=1e9,
                revenue=5e9, shares_outstanding=5e7),
        _annual("BBB", "2023-09-30", "2023-12-15", net_income=1.4e8, total_equity=1e9,
                revenue=4.9e9, shares_outstanding=5e7),
        _annual("CCC", "2024-09-30", "2024-12-15", net_income=2.5e8, total_equity=3e9,
                revenue=2e9, shares_outstanding=1e8),
        _annual("CCC", "2023-09-30", "2023-12-15", net_income=2.4e8, total_equity=3e9,
                revenue=2e9, shares_outstanding=1e8),
    ])
    storage.load_table(c, fund, "fundamentals")
    storage.load_table(c, _flat_prices({"AAA": 50.0, "BBB": 20.0, "CCC": 10.0}), "prices")
    return c


def test_report_uses_company_name_from_securities(tmp_path):
    conn = _conn(tmp_path)
    storage.load_table(conn, pd.DataFrame([
        {"ticker": "AAA", "cik": 1, "company": "Alpha Industries Inc", "sic": 3571,
         "sic_description": "Computers", "sector": "Information Technology"},
    ]), "securities")
    rep = report.build_security_report(conn, AS_OF, "AAA", Config(data_dir=tmp_path), use_llm=False)
    conn.close()
    assert rep.company == "Alpha Industries Inc"   # real name, not the ticker fallback


def test_report_company_falls_back_to_ticker_without_securities(tmp_path):
    conn = _conn(tmp_path)
    rep = report.build_security_report(conn, AS_OF, "AAA", Config(data_dir=tmp_path), use_llm=False)
    conn.close()
    assert rep.company == "AAA"                     # no securities table -> ticker, never blank


def test_report_assembly_offline(tmp_path):
    conn = _conn(tmp_path)
    rep = report.build_security_report(conn, AS_OF, "AAA", Config(data_dir=tmp_path), use_llm=False)
    conn.close()

    assert rep.ticker == "AAA"
    assert "composite" in rep.factor_profile
    assert "reverse-DCF implies" in rep.valuation_read       # positive FCF -> solvable
    assert "F=" in rep.quality_risk_flags and "Z=" in rep.quality_risk_flags  # flags estimated
    assert rep.confidence_tier.startswith("High")            # 8/8 factors, 3/3 flags, DCF yes
    # Honest "Not estimated" fields:
    assert rep.historical_base_rate.startswith(NOT_ESTIMATED)
    assert rep.suggested_role_weight.startswith(NOT_ESTIMATED)
    # use_llm=False leaves the narrative unset:
    assert rep.bull_case == NOT_ESTIMATED
    # Render always carries the disclaimer:
    out = rep.render()
    assert PERSISTENT_DISCLAIMER in out
    assert rep.completeness_pct() > 0.0


def test_report_unknown_ticker_is_graceful(tmp_path):
    conn = _conn(tmp_path)
    rep = report.build_security_report(conn, AS_OF, "ZZZ", Config(data_dir=tmp_path), use_llm=False)
    conn.close()
    assert "Not in the loaded screening universe" in rep.factor_profile
    assert PERSISTENT_DISCLAIMER in rep.render()
