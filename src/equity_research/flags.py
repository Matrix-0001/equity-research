"""Accounting-quality flags -Piotroski F, Altman Z, Beneish M.

These are FLAGS with published thresholds, deliberately NOT predictions. Each is
computed only when its inputs are actually present; otherwise the score is None
("Not estimated") and the missing inputs are listed, never silently treated as zero.

  - Piotroski F (0-9): fundamental strength; needs this year and last year (annual).
  - Altman Z: distress zones (>2.99 safe / 1.81-2.99 grey / <1.81 distress). Original
    model is calibrated for manufacturers and is unreliable for financials/banks.
  - Beneish M: earnings-manipulation screen; M > -1.78 is the classic flag threshold.
    Data-hungry (receivables, PP&E, depreciation, SG&A across two years).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import pit
from equity_research.disclaimer import NOT_ESTIMATED


@dataclass
class FlagResult:
    name: str
    score: float | None
    interpretation: str
    components: dict[str, float | bool | None] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def estimated(self) -> bool:
        return self.score is not None


def _get(row: pd.Series | None, key: str) -> float | None:
    if row is None or key not in row:
        return None
    val = row[key]
    return None if pd.isna(val) else float(val)


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


# --------------------------------------------------------------------------- #
# Piotroski F-score
# --------------------------------------------------------------------------- #
def piotroski_f(curr: pd.Series, prev: pd.Series | None) -> FlagResult:
    if prev is None:
        return FlagResult("Piotroski F", None, NOT_ESTIMATED,
                          missing=["prior-year annual filing"])
    roa_t, roa_p = _div(_get(curr, "net_income"), _get(curr, "total_assets")), \
        _div(_get(prev, "net_income"), _get(prev, "total_assets"))
    cr_t = _div(_get(curr, "current_assets"), _get(curr, "current_liabilities"))
    cr_p = _div(_get(prev, "current_assets"), _get(prev, "current_liabilities"))
    lev_t = _div(_get(curr, "long_term_debt"), _get(curr, "total_assets"))
    lev_p = _div(_get(prev, "long_term_debt"), _get(prev, "total_assets"))
    gm_t = _div(_get(curr, "gross_profit"), _get(curr, "revenue"))
    gm_p = _div(_get(prev, "gross_profit"), _get(prev, "revenue"))
    at_t = _div(_get(curr, "revenue"), _get(curr, "total_assets"))
    at_p = _div(_get(prev, "revenue"), _get(prev, "total_assets"))
    ni_t, ocf_t = _get(curr, "net_income"), _get(curr, "operating_cash_flow")
    sh_t, sh_p = _get(curr, "shares_outstanding"), _get(prev, "shares_outstanding")

    def _gt(a: float | None, b: float | None) -> bool | None:
        return None if a is None or b is None else a > b

    crit: dict[str, bool | None] = {
        "roa_positive": None if ni_t is None else ni_t > 0,
        "cfo_positive": None if ocf_t is None else ocf_t > 0,
        "roa_rising": _gt(roa_t, roa_p),
        "accruals_ok": None if (ocf_t is None or ni_t is None) else ocf_t > ni_t,
        "leverage_down": _gt(lev_p, lev_t),       # lower leverage is the "pass"
        "current_ratio_up": _gt(cr_t, cr_p),
        "no_dilution": None if (sh_t is None or sh_p is None) else sh_t <= sh_p,
        "gross_margin_up": _gt(gm_t, gm_p),
        "asset_turnover_up": _gt(at_t, at_p),
    }
    missing = [k for k, v in crit.items() if v is None]
    if missing:
        return FlagResult("Piotroski F", None, NOT_ESTIMATED, components=crit, missing=missing)
    score = sum(1 for v in crit.values() if v)
    label = "strong (>=7)" if score >= 7 else "weak (<=2)" if score <= 2 else "middling"
    return FlagResult("Piotroski F", float(score), f"F={score}/9 - {label}", components=crit)


# --------------------------------------------------------------------------- #
# Altman Z-score (original)
# --------------------------------------------------------------------------- #
def altman_z(curr: pd.Series, market_cap: float | None) -> FlagResult:
    ta = _get(curr, "total_assets")
    comps = {
        "working_capital/TA": _div(
            None if (_get(curr, "current_assets") is None or _get(curr, "current_liabilities") is None)
            else _get(curr, "current_assets") - _get(curr, "current_liabilities"), ta),
        "retained_earnings/TA": _div(_get(curr, "retained_earnings"), ta),
        "EBIT/TA": _div(_get(curr, "operating_income"), ta),
        "mktcap/total_liab": _div(market_cap, _get(curr, "total_liabilities")),
        "sales/TA": _div(_get(curr, "revenue"), ta),
    }
    weights = {"working_capital/TA": 1.2, "retained_earnings/TA": 1.4, "EBIT/TA": 3.3,
               "mktcap/total_liab": 0.6, "sales/TA": 1.0}
    missing = [k for k, v in comps.items() if v is None]
    note = "Original Z is calibrated for manufacturers; unreliable for financials/banks."
    if missing:
        return FlagResult("Altman Z", None, NOT_ESTIMATED, components=comps, missing=missing, note=note)
    z = sum(weights[k] * v for k, v in comps.items())
    zone = "safe (>2.99)" if z > 2.99 else "distress (<1.81)" if z < 1.81 else "grey (1.81-2.99)"
    return FlagResult("Altman Z", round(z, 2), f"Z={z:.2f} - {zone}", components=comps, note=note)


# --------------------------------------------------------------------------- #
# Beneish M-score
# --------------------------------------------------------------------------- #
def beneish_m(curr: pd.Series, prev: pd.Series | None) -> FlagResult:
    if prev is None:
        return FlagResult("Beneish M", None, NOT_ESTIMATED, missing=["prior-year annual filing"])

    def gm(row: pd.Series) -> float | None:
        return _div(_get(row, "gross_profit"), _get(row, "revenue"))

    def asset_quality(row: pd.Series) -> float | None:
        ca, ppe, ta = _get(row, "current_assets"), _get(row, "ppe_net"), _get(row, "total_assets")
        if ca is None or ppe is None or ta in (None, 0):
            return None
        return 1 - (ca + ppe) / ta

    def dep_rate(row: pd.Series) -> float | None:
        dep, ppe = _get(row, "depreciation"), _get(row, "ppe_net")
        return _div(dep, None if (dep is None or ppe is None) else dep + ppe)

    def leverage(row: pd.Series) -> float | None:
        ltd, cl, ta = _get(row, "long_term_debt"), _get(row, "current_liabilities"), _get(row, "total_assets")
        if ltd is None or cl is None or ta in (None, 0):
            return None
        return (ltd + cl) / ta

    comps = {
        "DSRI": _div(_div(_get(curr, "receivables"), _get(curr, "revenue")),
                     _div(_get(prev, "receivables"), _get(prev, "revenue"))),
        "GMI": _div(gm(prev), gm(curr)),
        "AQI": _div(asset_quality(curr), asset_quality(prev)),
        "SGI": _div(_get(curr, "revenue"), _get(prev, "revenue")),
        "DEPI": _div(dep_rate(prev), dep_rate(curr)),
        "SGAI": _div(_div(_get(curr, "sga"), _get(curr, "revenue")),
                     _div(_get(prev, "sga"), _get(prev, "revenue"))),
        "LVGI": _div(leverage(curr), leverage(prev)),
        "TATA": _div(
            None if (_get(curr, "net_income") is None or _get(curr, "operating_cash_flow") is None)
            else _get(curr, "net_income") - _get(curr, "operating_cash_flow"),
            _get(curr, "total_assets")),
    }
    w = {"DSRI": 0.92, "GMI": 0.528, "AQI": 0.404, "SGI": 0.892, "DEPI": 0.115,
         "SGAI": -0.172, "LVGI": -0.327, "TATA": 4.679}
    missing = [k for k, v in comps.items() if v is None]
    if missing:
        return FlagResult("Beneish M", None, NOT_ESTIMATED, components=comps, missing=missing)
    m = -4.84 + sum(w[k] * comps[k] for k in w)
    flag = "elevated (> -1.78: possible manipulation)" if m > -1.78 else "below threshold"
    return FlagResult("Beneish M", round(m, 2), f"M={m:.2f} - {flag}", components=comps)


def compute_flags(conn, as_of, ticker: str, cfg: Config = config) -> dict[str, FlagResult]:
    """All three flags for one ticker, point-in-time."""
    hist = pit.annual_history(conn, as_of, [ticker.upper()], n=2)
    if hist.empty:
        na = lambda name: FlagResult(name, None, NOT_ESTIMATED, missing=["no annual filing"])
        return {"piotroski": na("Piotroski F"), "altman": na("Altman Z"), "beneish": na("Beneish M")}
    by_rank = {int(r["yr_rank"]): r for _, r in hist.iterrows()}
    curr, prev = by_rank.get(1), by_rank.get(2)
    shares = _get(curr, "shares_outstanding")
    price = pit.last_price(conn, as_of, ticker.upper())
    mcap = None if (shares is None or price is None) else shares * price
    return {
        "piotroski": piotroski_f(curr, prev),
        "altman": altman_z(curr, mcap),
        "beneish": beneish_m(curr, prev),
    }
