"""Macro rate / recession exposure analytics.

Measures a security's HISTORICAL sensitivity to macro factors -- it is not a forecast:
  - Rate / curve / credit exposure: regress the security's periodic returns on the periodic
    CHANGE in a macro factor (10Y yield, 10Y-2Y spread, high-yield spread). Reported as a beta
    with a standard error, t-stat, R^2 and the number of observations, so the reader sees the
    precision, not a false-precise point. Univariate per factor (each is its own sensitivity).
  - Recession exposure: the distribution of returns split by NBER regime (USREC) -- a BASE RATE,
    explicitly "base rate, not a forecast", and "Not estimated" for a regime with too few periods.

Everything is point-in-time: prices via `pit.prices_asof`, macro via `pit.macro_series_asof`
(the ALFRED vintage in force on `as_of`), so no future revision leaks in. Missing inputs read
"Not estimated" -- never a fabricated number.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from equity_research.config import Config, config
from equity_research.data import fred, pit, storage
from equity_research.disclaimer import NOT_ESTIMATED, PERSISTENT_DISCLAIMER

_ANNUAL = {"W-FRI": 52, "W": 52, "M": 12, "ME": 12, "B": 252, "D": 252}


def _ols(x: np.ndarray, y: np.ndarray) -> dict | None:
    """Univariate OLS y = a + b*x. Returns alpha/beta/se(beta)/t/r2/n, or None if undefined."""
    n = len(x)
    if n < 3:
        return None
    xb, yb = float(x.mean()), float(y.mean())
    sxx = float(((x - xb) ** 2).sum())
    syy = float(((y - yb) ** 2).sum())
    if sxx <= 0:
        return None
    beta = float(((x - xb) * (y - yb)).sum()) / sxx
    alpha = yb - beta * xb
    resid = y - (alpha + beta * x)
    sse = float((resid ** 2).sum())
    dof = n - 2
    mse = sse / dof if dof > 0 else float("nan")
    se = float(np.sqrt(mse / sxx)) if mse > 0 else 0.0
    t = beta / se if se > 0 else float("nan")
    r2 = 1.0 - sse / syy if syy > 0 else float("nan")
    return {"alpha": alpha, "beta": beta, "se": se, "t": t, "r2": r2, "n": n}


@dataclass
class FactorExposure:
    factor: str
    label: str
    beta: float | None = None
    se: float | None = None
    t_stat: float | None = None
    r2: float | None = None
    n_obs: int = 0
    note: str = ""


@dataclass
class RegimeBaseRate:
    series: str
    by_regime: dict[str, dict] = field(default_factory=dict)  # regime -> {ann_return, ann_vol, n}
    note: str = ""


@dataclass
class ExposureReport:
    ticker: str
    as_of: dt.date
    frequency: str
    factors: list[FactorExposure] = field(default_factory=list)
    recession: RegimeBaseRate | None = None
    confidence: str = NOT_ESTIMATED
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"Macro exposures - {self.ticker} | as of {self.as_of} | {self.frequency} returns",
            f"Confidence: {self.confidence}",
            "",
            "Rate / curve / credit sensitivity (return per +1.00 unit move in the factor;",
            "univariate OLS, point-in-time):",
            f"  {'Factor':<14}{'beta':>9}{'std err':>10}{'t':>7}{'R^2':>7}{'n':>5}  description",
        ]
        for fx in self.factors:
            if fx.beta is None:
                lines.append(f"  {fx.factor:<14}{NOT_ESTIMATED:>38}  {fx.note or fx.label}")
            else:
                lines.append(
                    f"  {fx.factor:<14}{fx.beta:>9.2f}{fx.se:>10.2f}{fx.t_stat:>7.2f}"
                    f"{fx.r2:>7.2f}{fx.n_obs:>5}  {fx.label}"
                )
        if self.recession is not None:
            lines.append("")
            lines.append(f"Recession base rate by {self.recession.series} regime "
                         f"(annualized; base rate, NOT a forecast):")
            for regime in ("expansion", "recession"):
                d = self.recession.by_regime.get(regime)
                if d and d.get("n", 0) > 0 and "ann_return" in d:
                    lines.append(f"  {regime:<11} return {d['ann_return']:>7.1%}  "
                                 f"vol {d['ann_vol']:>6.1%}  (n={d['n']})")
                else:
                    n = d.get("n", 0) if d else 0
                    lines.append(f"  {regime:<11} {NOT_ESTIMATED} (n={n} periods in this regime)")
            if self.recession.note:
                lines.append(f"  {self.recession.note}")
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines += [f"  - {n}" for n in self.notes]
        body = "\n".join(lines)
        return f"{body}\n{'-' * 60}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"


def _security_returns(conn, as_of, ticker, ecfg) -> pd.Series:
    px = pit.prices_asof(conn, as_of, ecfg.lookback_days, [ticker.upper()])
    if px.empty:
        return pd.Series(dtype="float64")
    s = px.set_index("date").sort_index()["closeadj"]
    return s.resample(ecfg.frequency).last().pct_change().dropna()


def _factor_changes(conn, as_of, series_id, ecfg) -> pd.Series:
    ms = pit.macro_series_asof(conn, as_of, series_id, ecfg.lookback_days)
    if ms.empty:
        return pd.Series(dtype="float64")
    s = ms.set_index("date").sort_index()["value"]
    return s.resample(ecfg.frequency).last().diff().dropna()


def _regime_base_rate(conn, as_of, ret, ecfg) -> RegimeBaseRate | None:
    ms = pit.macro_series_asof(conn, as_of, ecfg.recession_series, ecfg.lookback_days)
    if ms.empty or ret.empty:
        return None
    usrec = ms.set_index("date").sort_index()["value"]
    regime = usrec.reindex(ret.index, method="ffill")  # NBER value for each return period
    ann = _ANNUAL.get(ecfg.frequency, 52)
    out: dict[str, dict] = {}
    for name, flag in (("expansion", 0.0), ("recession", 1.0)):
        r = ret[regime == flag]
        if len(r) >= ecfg.min_regime_obs:
            out[name] = {"ann_return": float(r.mean()) * ann,
                         "ann_vol": float(r.std(ddof=1)) * np.sqrt(ann), "n": int(len(r))}
        else:
            out[name] = {"n": int(len(r))}
    note = ""
    if out["recession"].get("ann_return") is None:
        note = "No NBER-recession periods in the available price history -- recession base rate " \
               "not estimable on free (short, survivorship-biased) prices."
    return RegimeBaseRate(ecfg.recession_series, out, note)


def macro_exposures(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    ticker: str,
    cfg: Config = config,
) -> ExposureReport:
    """Point-in-time macro exposure report for one security."""
    ecfg = cfg.exposures
    t = ticker.upper()
    rep = ExposureReport(t, as_of, ecfg.frequency)

    ret = _security_returns(conn, as_of, t, ecfg)
    if ret.empty:
        rep.notes.append("No price history for this ticker -- pull it first.")
        rep.confidence = f"{NOT_ESTIMATED} (no prices)"
        return rep
    if not storage.table_exists(conn, "macro"):
        rep.notes.append("No macro data loaded -- run `pull-macro` (needs FRED_API_KEY).")
        rep.confidence = f"{NOT_ESTIMATED} (no macro data)"
        return rep

    factor_ns: list[int] = []
    for sid in ecfg.rate_factors:
        label = fred.DEFAULT_SERIES.get(sid, sid)
        chg = _factor_changes(conn, as_of, sid, ecfg)
        if chg.empty:
            rep.factors.append(FactorExposure(sid, label, note=f"{NOT_ESTIMATED} - no macro data "
                                              f"(run pull-macro)"))
            continue
        aligned = pd.concat([ret.rename("y"), chg.rename("x")], axis=1).dropna()
        if len(aligned) < ecfg.min_obs:
            rep.factors.append(FactorExposure(sid, label, n_obs=len(aligned),
                               note=f"{NOT_ESTIMATED} - only {len(aligned)} aligned periods "
                                    f"(< {ecfg.min_obs})"))
            continue
        res = _ols(aligned["x"].to_numpy(), aligned["y"].to_numpy())
        if res is None:
            rep.factors.append(FactorExposure(sid, label, note=f"{NOT_ESTIMATED} - degenerate fit"))
            continue
        rep.factors.append(FactorExposure(sid, label, res["beta"], res["se"], res["t"],
                                          res["r2"], res["n"]))
        factor_ns.append(res["n"])

    rep.recession = _regime_base_rate(conn, as_of, ret, ecfg)

    n = max(factor_ns) if factor_ns else 0
    tier = "High" if n >= 80 else "Medium" if n >= ecfg.min_obs else "Low"
    rep.confidence = (f"{tier} (n={n} {ecfg.frequency} periods; betas are measured historical "
                      f"sensitivities, not forecasts)") if n else f"{NOT_ESTIMATED} (no macro data)"
    if not factor_ns:
        rep.notes.append("No macro factors estimable -- run `pull-macro` to load FRED series.")
    return rep
