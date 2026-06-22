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

import math

import numpy as np

NAN = float("nan")


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
