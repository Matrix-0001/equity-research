"""Tests for accounting-quality flags, with hand-computed known answers."""

from __future__ import annotations

import pandas as pd
import pytest

from equity_research import flags

# A clean two-year company where year t improves on t-1 across most dimensions.
PREV = pd.Series({
    "net_income": 100, "total_assets": 1000, "operating_cash_flow": 120,
    "long_term_debt": 300, "current_assets": 400, "current_liabilities": 200,
    "shares_outstanding": 1000, "gross_profit": 300, "revenue": 1000,
    "retained_earnings": 200, "operating_income": 150, "total_liabilities": 500,
    "receivables": 100, "ppe_net": 400, "depreciation": 40, "sga": 120,
})
CURR = pd.Series({
    "net_income": 150, "total_assets": 1100, "operating_cash_flow": 200,
    "long_term_debt": 250, "current_assets": 500, "current_liabilities": 200,
    "shares_outstanding": 1000, "gross_profit": 400, "revenue": 1100,
    "retained_earnings": 320, "operating_income": 210, "total_liabilities": 480,
    "receivables": 110, "ppe_net": 420, "depreciation": 45, "sga": 121,
})


def test_piotroski_known_score():
    res = flags.piotroski_f(CURR, PREV)
    # 8 of 9 criteria pass; asset turnover is flat (1.0 vs 1.0) -> the only fail.
    assert res.score == 8.0
    assert res.components["asset_turnover_up"] is False
    assert "strong" in res.interpretation


def test_altman_known_score():
    res = flags.altman_z(CURR, market_cap=2000.0)
    assert res.score == pytest.approx(4.86, abs=0.02)
    assert "safe" in res.interpretation


def test_beneish_known_score():
    res = flags.beneish_m(CURR, PREV)
    assert res.score == pytest.approx(-2.70, abs=0.03)
    assert "below threshold" in res.interpretation


def test_missing_prior_year_is_not_estimated():
    res = flags.piotroski_f(CURR, None)
    assert res.score is None
    assert res.interpretation == "Not estimated"
    assert "prior-year annual filing" in res.missing


def test_altman_missing_input_lists_it():
    curr_no_re = CURR.drop("retained_earnings")
    res = flags.altman_z(curr_no_re, market_cap=2000.0)
    assert res.score is None
    assert "retained_earnings/TA" in res.missing


def test_beneish_missing_input_is_not_estimated():
    curr_no_recv = CURR.drop("receivables")
    res = flags.beneish_m(curr_no_recv, PREV)
    assert res.score is None
    assert "DSRI" in res.missing
