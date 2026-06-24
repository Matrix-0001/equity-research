"""Offline tests for the v3 performance metrics (pure, hand-computed values)."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
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


# --------------------------------------------------------------------------- #
# Phase C - higher moments
# --------------------------------------------------------------------------- #
def test_skewness_symmetric_is_zero():
    assert metrics.skewness([-2.0, -1.0, 0.0, 1.0, 2.0]) == pytest.approx(0.0)
    assert math.isnan(metrics.skewness([0.1, 0.2]))         # need >= 3 obs


def test_kurtosis_hand_computed():
    # [-2,-1,0,1,2]: mean 0, m2 = 2, m4 = 34/5 = 6.8 -> kurtosis = 6.8/4 = 1.7 (platykurtic).
    assert metrics.kurtosis([-2.0, -1.0, 0.0, 1.0, 2.0]) == pytest.approx(1.7)
    assert math.isnan(metrics.kurtosis([0.1, 0.2, 0.3]))    # need >= 4 obs


# --------------------------------------------------------------------------- #
# Phase C - normal CDF / inverse CDF (self-contained, no scipy)
# --------------------------------------------------------------------------- #
def test_norm_cdf_known_values():
    assert metrics._norm_cdf(0.0) == pytest.approx(0.5)
    assert metrics._norm_cdf(1.959963985) == pytest.approx(0.975, abs=1e-6)
    assert metrics._norm_cdf(-1.959963985) == pytest.approx(0.025, abs=1e-6)


def test_norm_ppf_known_values_and_roundtrip():
    assert metrics._norm_ppf(0.5) == pytest.approx(0.0, abs=1e-12)
    assert metrics._norm_ppf(0.975) == pytest.approx(1.959963985, abs=1e-7)
    # Phi(Phi^-1(p)) == p across the range (exercises both Acklam branches + Halley step).
    for p in (0.001, 0.02, 0.3, 0.83, 0.999):
        assert metrics._norm_cdf(metrics._norm_ppf(p)) == pytest.approx(p, abs=1e-10)


# --------------------------------------------------------------------------- #
# Phase C - Probabilistic & Deflated Sharpe Ratio
# --------------------------------------------------------------------------- #
def test_psr_zero_sharpe_is_one_half():
    # SR == benchmark -> z = 0 -> PSR = 0.5 regardless of n / moments.
    assert metrics.probabilistic_sharpe_ratio(0.0, 100, 0.0, 3.0) == pytest.approx(0.5)


def test_psr_matches_formula_for_normal_returns():
    # skew 0, kurt 3 -> denom_var = 1 + 0.5*SR^2; z = SR*sqrt(n-1)/sqrt(denom_var).
    sr, n = 0.2, 101
    z = sr * math.sqrt(n - 1) / math.sqrt(1.0 + 0.5 * sr ** 2)
    assert metrics.probabilistic_sharpe_ratio(sr, n, 0.0, 3.0) == pytest.approx(metrics._norm_cdf(z))


def test_psr_nonpositive_variance_term_is_not_estimated():
    # A large positive skew with a big SR drives 1 - g3*SR + (g4-1)/4*SR^2 <= 0
    # (1 - 2*3 + 0.5*9 = -0.5) -> refuse, not fabricate.
    assert math.isnan(metrics.probabilistic_sharpe_ratio(3.0, 50, 2.0, 3.0))


def test_expected_max_sharpe_no_multiple_testing_is_zero():
    assert metrics.expected_max_sharpe(1, 0.25) == 0.0       # one trial -> nothing to deflate
    assert metrics.expected_max_sharpe(10, 0.0) == 0.0       # zero dispersion -> no deflation


def test_expected_max_sharpe_matches_published_formula():
    # SR_0 = sqrt(V)[(1-g)Z^-1(1-1/N) + g Z^-1(1-1/(Ne))]; N=2, V=1.
    g = metrics.EULER_MASCHERONI
    z1 = metrics._norm_ppf(1.0 - 1.0 / 2.0)
    z2 = metrics._norm_ppf(1.0 - 1.0 / (2.0 * math.e))
    expected = (1.0 - g) * z1 + g * z2
    assert metrics.expected_max_sharpe(2, 1.0) == pytest.approx(expected)
    assert expected == pytest.approx(0.5198, abs=2e-3)       # independent hand-check of the value


def test_dsr_single_trial_reduces_to_psr():
    # n_trials = 1 -> SR_0 = 0 -> DSR is exactly PSR at threshold 0.
    sr, n = 0.25, 60
    dsr = metrics.deflated_sharpe_ratio(sr, n, 1, 0.0, 3.0)
    assert dsr == pytest.approx(metrics.probabilistic_sharpe_ratio(sr, n, 0.0, 3.0))


def test_dsr_deflates_with_more_trials():
    # Same observed Sharpe, more trials -> a higher deflation benchmark -> a LOWER DSR.
    sr, n, V = 0.25, 60, 0.04
    few = metrics.deflated_sharpe_ratio(sr, n, 2, 0.0, 3.0, sr_variance=V)
    many = metrics.deflated_sharpe_ratio(sr, n, 50, 0.0, 3.0, sr_variance=V)
    assert few > many
    # And the assembly matches PSR evaluated at the deflated benchmark exactly.
    sr0 = metrics.expected_max_sharpe(50, V)
    assert many == pytest.approx(metrics.probabilistic_sharpe_ratio(sr, n, 0.0, 3.0, benchmark_sr=sr0))


def test_dsr_default_variance_is_null_sampling_variance():
    # The 5-arg form falls back to V = 1/(n-1).
    sr, n, N = 0.25, 60, 5
    explicit = metrics.deflated_sharpe_ratio(sr, n, N, 0.0, 3.0, sr_variance=1.0 / (n - 1))
    default = metrics.deflated_sharpe_ratio(sr, n, N, 0.0, 3.0)
    assert default == pytest.approx(explicit)


# --------------------------------------------------------------------------- #
# Phase C - multi-regime stress tagging
# --------------------------------------------------------------------------- #
def test_regime_of_tags_each_crisis_window():
    assert metrics.regime_of(dt.date(2001, 1, 1)) == "dot-com crash"
    assert metrics.regime_of(dt.date(2008, 3, 15)) == "GFC"
    assert metrics.regime_of(dt.date(2020, 3, 23)) == "COVID crash"
    assert metrics.regime_of(dt.date(2022, 6, 1)) == "2022 drawdown"
    assert metrics.regime_of(dt.date(2015, 7, 4)) == "normal"


def test_regime_of_boundaries_inclusive():
    assert metrics.regime_of(dt.date(2007, 10, 1)) == "GFC"       # inclusive lower bound
    assert metrics.regime_of(dt.date(2009, 6, 30)) == "GFC"       # inclusive upper bound
    assert metrics.regime_of(dt.date(2009, 7, 1)) == "normal"     # one day past -> normal


def test_regime_of_accepts_pandas_timestamp():
    assert metrics.regime_of(pd.Timestamp("2008-03-15")) == "GFC"


def test_regime_breakdown_groups_and_computes():
    dates = [dt.date(2008, 1, 31), dt.date(2008, 2, 29), dt.date(2015, 1, 31)]
    rets = [0.1, -0.5, 0.2]
    bd = metrics.regime_breakdown(dates, rets, periods_per_year=12)
    assert bd["GFC"]["n"] == 2
    assert bd["GFC"]["total_return"] == pytest.approx(1.1 * 0.5 - 1.0)   # -0.45
    assert bd["GFC"]["max_drawdown"] == pytest.approx(-0.5)              # 0.55/1.1 - 1
    assert bd["normal"]["n"] == 1
    assert bd["normal"]["total_return"] == pytest.approx(0.2)
    assert "COVID crash" not in bd            # a regime with no periods is absent (not fabricated)
