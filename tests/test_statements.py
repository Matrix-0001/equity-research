"""Tests for the as-reported financial-statement assembly (`statements.py`).

Synthetic in-memory `fundamentals` fixture with hand-known values; no network. Pins the honest
behaviours: most-recent-first periods, USD-millions formatting, derived free cash flow, a missing
XBRL tag rendering "Not estimated" (never a fabricated 0), and point-in-time filtering on datekey.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from equity_research import statements
from equity_research.disclaimer import NOT_ESTIMATED
from equity_research.statements import financial_statements, fmt_value


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    # Deliberately OMIT gross_profit / current_assets columns to exercise the "Not estimated" path.
    c.execute(
        """
        CREATE TABLE fundamentals (
            ticker VARCHAR, datekey DATE, reportperiod DATE, form VARCHAR,
            revenue DOUBLE, net_income DOUBLE, eps_diluted DOUBLE,
            total_assets DOUBLE, total_equity DOUBLE,
            operating_cash_flow DOUBLE, capex DOUBLE
        )
        """
    )
    rows = [
        ("AAA", "2023-11-01", "2023-09-30", "10-K", 100e9, 25e9, 6.00, 300e9, 150e9, 40e9, 10e9),
        ("AAA", "2024-11-01", "2024-09-30", "10-K", 120e9, 30e9, 6.50, 320e9, 160e9, 50e9, 12e9),
        # A future filing that must NOT be visible as of 2025-01-01 (point-in-time discipline).
        ("AAA", "2025-11-01", "2025-09-30", "10-K", 150e9, 35e9, 7.00, 350e9, 170e9, 60e9, 14e9),
    ]
    c.executemany("INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    yield c
    c.close()


def _row(fs, section_name, label):
    rows = dict((lbl, (unit, vals)) for name, sec in fs.sections if name == section_name
                for lbl, unit, vals in sec)
    return rows[label]


def test_periods_most_recent_first_and_point_in_time(conn):
    fs = financial_statements(conn, dt.date(2025, 1, 1), "AAA", n_years=4)
    # The 2025 filing is in the future as of 2025-01-01 -> excluded; 2024 then 2023, newest first.
    assert fs.periods == [dt.date(2024, 9, 30), dt.date(2023, 9, 30)]
    assert fs.forms == ["10-K", "10-K"]


def test_known_values_and_usd_millions(conn):
    fs = financial_statements(conn, dt.date(2025, 1, 1), "AAA")
    unit, vals = _row(fs, "Income statement", "Revenue")
    assert unit == "usd" and vals == [120e9, 100e9]
    assert fmt_value(vals[0], unit) == "120,000"          # $120,000 million = $120B
    eps_unit, eps = _row(fs, "Income statement", "Diluted EPS")
    assert eps_unit == "per_share" and fmt_value(eps[0], eps_unit) == "6.50"


def test_missing_tag_reads_not_estimated_never_zero(conn):
    fs = financial_statements(conn, dt.date(2025, 1, 1), "AAA")
    _unit, vals = _row(fs, "Income statement", "Gross profit")   # column absent in the fixture
    assert vals == [None, None]
    assert fmt_value(vals[0], "usd") == NOT_ESTIMATED           # NOT "0"


def test_free_cash_flow_is_derived(conn):
    fs = financial_statements(conn, dt.date(2025, 1, 1), "AAA")
    _unit, fcf = _row(fs, "Cash flow", "Free cash flow (OCF - capex)")
    assert fcf == [50e9 - 12e9, 40e9 - 10e9]                    # 38B, 30B


def test_render_carries_disclaimer_and_not_estimated(conn):
    text = financial_statements(conn, dt.date(2025, 1, 1), "AAA").render()
    assert "Income statement" in text and "Balance sheet" in text and "Cash flow" in text
    assert NOT_ESTIMATED in text
    assert "Not investment advice" in text


def test_empty_when_no_filings(conn):
    fs = financial_statements(conn, dt.date(2025, 1, 1), "ZZZ")
    assert fs.is_empty()
    assert "No annual filings" in fs.render()


def test_fmt_value_edges():
    assert fmt_value(None, "usd") == NOT_ESTIMATED
    assert fmt_value(0.0, "usd") == "0"                         # a real reported 0 is shown, not hidden
    assert fmt_value(-1.5e9, "usd") == "-1,500"                 # losses keep their sign
    assert fmt_value(2.345, "per_share") == "2.35"
