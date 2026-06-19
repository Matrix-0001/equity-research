"""Offline tests for macro rate/recession exposure analytics (synthetic, hand-computed).

Pins: the univariate OLS math (textbook numbers), end-to-end beta RECOVERY (returns built as an
exact linear function of a factor's changes -> the regression must return that coefficient), the
NBER-regime base-rate split, and the honest "Not estimated" path when no macro data is loaded.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from equity_research import exposures as ex
from equity_research.config import Config, ExposureConfig
from equity_research.data import storage
from equity_research.disclaimer import NOT_ESTIMATED

AS_OF = dt.date(2026, 6, 15)


def test_ols_hand_computed():
    x = np.array([1.0, 2, 3, 4, 5])
    y = np.array([2.0, 4, 5, 4, 5])
    r = ex._ols(x, y)
    assert r["beta"] == pytest.approx(0.6)
    assert r["alpha"] == pytest.approx(2.2)
    assert r["r2"] == pytest.approx(0.6)
    assert r["se"] == pytest.approx(0.282843, abs=1e-5)   # sqrt(MSE/Sxx) = sqrt(0.8/10)
    assert r["t"] == pytest.approx(2.12132, abs=1e-4)
    assert r["n"] == 5


def _cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path, exposures=ExposureConfig(
        frequency="W-FRI", lookback_days=4000, min_obs=10, min_regime_obs=3,
        rate_factors=("DGS10",), recession_series="USREC"))


def _build(tmp_path, beta_true=-2.5, n_weeks=40, seed=1):
    """Prices whose weekly return == beta_true * weekly change in DGS10, plus a USREC regime."""
    cfg = _cfg(tmp_path)
    c = storage.connect(cfg)
    fridays = pd.date_range(end="2026-06-12", periods=n_weeks + 1, freq="W-FRI")
    rng = np.random.default_rng(seed)
    dfac = rng.normal(0.0, 0.05, size=n_weeks + 1)   # weekly factor changes (vary)
    f = 3.0 + np.cumsum(dfac)                         # factor levels (random walk)
    rets = np.zeros(n_weeks + 1)
    rets[1:] = beta_true * (f[1:] - f[:-1])           # exact linear: return = beta * d(factor)
    px = 100.0 * np.cumprod(1.0 + rets)

    prices = pd.DataFrame({"ticker": "TST", "date": fridays, "closeadj": px,
                           "closeunadj": px, "volume": 1_000_000})
    storage.load_table(c, prices, "prices")

    far = pd.Timestamp("9999-12-31")
    dgs = pd.DataFrame({"series_id": "DGS10", "date": fridays, "value": f,
                        "realtime_start": fridays, "realtime_end": far})
    months = pd.date_range("2025-06-01", "2026-06-01", freq="MS")
    usrec_val = [1.0 if (m.year == 2026 and m.month in (2, 3)) else 0.0 for m in months]
    usrec = pd.DataFrame({"series_id": "USREC", "date": months, "value": usrec_val,
                          "realtime_start": months, "realtime_end": far})
    storage.load_table(c, pd.concat([dgs, usrec], ignore_index=True), "macro")
    return c, cfg


def test_exposures_recovers_known_beta(tmp_path):
    c, cfg = _build(tmp_path, beta_true=-2.5, n_weeks=40)
    try:
        rep = ex.macro_exposures(c, AS_OF, "TST", cfg)
    finally:
        c.close()
    dgs = next(fx for fx in rep.factors if fx.factor == "DGS10")
    assert dgs.beta == pytest.approx(-2.5, abs=1e-6)   # exact recovery of the planted sensitivity
    assert dgs.r2 == pytest.approx(1.0, abs=1e-9)      # perfectly linear by construction
    assert dgs.n_obs == 40


def test_exposures_recession_regime_split(tmp_path):
    c, cfg = _build(tmp_path)
    try:
        rep = ex.macro_exposures(c, AS_OF, "TST", cfg)
    finally:
        c.close()
    assert rep.recession is not None
    rec = rep.recession.by_regime
    assert rec["recession"]["n"] >= cfg.exposures.min_regime_obs  # Feb-Mar 2026 weeks landed here
    assert "ann_return" in rec["recession"]
    assert rec["expansion"]["n"] > 0


def test_exposures_not_estimated_without_macro(tmp_path):
    cfg = _cfg(tmp_path)
    c = storage.connect(cfg)
    fridays = pd.date_range(end="2026-06-12", periods=30, freq="W-FRI")
    storage.load_table(c, pd.DataFrame({
        "ticker": "TST", "date": fridays, "closeadj": np.linspace(100, 130, 30),
        "closeunadj": np.linspace(100, 130, 30), "volume": 1_000_000}), "prices")
    try:
        rep = ex.macro_exposures(c, AS_OF, "TST", cfg)
    finally:
        c.close()
    assert rep.factors == []
    assert NOT_ESTIMATED in rep.confidence
    assert NOT_ESTIMATED in rep.render()
