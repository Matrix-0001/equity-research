"""Offline tests for the v3 performance metrics (pure, hand-computed values)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from equity_research import metrics


def test_nav_curve_includes_starting_point():
    nav = metrics.nav_curve([0.1, -0.5, 0.2], initial=1.0)
    assert nav == pytest.approx([1.0, 1.1, 0.55, 0.66])


def test_total_return_hand_computed():
    assert metrics.total_return([0.1, -0.5, 0.2]) == pytest.approx(0.66 - 1.0)
    assert math.isnan(metrics.total_return([]))


def test_cagr_one_year_of_monthly_returns():
    # twelve +1% months, ppy=12 -> exactly one year -> 1.01^12 - 1.
    assert metrics.cagr([0.01] * 12, 12) == pytest.approx(1.01 ** 12 - 1.0)


def test_cagr_total_wipeout_returns_minus_one():
    assert metrics.cagr([-1.0, 0.2], 12) == -1.0


def test_annualized_volatility_hand_computed():
    # +/-2% alternating: sample sd = sqrt(4*0.02^2/3); * sqrt(12) = 0.08 exactly.
    assert metrics.annualized_volatility([0.02, -0.02, 0.02, -0.02], 12) == pytest.approx(0.08)
    assert math.isnan(metrics.annualized_volatility([0.01], 12))  # need >= 2 obs


def test_sharpe_and_standard_error_hand_computed():
    # returns 1/2/3%: mean 0.02, sd 0.01 -> SR_period 2.0; annualized 2*sqrt(12).
    sr, se = metrics.sharpe([0.01, 0.02, 0.03], 12, rf_per_period=0.0)
    assert sr == pytest.approx(2.0 * math.sqrt(12))
    # SE_period = sqrt((1 + 0.5*2^2)/3) = sqrt(1) = 1.0; annualized sqrt(12).
    assert se == pytest.approx(math.sqrt(12))


def test_sharpe_zero_mean_is_zero_with_finite_se():
    sr, se = metrics.sharpe([0.02, -0.02, 0.02, -0.02], 12)
    assert sr == pytest.approx(0.0)
    assert se == pytest.approx(0.5 * math.sqrt(12))  # SR_period 0 -> SE_period sqrt(1/4)=0.5


def test_sharpe_zero_vol_is_not_estimated():
    sr, se = metrics.sharpe([0.01, 0.01, 0.01], 12)
    assert math.isnan(sr) and math.isnan(se)


def test_sortino_hand_computed():
    # ann excess = mean(0.005)*12 = 0.06; downside dev = sqrt(mean[0,.01^2,0,.02^2])*sqrt(12).
    r = [0.02, -0.01, 0.03, -0.02]
    dd = math.sqrt((0.01 ** 2 + 0.02 ** 2) / 4) * math.sqrt(12)
    assert metrics.sortino(r, 12) == pytest.approx(0.06 / dd)


def test_max_drawdown_hand_computed():
    # NAV 1 -> 1.1 -> 0.55 -> 0.66; worst is 0.55/1.1 - 1 = -0.5.
    assert metrics.max_drawdown([0.1, -0.5, 0.2]) == pytest.approx(-0.5)
    # A monotonically rising path has no drawdown.
    assert metrics.max_drawdown([0.1, 0.1, 0.1]) == pytest.approx(0.0)


def test_hit_rate_counts_strictly_positive():
    assert metrics.hit_rate([0.01, -0.02, 0.0, 0.03]) == pytest.approx(0.5)
    assert math.isnan(metrics.hit_rate([]))


def test_metrics_drop_nonfinite_inputs():
    # NaN/inf are dropped before computation (no fabricated handling of bad inputs).
    assert metrics.total_return([0.1, np.nan, 0.1]) == pytest.approx(1.1 * 1.1 - 1.0)
