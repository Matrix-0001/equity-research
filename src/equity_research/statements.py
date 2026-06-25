"""Standardized financial statements from the existing as-reported `fundamentals` table.

This is a RENDERING layer, not a new data pull: every line item is an XBRL concept we already
ingest from SEC EDGAR companyfacts (`data/edgar.py`), keyed by filing date, so the statements are
**as-reported and point-in-time** -- a statement built "as of" a date shows only what had actually
been filed by then. Missing line items read the literal **"Not estimated"**, never a fabricated 0
(non-negotiable #1); a bank with no `gross_profit` tag simply shows "Not estimated" for that row.

Annual basis only in Phase 1 (last N fiscal years via `pit.annual_history`, amendments collapsed to
their latest-filed version); a quarterly toggle is a later phase. One derived line is computed and
labelled as such: Free cash flow = operating cash flow - capital expenditures (only when both are
present; otherwise "Not estimated").
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from equity_research.config import Config, config
from equity_research.data import pit
from equity_research.disclaimer import NOT_ESTIMATED, PERSISTENT_DISCLAIMER

# (display label, fundamentals column, unit). unit "usd" renders in $ millions; "per_share" as-is.
# The "_fcf" sentinel column is computed (operating_cash_flow - capex), not read from a tag.
INCOME_STATEMENT: list[tuple[str, str, str]] = [
    ("Revenue", "revenue", "usd"),
    ("Gross profit", "gross_profit", "usd"),
    ("Operating income", "operating_income", "usd"),
    ("Interest expense", "interest_expense", "usd"),
    ("Net income", "net_income", "usd"),
    ("Diluted EPS", "eps_diluted", "per_share"),
]
BALANCE_SHEET: list[tuple[str, str, str]] = [
    ("Total assets", "total_assets", "usd"),
    ("Current assets", "current_assets", "usd"),
    ("Cash & equivalents", "cash", "usd"),
    ("Receivables", "receivables", "usd"),
    ("PP&E, net", "ppe_net", "usd"),
    ("Total liabilities", "total_liabilities", "usd"),
    ("Current liabilities", "current_liabilities", "usd"),
    ("Long-term debt", "long_term_debt", "usd"),
    ("Total equity", "total_equity", "usd"),
    ("Retained earnings", "retained_earnings", "usd"),
]
CASH_FLOW: list[tuple[str, str, str]] = [
    ("Operating cash flow", "operating_cash_flow", "usd"),
    ("Capital expenditures", "capex", "usd"),
    ("Free cash flow (OCF - capex)", "_fcf", "usd"),  # derived/computed line
]
STATEMENTS: tuple[tuple[str, list[tuple[str, str, str]]], ...] = (
    ("Income statement", INCOME_STATEMENT),
    ("Balance sheet", BALANCE_SHEET),
    ("Cash flow", CASH_FLOW),
)


def _num(v) -> float | None:
    """Coerce a cell to a finite float, mapping None/NaN -> None (so it renders 'Not estimated')."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def fmt_value(v: float | None, unit: str) -> str:
    """Render one cell honestly: None -> 'Not estimated'; USD in $ millions; per-share as-is."""
    if v is None:
        return NOT_ESTIMATED
    if unit == "per_share":
        return f"{v:,.2f}"
    return f"{v / 1e6:,.0f}"


@dataclass
class FinancialStatements:
    """As-reported annual statements for one ticker (last N fiscal years), point-in-time on `as_of`.

    `periods` are fiscal-year-end dates, MOST RECENT FIRST, and every value list aligns 1:1 with
    them. `sections` is [(statement_name, [(label, unit, [value-per-period]), ...]), ...]; a missing
    value is None and renders as "Not estimated".
    """

    ticker: str
    company: str
    as_of: dt.date
    periods: list[dt.date] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)        # filing form per period (provenance)
    filed: list[dt.date] = field(default_factory=list)     # filing date per period (provenance)
    sections: list[tuple[str, list[tuple[str, str, list[float | None]]]]] = field(
        default_factory=list)

    def is_empty(self) -> bool:
        return not self.periods

    def render(self) -> str:
        head = f"Financial statements - {self.ticker}" + (f" ({self.company})" if self.company
                                                           else "")
        if self.is_empty():
            return (f"{head}\n  No annual filings (10-K/20-F/40-F) on record as of {self.as_of}. "
                    f"Run `pull {self.ticker}` to ingest EDGAR fundamentals.\n"
                    f"{'-' * 60}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}")
        lblw = max(len(lbl) for _, rows in self.sections for lbl, _, _ in rows) + 1
        colw = 15
        yr_hdr = "".join(f"{p.year:>{colw}}" for p in self.periods)
        form_hdr = "".join(f"{f:>{colw}}" for f in self.forms)
        lines = [
            head,
            f"As of {self.as_of} | as-reported (SEC EDGAR companyfacts), point-in-time "
            f"(filing date <= as_of).",
            "USD in millions except per-share. Most recent fiscal year first.",
            "",
            f"{'Fiscal year end':<{lblw}}{yr_hdr}",
            f"{'Filed form':<{lblw}}{form_hdr}",
        ]
        for name, rows in self.sections:
            lines.append("")
            lines.append(f"{name}:")
            for label, unit, vals in rows:
                cells = "".join(f"{fmt_value(v, unit):>{colw}}" for v in vals)
                lines.append(f"  {label:<{lblw - 2}}{cells}")
        lines.append("")
        lines.append("Missing line items read 'Not estimated' (the tag was absent in the filing), "
                     "never a fabricated 0. Free cash flow is computed (OCF - capex).")
        body = "\n".join(lines)
        return f"{body}\n{'-' * 60}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"


def financial_statements(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    ticker: str,
    n_years: int = 4,
    cfg: Config = config,
) -> FinancialStatements:
    """Assemble as-reported annual statements for `ticker` over the last `n_years` fiscal years.

    Point-in-time: only filings with `datekey <= as_of` are read (via `pit.annual_history`).
    """
    tkr = ticker.upper()
    info = pit.security_info(conn, [tkr])
    company = ""
    if not info.empty and "company" in info.columns:
        company = str(info.iloc[0]["company"] or "")

    hist = pit.annual_history(conn, as_of, [tkr], n=n_years)
    if hist.empty:
        return FinancialStatements(tkr, company, as_of)

    hist = hist.sort_values("yr_rank")  # yr_rank 1 = most recent fiscal year -> most recent first
    periods = [pd.Timestamp(d).date() for d in hist["reportperiod"]]
    forms = [str(f) for f in hist["form"]]
    filed = [pd.Timestamp(d).date() for d in hist["datekey"]]

    def col(name: str) -> list[float | None]:
        if name not in hist.columns:
            return [None] * len(hist)
        return [_num(v) for v in hist[name]]

    ocf, capex = col("operating_cash_flow"), col("capex")
    fcf = [(o - c) if (o is not None and c is not None) else None for o, c in zip(ocf, capex)]

    sections: list[tuple[str, list[tuple[str, str, list[float | None]]]]] = []
    for name, specs in STATEMENTS:
        rows: list[tuple[str, str, list[float | None]]] = []
        for label, key, unit in specs:
            vals = fcf if key == "_fcf" else col(key)
            rows.append((label, unit, vals))
        sections.append((name, rows))

    return FinancialStatements(tkr, company, as_of, periods, forms, filed, sections)
