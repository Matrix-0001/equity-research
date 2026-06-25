"""Assemble the honest per-security SecurityReport from computed figures.

Pulls together the factor ranks, valuation read, and accounting-quality flags into
the spec's output format, then (optionally) asks the bounded LLM for a bull/bear
narrative grounded only in those figures. Fields the system genuinely cannot compute
in v1 (forward-outcome base rates, optimizer weight) read "Not estimated" -never faked.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

from equity_research import factors, flags as flags_mod, llm, valuation
from equity_research.config import Config, config
from equity_research.data import pit
from equity_research.disclaimer import NOT_ESTIMATED, ConfidenceTier, SecurityReport


def _factor_profile_str(row: pd.Series) -> str:
    parts = []
    for g in ("value", "quality", "profitability", "momentum", "low_vol", "size"):
        v = row[g]
        parts.append(f"{g} {('%.0f' % v) if pd.notna(v) else 'NA'}")
    return (f"{' / '.join(parts)} (composite {row['composite']:.0f}; "
            f"{int(row['n_metrics'])}/8 metrics) - percentiles within the loaded universe")


def _valuation_str(val: valuation.ValuationResult) -> str:
    pieces = []
    ig = val.implied_growth
    if ig:
        clamp = " (search-bound)" if ig.get("clamped") else ""
        pieces.append(f"reverse-DCF implies {ig['low'] * 100:.1f}-{ig['high'] * 100:.1f}%/yr "
                      f"FCF growth{clamp}")
    else:
        pieces.append(f"reverse-DCF: {NOT_ESTIMATED}")
    for m, label in (("pe", "P/E"), ("pb", "P/B"), ("ps", "P/S")):
        v, st = val.multiples.get(m), val.peer_stats.get(m)
        if v is not None and st:
            pieces.append(f"{label} {v:.1f} vs peer median {st['median']:.1f}")
    s = "; ".join(pieces)
    return f"{s}  [{val.note}]" if val.note else s


def _flags_str(flg: dict) -> str:
    return " | ".join(
        flg[k].interpretation if flg[k].estimated else f"{flg[k].name}: {NOT_ESTIMATED}"
        for k in ("piotroski", "altman", "beneish")
    )


def _key_risks(conn, as_of, ticker, val, flg) -> str:
    risks: list[str] = []
    ig = val.implied_growth
    if ig and ig["low"] > 0.10:
        risks.append(f"priced for growth (~{ig['low'] * 100:.0f}%+/yr implied by price)")
    f = pit.fundamentals_asof(conn, as_of, [ticker], forms=pit.ANNUAL_FORMS)
    if not f.empty:
        row = f.iloc[0]
        eq, ltd = row.get("total_equity"), row.get("long_term_debt")
        if pd.notna(eq) and pd.notna(ltd) and eq and eq > 0 and ltd / eq > 1.0:
            risks.append(f"elevated leverage (LT debt / equity ~{ltd / eq:.1f}x)")
    if flg["altman"].estimated and flg["altman"].score is not None and flg["altman"].score < 1.81:
        risks.append("Altman distress zone")
    if flg["beneish"].estimated and flg["beneish"].score is not None and flg["beneish"].score > -1.78:
        risks.append("Beneish manipulation flag elevated")
    return "; ".join(risks) if risks else "None flagged among the computed metrics"


def _what_would_change(in_universe, n_metrics, n_flags, has_val, flg) -> str:
    items: list[str] = []
    if not in_universe:
        items.append("loading this ticker into the screening universe (pull/pull-sp500)")
    if not has_val:
        items.append("positive trailing free cash flow (unlocks the reverse-DCF growth read)")
    missing = [flg[k].name for k in flg if not flg[k].estimated]
    if missing:
        items.append("the missing accounting inputs for " + ", ".join(missing))
    if n_metrics < 8:
        items.append(f"the {8 - n_metrics} missing factor metric(s)")
    return "; ".join(items[:3]) if items else "All v1 inputs present; newer filings/prices refresh it"


def build_security_report(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    ticker: str,
    cfg: Config = config,
    use_llm: bool = True,
) -> SecurityReport:
    t = ticker.upper()
    prof = factors.factor_profile(conn, as_of, cfg)
    val = valuation.valuation_for(conn, as_of, t, cfg)
    flg = flags_mod.compute_flags(conn, as_of, t, cfg)

    rep = SecurityReport(ticker=t, company=t)
    in_universe = (not prof.empty) and t in prof.index
    if in_universe:
        row = prof.loc[t]
        rep.factor_profile = _factor_profile_str(row)
        n_metrics = int(row["n_metrics"])
    else:
        rep.factor_profile = "Not in the loaded screening universe -pull this ticker first"
        n_metrics = 0

    rep.valuation_read = _valuation_str(val)
    rep.quality_risk_flags = _flags_str(flg)
    rep.key_risks_machine = _key_risks(conn, as_of, t, val, flg)

    n_flags = sum(1 for k in flg if flg[k].estimated)
    has_val = val.implied_growth is not None
    rep.data_completeness = (f"factors {n_metrics}/8, flags {n_flags}/3, "
                             f"reverse-DCF {'yes' if has_val else 'no'}")
    score = n_metrics / 8 + n_flags / 3 + (1 if has_val else 0)
    tier = (ConfidenceTier.HIGH if score >= 2.4
            else ConfidenceTier.MEDIUM if score >= 1.4 else ConfidenceTier.LOW)
    rep.confidence_tier = f"{tier.value} (mechanical, from data completeness)"

    rep.historical_base_rate = f"{NOT_ESTIMATED} - forward-outcome base rates are a later phase"
    rep.suggested_role_weight = (
        f"{NOT_ESTIMATED} - a single-name report carries no weight; build a basket via "
        f"`construct` (risk parity / HRP). Not advice."
    )
    rep.what_would_change_this = _what_would_change(in_universe, n_metrics, n_flags, has_val, flg)

    if use_llm:
        figures = {
            "ticker": t,
            "factor_profile": rep.factor_profile,
            "valuation_read": rep.valuation_read,
            "quality_risk_flags": rep.quality_risk_flags,
            "key_risks": rep.key_risks_machine,
            "data_completeness": rep.data_completeness,
        }
        bb = llm.generate_bull_bear(figures)
        if bb:
            rep.bull_case = bb["bull_case"]
            rep.bear_case = bb["bear_case"]
        else:
            note = f"{NOT_ESTIMATED} - LLM not configured (set ANTHROPIC_API_KEY; pip install -e .[llm])"
            rep.bull_case = note
            rep.bear_case = NOT_ESTIMATED
    return rep
