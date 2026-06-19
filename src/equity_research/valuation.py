"""Valuation — reverse-DCF and relative multiples, expressed as RANGES.

Per the spec, this never emits a single "fair value" point estimate. Instead it
shows what the *current price implies*:

  - Reverse-DCF: solve for the free-cash-flow growth rate that makes a transparent
    two-stage DCF equal today's market cap. Reported as a RANGE across discount-rate
    assumptions (a higher required return implies the price is baking in more growth).
    Read it as "the market is pricing in roughly X-Y% FCF growth" — not a forecast.

  - Relative multiples: the stock's P/E, P/B, P/S alongside the universe median and
    interquartile range, so "expensive/cheap" is anchored to peers, not asserted.

Inputs that are missing or non-sensible (e.g. negative FCF for the reverse-DCF) yield
None / "Not estimated" rather than a fabricated number.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from equity_research import universe
from equity_research.config import Config, config
from equity_research.data import pit


@dataclass(frozen=True)
class ValuationConfig:
    discount_rates: tuple[float, ...] = (0.08, 0.09, 0.10)
    terminal_growth: float = 0.025
    horizon_years: int = 10
    g_bounds: tuple[float, float] = (-0.50, 1.00)  # search range for implied growth


def _two_stage_pv(fcf0: float, g: float, r: float, g_term: float, n: int) -> float:
    """PV of a 2-stage DCF: n years at growth g, then perpetuity at g_term."""
    pv, cf = 0.0, fcf0
    for t in range(1, n + 1):
        cf *= (1 + g)
        pv += cf / (1 + r) ** t
    terminal = cf * (1 + g_term) / (r - g_term)  # r > g_term guaranteed by config
    return pv + terminal / (1 + r) ** n


def _solve_implied_growth(
    market_cap: float, fcf0: float, r: float, vcfg: ValuationConfig
) -> tuple[float, bool]:
    """Bisection for the growth g where PV == market_cap. Returns (g, clamped)."""
    lo, hi = vcfg.g_bounds
    f = lambda g: _two_stage_pv(fcf0, g, r, vcfg.terminal_growth, vcfg.horizon_years) - market_cap
    if f(hi) < 0:
        return hi, True   # price implies growth above the search ceiling
    if f(lo) > 0:
        return lo, True   # even at the floor the model exceeds price (very cheap)
    for _ in range(100):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2, False


def implied_growth(
    market_cap: float | None, fcf0: float | None, vcfg: ValuationConfig = ValuationConfig()
) -> dict | None:
    """Implied FCF growth by discount rate + a low/high range. None if not estimable."""
    if not market_cap or not fcf0 or market_cap <= 0 or fcf0 <= 0:
        return None
    by_rate: dict[float, float] = {}
    clamped = False
    for r in vcfg.discount_rates:
        g, was_clamped = _solve_implied_growth(market_cap, fcf0, r, vcfg)
        by_rate[r] = g
        clamped = clamped or was_clamped
    gs = list(by_rate.values())
    return {
        "by_rate": by_rate,
        "low": min(gs),
        "high": max(gs),
        "clamped": clamped,
        "terminal_growth": vcfg.terminal_growth,
        "horizon_years": vcfg.horizon_years,
    }


def _col(funds: pd.DataFrame, name: str, index: pd.Index) -> pd.Series:
    return funds[name].reindex(index) if name in funds.columns else pd.Series(index=index, dtype="float64")


def relative_multiples(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, cfg: Config = config
) -> tuple[pd.DataFrame, dict]:
    """Per-ticker P/E, P/B, P/S for the universe + median/IQR stats across it."""
    scr = universe.screening_universe(conn, as_of, cfg)
    if scr.empty:
        return pd.DataFrame(), {}
    idx = scr.set_index("ticker").index
    mcap = scr.set_index("ticker")["market_cap"]
    funds = pit.fundamentals_asof(conn, as_of, list(idx), forms=pit.ANNUAL_FORMS)
    funds = funds.set_index("ticker") if not funds.empty else pd.DataFrame(index=[])

    ni, eq, rev = _col(funds, "net_income", idx), _col(funds, "total_equity", idx), _col(funds, "revenue", idx)
    df = pd.DataFrame(index=idx)
    df["pe"] = mcap / ni.where(ni > 0)
    df["pb"] = mcap / eq.where(eq > 0)
    df["ps"] = mcap / rev.where(rev > 0)
    stats = {
        m: {"median": float(df[m].median()), "p25": float(df[m].quantile(0.25)),
            "p75": float(df[m].quantile(0.75))}
        for m in ("pe", "pb", "ps") if df[m].notna().any()
    }
    return df, stats


@dataclass
class ValuationResult:
    ticker: str
    implied_growth: dict | None
    multiples: dict[str, float | None] = field(default_factory=dict)
    peer_stats: dict = field(default_factory=dict)
    note: str = ""


def valuation_for(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    ticker: str,
    cfg: Config = config,
    vcfg: ValuationConfig = ValuationConfig(),
) -> ValuationResult:
    t = ticker.upper()
    hist = pit.fundamentals_asof(conn, as_of, [t], forms=pit.ANNUAL_FORMS)
    fcf0 = None
    if not hist.empty:
        row = hist.iloc[0]
        ocf = row.get("operating_cash_flow")
        capex = row.get("capex")
        if pd.notna(ocf) and pd.notna(capex):
            fcf0 = float(ocf) - float(capex)
        shares = row.get("shares_outstanding")
    else:
        shares = None
    price = pit.last_price(conn, as_of, t)
    mcap = None if (shares is None or pd.isna(shares) or price is None) else float(shares) * price

    df, stats = relative_multiples(conn, as_of, cfg)
    mult = {m: (float(df.loc[t, m]) if (t in df.index and pd.notna(df.loc[t, m])) else None)
            for m in ("pe", "pb", "ps")} if not df.empty else {}

    note = "" if fcf0 and fcf0 > 0 else "Reverse-DCF needs positive trailing free cash flow."
    return ValuationResult(t, implied_growth(mcap, fcf0, vcfg), mult, stats, note)
