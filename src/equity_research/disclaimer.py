"""Compliance & honest-output scaffolding.

Encodes two non-negotiables structurally so they can't be forgotten:
  - the persistent disclaimer (shown on every output surface), and
  - the per-security report format, where every uncomputed field defaults to
    "Not estimated" rather than a fabricated value.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field, fields
from enum import Enum

PERSISTENT_DISCLAIMER = (
    "Research only. Not investment advice. This tool makes no recommendations. "
    "The user is solely responsible for all decisions. Past patterns do not predict "
    "future returns."
)

# Sentinel for any field the system did not compute. Per non-negotiable #1, a
# missing value is reported as this string — never guessed, never left blank.
NOT_ESTIMATED = "Not estimated"


class ConfidenceTier(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass
class SecurityReport:
    """The honest output format from the system spec.

    Every field defaults to NOT_ESTIMATED. A field only holds a value if it was
    actually computed from specific inputs. The LLM may populate the narrative
    fields (bull_case / bear_case) but must ground them ONLY in the computed
    figures above and may never introduce a number of its own.
    """

    ticker: str
    company: str = NOT_ESTIMATED
    data_completeness: str = NOT_ESTIMATED      # "% populated; missing: [...]"
    confidence_tier: str = NOT_ESTIMATED        # ConfidenceTier + reason
    valuation_read: str = NOT_ESTIMATED         # implied growth vs peers, as a RANGE
    factor_profile: str = NOT_ESTIMATED         # value/quality/momentum/profitability/low-vol pctile
    quality_risk_flags: str = NOT_ESTIMATED     # Piotroski/Altman/Beneish/leverage/dilution/liquidity
    historical_base_rate: str = NOT_ESTIMATED   # fwd-5yr distribution, "base rate, not a forecast"
    key_risks_machine: str = NOT_ESTIMATED      # quantified exposures
    bull_case: str = NOT_ESTIMATED              # LLM narrative grounded in figures above
    bear_case: str = NOT_ESTIMATED              # LLM narrative grounded in figures above
    suggested_role_weight: str = NOT_ESTIMATED  # optimizer output + constraints; "not advice"
    what_would_change_this: str = NOT_ESTIMATED # 2-3 data points that most move the assessment

    def completeness_pct(self) -> float:
        """Fraction of report fields actually populated (computed, not sentinel)."""
        report_fields = [f for f in fields(self) if f.name not in ("ticker", "company")]
        populated = sum(
            1 for f in report_fields if getattr(self, f.name) != NOT_ESTIMATED
        )
        return populated / len(report_fields) if report_fields else 0.0

    def render(self) -> str:
        rows = [
            ("Ticker / Company", f"{self.ticker} - {self.company}"),
            ("Data completeness", self.data_completeness),
            ("Confidence tier", self.confidence_tier),
            ("Valuation read", self.valuation_read),
            ("Factor profile", self.factor_profile),
            ("Quality/risk flags", self.quality_risk_flags),
            ("Historical base rate", self.historical_base_rate),
            ("Key risks (machine)", self.key_risks_machine),
            ("Bull case", self.bull_case),
            ("Bear case", self.bear_case),
            ("Suggested role/weight", self.suggested_role_weight),
            ("What would change this", self.what_would_change_this),
        ]
        width = max(len(label) for label, _ in rows)
        body = "\n".join(f"{label:<{width}} : {value}" for label, value in rows)
        return f"{body}\n{'-' * width}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"


# Stamp applied to every run whose data lane cannot satisfy non-negotiable #2 (survivorship-free,
# point-in-time index membership). Free Lane-1 data never can, so Phase A runs always carry it.
INDICATIVE_STAMP = "INDICATIVE ONLY - survivorship/lookahead risk present"


def _fmt(value: float | str | None, pct: bool = False, nd: int = 2) -> str:
    """Render a metric, mapping None/NaN to NOT_ESTIMATED (never a fabricated number)."""
    if isinstance(value, str):
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return NOT_ESTIMATED
    return f"{value * 100:.{nd}f}%" if pct else f"{value:.{nd}f}"


@dataclass
class BacktestResult:
    """Honest backtest output (v3). Mirrors `SecurityReport`'s posture:

      - every headline number is NET of costs, with the GROSS (pre-cost) figure shown alongside
        so a pre-cost result is never presented on its own (non-negotiable #2);
      - the run carries an explicit data lane + validity stamp (`valid=False` -> indicative only);
      - a backtest is ONE realized path, so the Sharpe is shown as a range (+/- 1 standard error),
        never as a lone hero number (non-negotiable #3);
      - anything not estimable reads NOT_ESTIMATED rather than a fabricated value.

    Stats are stored as plain floats (NaN where unknown); `render()` formats them. `nav_curve` is
    kept for programmatic use (list of (date, NAV)); only its endpoints are printed.
    """

    strategy: str
    lane: str                              # "free" | "sharadar"
    valid: bool                            # False => indicative-only run
    start: dt.date
    end: dt.date
    rebalance: str
    periods_per_year: int = 12
    n_periods: int = 0                     # number of realized holding periods
    initial_capital: float = 0.0
    final_nav: float = 0.0
    # Net (post-cost) headline.
    cagr_net: float = float("nan")
    vol_net: float = float("nan")
    sharpe_net: float = float("nan")
    sharpe_se: float = float("nan")
    sortino_net: float = float("nan")
    max_drawdown: float = float("nan")
    hit_rate: float = float("nan")
    avg_turnover: float = float("nan")
    # Gross (pre-cost) comparison.
    cagr_gross: float = float("nan")
    vol_gross: float = float("nan")
    sharpe_gross: float = float("nan")
    # Cost accounting.
    total_txn_cost: float = 0.0
    total_tax: float = 0.0
    cost_drag_annual: float = float("nan")  # cagr_gross - cagr_net (annualized cost drag)
    # Benchmark (a strategy return is meaningless without one): equal-weight 1/N of the SAME
    # point-in-time investable universe, held and costed identically. Empty label => not run.
    benchmark_label: str = ""
    bench_cagr_net: float = float("nan")
    bench_vol_net: float = float("nan")
    bench_sharpe_net: float = float("nan")
    bench_max_drawdown: float = float("nan")
    excess_cagr: float = float("nan")       # strategy net CAGR - benchmark net CAGR
    # Provenance.
    cost_assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    nav_curve: list[tuple[dt.date, float]] = field(default_factory=list)

    def _sharpe_range(self) -> str:
        if math.isnan(self.sharpe_net):
            return NOT_ESTIMATED
        if math.isnan(self.sharpe_se):
            return f"{self.sharpe_net:.2f}"
        lo, hi = self.sharpe_net - self.sharpe_se, self.sharpe_net + self.sharpe_se
        return f"{self.sharpe_net:.2f}  (+/- 1 SE: {lo:.2f} to {hi:.2f})"

    def render(self) -> str:
        stamp = "VALID (survivorship-free)" if self.valid else self.notes_stamp()
        lines = [
            f"Backtest - strategy: {self.strategy} | {self.start} to {self.end} | "
            f"rebalanced {self.rebalance}",
            f"Data lane: {self.lane}   Validity: {stamp}",
            f"Periods: {self.n_periods} ({self.periods_per_year}/yr) | "
            f"capital ${self.initial_capital:,.0f} -> ${self.final_nav:,.0f}",
            "",
            f"{'Metric':<22}{'Net (after costs)':>22}{'Gross (pre-cost)':>20}",
            f"{'CAGR':<22}{_fmt(self.cagr_net, pct=True):>22}{_fmt(self.cagr_gross, pct=True):>20}",
            f"{'Annualized vol':<22}{_fmt(self.vol_net, pct=True):>22}"
            f"{_fmt(self.vol_gross, pct=True):>20}",
            f"{'Sharpe (net)':<22}{self._sharpe_range():>22}"
            f"{_fmt(self.sharpe_gross):>20}",
            f"{'Sortino (net)':<22}{_fmt(self.sortino_net):>22}{'':>20}",
            f"{'Max drawdown':<22}{_fmt(self.max_drawdown, pct=True):>22}{'':>20}",
            f"{'Hit rate':<22}{_fmt(self.hit_rate, pct=True):>22}{'':>20}",
            f"{'Avg turnover/rebal':<22}{_fmt(self.avg_turnover, pct=True):>22}{'':>20}",
            "",
            f"Costs charged (ALWAYS included): transaction ${self.total_txn_cost:,.0f} + "
            f"tax ${self.total_tax:,.0f}",
            f"Annualized cost drag (gross CAGR - net CAGR): {_fmt(self.cost_drag_annual, pct=True)}",
        ]
        if self.benchmark_label:
            lines.append("")
            lines.append(f"Benchmark ({self.benchmark_label}):")
            lines.append(
                f"  CAGR {_fmt(self.bench_cagr_net, pct=True)} | "
                f"vol {_fmt(self.bench_vol_net, pct=True)} | "
                f"Sharpe {_fmt(self.bench_sharpe_net)} | "
                f"max DD {_fmt(self.bench_max_drawdown, pct=True)}"
            )
            lines.append(f"  Strategy EXCESS CAGR vs benchmark: {_fmt(self.excess_cagr, pct=True)}")
        if self.cost_assumptions:
            lines.append("")
            lines.append("Cost assumptions (modelled, not observed fills):")
            lines += [f"  - {a}" for a in self.cost_assumptions]
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines += [f"  - {n}" for n in self.notes]
        if not self.valid:
            lines.append("")
            lines.append(f"*** {INDICATIVE_STAMP} ***")
        body = "\n".join(lines)
        return f"{body}\n{'-' * 64}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"

    def notes_stamp(self) -> str:
        return INDICATIVE_STAMP
