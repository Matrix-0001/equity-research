"""Performance metrics for the v3 backtester (pure functions over a return series).

Every function takes a sequence of **per-period returns** and is hand-checkable: feed a
synthetic series, get a value you can compute by hand. The engine feeds these its NET
(post-cost) returns for the headline numbers and its GROSS (pre-cost) returns alongside, so
no metric can quietly report a pre-cost figure on its own (non-negotiable #2).

Honesty posture:
  - A backtest is ONE realized path; a point Sharpe is not "the" Sharpe. `sharpe()` therefore
    also returns an analytic standard error (Lo, 2002) so the caller can report a RANGE rather
    than a hero number (non-negotiable #3). Full multiple-testing correction (the Deflated
    Sharpe Ratio) is a Phase C deliverable; this SE is the honest minimum for a single run.
  - Any metric not estimable from the given series returns `float('nan')`, which the caller
    renders as "Not estimated" -- never a fabricated value.

`periods_per_year` annualizes: 12 for monthly rebalancing, 4 for quarterly.
"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np

NAN = float("nan")
EULER_MASCHERONI = 0.5772156649015329  # gamma, for the expected-maximum-Sharpe deflation term


def _clean(returns) -> np.ndarray:
    """Return a 1-D float array of finite per-period returns (drops NaN/inf)."""
    arr = np.asarray(list(returns), dtype="float64")
    return arr[np.isfinite(arr)]


def nav_curve(returns, initial: float = 1.0) -> np.ndarray:
    """Compounded NAV path including the starting point: [initial, initial*(1+r0), ...]."""
    arr = _clean(returns)
    return initial * np.concatenate([[1.0], np.cumprod(1.0 + arr)])


def total_return(returns) -> float:
    """Cumulative return over the whole series: prod(1 + r) - 1."""
    arr = _clean(returns)
    if arr.size == 0:
        return NAN
    return float(np.prod(1.0 + arr) - 1.0)


def cagr(returns, periods_per_year: int) -> float:
    """Compound annual growth rate from the period returns (years = n_periods / periods_per_year)."""
    arr = _clean(returns)
    if arr.size == 0:
        return NAN
    growth = float(np.prod(1.0 + arr))
    if growth <= 0.0:  # a -100% path has no real annualized rate
        return -1.0
    years = arr.size / float(periods_per_year)
    return growth ** (1.0 / years) - 1.0


def annualized_volatility(returns, periods_per_year: int) -> float:
    """Annualized standard deviation of period returns (sample sd, ddof=1)."""
    arr = _clean(returns)
    if arr.size < 2:
        return NAN
    return float(np.std(arr, ddof=1) * math.sqrt(periods_per_year))


def downside_deviation(returns, periods_per_year: int, mar_per_period: float = 0.0) -> float:
    """Annualized downside deviation below `mar_per_period` (the Sortino denominator)."""
    arr = _clean(returns)
    if arr.size < 2:
        return NAN
    shortfall = np.minimum(arr - mar_per_period, 0.0)
    return float(math.sqrt(np.mean(shortfall ** 2)) * math.sqrt(periods_per_year))


def sharpe(returns, periods_per_year: int, rf_per_period: float = 0.0) -> tuple[float, float]:
    """Annualized (Sharpe ratio, its standard error).

    SR = mean(excess) / sd(returns) * sqrt(ppy), excess = returns - rf_per_period.
    SE follows Lo (2002) for IID returns: SE(SR_period) = sqrt((1 + 0.5 SR_period^2)/n), scaled
    to annual by sqrt(ppy). The SE lets the caller present SR as a range, not a point estimate.
    """
    arr = _clean(returns)
    if arr.size < 2:
        return NAN, NAN
    sd = float(np.std(arr, ddof=1))
    if sd == 0.0:
        return NAN, NAN
    sr_period = float(np.mean(arr - rf_per_period)) / sd
    n = arr.size
    se_period = math.sqrt((1.0 + 0.5 * sr_period ** 2) / n)
    scale = math.sqrt(periods_per_year)
    return sr_period * scale, se_period * scale


def sortino(returns, periods_per_year: int, rf_per_period: float = 0.0) -> float:
    """Annualized Sortino ratio: annualized mean excess / annualized downside deviation."""
    arr = _clean(returns)
    if arr.size < 2:
        return NAN
    dd = downside_deviation(arr, periods_per_year, mar_per_period=rf_per_period)
    if not math.isfinite(dd) or dd == 0.0:
        return NAN
    ann_excess = float(np.mean(arr - rf_per_period)) * periods_per_year
    return ann_excess / dd


def max_drawdown(returns) -> float:
    """Worst peak-to-trough decline of the compounded NAV, as a NEGATIVE fraction (0.0 if none)."""
    arr = _clean(returns)
    if arr.size == 0:
        return NAN
    nav = nav_curve(arr)
    running_peak = np.maximum.accumulate(nav)
    drawdowns = nav / running_peak - 1.0
    return float(drawdowns.min())


def hit_rate(returns) -> float:
    """Fraction of periods with a strictly positive return."""
    arr = _clean(returns)
    if arr.size == 0:
        return NAN
    return float(np.mean(arr > 0.0))


# --------------------------------------------------------------------------- #
# Phase C - higher moments of the return series (for the Deflated Sharpe Ratio)
# --------------------------------------------------------------------------- #
def skewness(returns) -> float:
    """Sample skewness (Fisher-Pearson moment estimator). 0.0 for a symmetric series."""
    arr = _clean(returns)
    if arr.size < 3:
        return NAN
    mu = float(arr.mean())
    m2 = float(np.mean((arr - mu) ** 2))
    m3 = float(np.mean((arr - mu) ** 3))
    if m2 <= 0.0:
        return NAN
    return m3 / m2 ** 1.5


def kurtosis(returns) -> float:
    """REGULAR (non-excess) kurtosis; a normal distribution gives 3.0 (subtract 3 for 'excess')."""
    arr = _clean(returns)
    if arr.size < 4:
        return NAN
    mu = float(arr.mean())
    m2 = float(np.mean((arr - mu) ** 2))
    m4 = float(np.mean((arr - mu) ** 4))
    if m2 <= 0.0:
        return NAN
    return m4 / m2 ** 2


# --------------------------------------------------------------------------- #
# Phase C - normal CDF / inverse CDF (self-contained; the venv has no scipy)
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    """Standard-normal CDF Phi(x) via erfc -- exact to floating-point precision."""
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


# Acklam's rational approximation for the inverse normal CDF (|abs err| < 1.2e-9), then one
# Halley step to machine precision. Used only for the small N-trial deflation term below.
_ACK_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
          1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_ACK_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
          6.680131188771972e+01, -1.328068155288572e+01)
_ACK_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
          -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_ACK_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
          3.754408661907416e+00)


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF Phi^-1(p) for p in (0, 1); +/-inf at the bounds, NaN outside."""
    if not math.isfinite(p):
        return NAN
    if p <= 0.0:
        return -math.inf if p == 0.0 else NAN
    if p >= 1.0:
        return math.inf if p == 1.0 else NAN
    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = ((((((_ACK_C[0]*q + _ACK_C[1])*q + _ACK_C[2])*q + _ACK_C[3])*q + _ACK_C[4])*q
              + _ACK_C[5]) / ((((_ACK_D[0]*q + _ACK_D[1])*q + _ACK_D[2])*q + _ACK_D[3])*q + 1.0))
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = ((((((_ACK_A[0]*r + _ACK_A[1])*r + _ACK_A[2])*r + _ACK_A[3])*r + _ACK_A[4])*r
              + _ACK_A[5]) * q
             / (((((_ACK_B[0]*r + _ACK_B[1])*r + _ACK_B[2])*r + _ACK_B[3])*r + _ACK_B[4])*r + 1.0))
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -((((((_ACK_C[0]*q + _ACK_C[1])*q + _ACK_C[2])*q + _ACK_C[3])*q + _ACK_C[4])*q
               + _ACK_C[5]) / ((((_ACK_D[0]*q + _ACK_D[1])*q + _ACK_D[2])*q + _ACK_D[3])*q + 1.0))
    # One Halley refinement: x <- x - e/(phi(x)(1 + x e / 2)), e = Phi(x) - p.
    e = _norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    return x - u / (1.0 + x * u / 2.0)


# --------------------------------------------------------------------------- #
# Phase C - Probabilistic & Deflated Sharpe Ratio (Bailey & Lopez de Prado)
# --------------------------------------------------------------------------- #
# All three take PER-PERIOD (non-annualized) Sharpe ratios. To go from the annualized SR that
# `sharpe()` reports to the per-period SR these expect, divide by sqrt(periods_per_year).
def probabilistic_sharpe_ratio(
    observed_sr: float, n_obs: int, skew: float, kurt: float, benchmark_sr: float = 0.0
) -> float:
    """P(true per-period SR > benchmark_sr) for an observed per-period SR over `n_obs` periods.

    Bailey & Lopez de Prado (2012):
        PSR = Phi[ (SR - SR*) * sqrt(n - 1) / sqrt(1 - g3*SR + (g4 - 1)/4 * SR^2) ]
    where g3 = `skew`, g4 = `kurt` (REGULAR kurtosis, normal = 3). Corrects the Sharpe for sample
    length and non-normality. Returns NaN if undefined (n < 2 or a non-positive variance term).
    """
    if n_obs is None or n_obs < 2:
        return NAN
    if not all(math.isfinite(v) for v in (observed_sr, skew, kurt, benchmark_sr)):
        return NAN
    denom_var = 1.0 - skew * observed_sr + (kurt - 1.0) / 4.0 * observed_sr ** 2
    if denom_var <= 0.0:
        return NAN
    z = (observed_sr - benchmark_sr) * math.sqrt(n_obs - 1) / math.sqrt(denom_var)
    return _norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected MAXIMUM per-period Sharpe under the null (zero skill) across `n_trials` attempts.

        SR_0 = sqrt(V) * [ (1 - gamma) * Z^-1(1 - 1/N) + gamma * Z^-1(1 - 1/(N e)) ]
    `sr_variance` (V) is the variance of the per-period Sharpe ESTIMATES across the N trials. This
    is the multiple-testing 'deflation' benchmark: the Sharpe you'd expect from the luckiest of N
    coin-flippers. With N <= 1 there is no multiple testing, so SR_0 = 0 (nothing to deflate).
    """
    if n_trials is None or n_trials <= 1:
        return 0.0
    if not math.isfinite(sr_variance) or sr_variance < 0.0:
        return NAN
    if sr_variance == 0.0:
        return 0.0
    n = float(n_trials)
    z1 = _norm_ppf(1.0 - 1.0 / n)
    z2 = _norm_ppf(1.0 - 1.0 / (n * math.e))
    return math.sqrt(sr_variance) * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)


def deflated_sharpe_ratio(
    observed_sr: float, n_obs: int, n_trials: int, skew: float, kurt: float,
    sr_variance: float | None = None,
) -> float:
    """Deflated Sharpe Ratio: P(true SR > the multiple-testing-deflated benchmark SR_0).

    `observed_sr` is PER-PERIOD. The benchmark SR_0 = `expected_max_sharpe(n_trials, sr_variance)`,
    so a Sharpe that is merely the best of many tries is correctly discounted. `sr_variance` is the
    variance of the per-period Sharpe estimates across the trials; when None it falls back to the
    null sampling variance 1/(n_obs - 1) of a single zero-skill Sharpe estimate, so the documented
    5-argument form is fully determined. Combines all three corrections demanded by non-negotiable
    #5: multiple testing (SR_0), non-normality (skew/kurt), and sample length (n). NaN if undefined.
    """
    if n_obs is None or n_obs < 2:
        return NAN
    if sr_variance is None:
        sr_variance = 1.0 / (n_obs - 1)
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    if not math.isfinite(sr0):
        return NAN
    return probabilistic_sharpe_ratio(observed_sr, n_obs, skew, kurt, benchmark_sr=sr0)


# --------------------------------------------------------------------------- #
# Phase C - multi-regime stress tagging
# --------------------------------------------------------------------------- #
# Crisis windows (inclusive). Approximate but defensible peak-to-recovery spans for the four
# regimes the spec names; everything outside them is "normal". A run can only speak to a regime
# its [start, end] window + data lane actually reach -- the caller reports "Not estimated" for the
# rest rather than fabricating coverage (non-negotiable #1).
REGIME_WINDOWS: tuple[tuple[str, dt.date, dt.date], ...] = (
    ("dot-com crash", dt.date(2000, 3, 1), dt.date(2002, 10, 31)),
    ("GFC", dt.date(2007, 10, 1), dt.date(2009, 6, 30)),
    ("COVID crash", dt.date(2020, 2, 1), dt.date(2020, 6, 30)),
    ("2022 drawdown", dt.date(2022, 1, 1), dt.date(2022, 12, 31)),
)
NORMAL_REGIME = "normal"
CRISIS_REGIMES: tuple[str, ...] = tuple(label for label, _, _ in REGIME_WINDOWS)


def _as_date(d) -> dt.date:
    """Normalize a date / datetime / pandas Timestamp to datetime.date."""
    if isinstance(d, dt.datetime):   # also catches pandas.Timestamp (a datetime subclass)
        return d.date()
    if isinstance(d, dt.date):
        return d
    return dt.date(d.year, d.month, d.day)


def regime_of(d) -> str:
    """Tag a date with its market-stress regime label, else 'normal'."""
    dd = _as_date(d)
    for label, lo, hi in REGIME_WINDOWS:
        if lo <= dd <= hi:
            return label
    return NORMAL_REGIME


def regime_breakdown(dates, returns, periods_per_year: int) -> dict[str, dict]:
    """Group per-period returns by the regime of their (period-end) date.

    Returns {regime_label: {"n", "total_return", "ann_vol", "max_drawdown"}} for every regime with
    >= 1 finite period in this run. Regimes the run never reaches are simply absent from the dict;
    the caller renders those as "Not estimated" (this window/lane can't speak to them).
    """
    out: dict[str, list[float]] = {}
    for d, r in zip(list(dates), list(returns), strict=True):
        if not (isinstance(r, (int, float)) and math.isfinite(r)):
            continue
        out.setdefault(regime_of(d), []).append(float(r))
    return {
        label: {
            "n": len(rs),
            "total_return": total_return(rs),
            "ann_vol": annualized_volatility(rs, periods_per_year),
            "max_drawdown": max_drawdown(rs),
        }
        for label, rs in out.items()
    }
