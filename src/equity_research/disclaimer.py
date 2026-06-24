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
# point-in-time index membership). Free Lane-1 data (Yahoo, current names only) never can.
INDICATIVE_STAMP = "INDICATIVE ONLY - survivorship/lookahead risk present"

# Stamp for the DIY survivorship-free lane: point-in-time index membership (fja05680) + delisted
# prices (SimFin free). Honest about its limits -- not a pristine vendor-grade "valid" run.
SURVIVORSHIP_STAMP = (
    "BEST-EFFORT SURVIVORSHIP-FREE - PIT index membership + delisted prices (SimFin free, >=2019); "
    "coverage gaps possible; PIT fundamentals (EDGAR) for current names only"
)


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
    lane: str                              # "free" | "simfin" | "sharadar"
    valid: bool                            # True only for a pristine vendor-grade survivorship-free run
    start: dt.date
    end: dt.date
    rebalance: str
    stamp: str = INDICATIVE_STAMP          # honest validity description shown on the run
    periods_per_year: int = 12
    n_periods: int = 0                     # number of realized holding periods
    initial_capital: float = 0.0
    final_nav: float = 0.0
    n_names_traded: int = 0                # distinct tickers ever held (universe-coverage signal)
    avg_holdings: float = float("nan")     # average names held per rebalance
    # Net (post-cost) headline.
    cagr_net: float = float("nan")
    vol_net: float = float("nan")
    sharpe_net: float = float("nan")
    sharpe_se: float = float("nan")
    sortino_net: float = float("nan")
    max_drawdown: float = float("nan")
    hit_rate: float = float("nan")
    avg_turnover: float = float("nan")
    # Phase C - probabilistic Sharpe (P(true SR > 0), corrected for sample length + non-normality)
    # and the return distribution's higher moments that drive it.
    prob_sharpe_positive: float = float("nan")
    ret_skew: float = float("nan")
    ret_kurtosis: float = float("nan")
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
    # Phase C - multi-regime stress: {regime_label: {n, total_return, ann_vol, max_drawdown}} for
    # the regimes this run's window/lane actually reached. Crisis regimes it never reaches are
    # absent here and render as "Not estimated" (never fabricated coverage).
    regime_stats: dict[str, dict] = field(default_factory=dict)
    # Phase C - locked test set (out-of-sample discipline). If a lock boundary is set, the realized
    # return path is split into in-sample (before) and the locked out-of-sample tail (on/after), and
    # each is summarized separately so a single hero number can't span selection + evaluation.
    locked_test_start: dt.date | None = None
    is_n: int = 0
    is_cagr: float = float("nan")
    is_vol: float = float("nan")
    is_sharpe: float = float("nan")
    is_max_drawdown: float = float("nan")
    oos_n: int = 0
    oos_cagr: float = float("nan")
    oos_vol: float = float("nan")
    oos_sharpe: float = float("nan")
    oos_max_drawdown: float = float("nan")
    # Provenance + programmatic (not printed): the realized net path, for compare / regime / lock.
    cost_assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    nav_curve: list[tuple[dt.date, float]] = field(default_factory=list)
    net_returns: list[float] = field(default_factory=list)
    period_end_dates: list[dt.date] = field(default_factory=list)

    def _sharpe_range(self) -> str:
        if math.isnan(self.sharpe_net):
            return NOT_ESTIMATED
        if math.isnan(self.sharpe_se):
            return f"{self.sharpe_net:.2f}"
        lo, hi = self.sharpe_net - self.sharpe_se, self.sharpe_net + self.sharpe_se
        return f"{self.sharpe_net:.2f}  (+/- 1 SE: {lo:.2f} to {hi:.2f})"

    def _regime_lines(self) -> list[str]:
        """Per-regime stress breakdown; crisis regimes the run can't reach read 'Not estimated'."""
        from equity_research import metrics  # local import: single source of truth for the labels

        lines = ["", "Multi-regime stress (net return / vol / max drawdown by market regime):"]
        for label in (*metrics.CRISIS_REGIMES, metrics.NORMAL_REGIME):
            d = self.regime_stats.get(label)
            if d and d.get("n", 0) > 0:
                lines.append(
                    f"  {label:<16} ret {_fmt(d['total_return'], pct=True):>9}  "
                    f"vol {_fmt(d['ann_vol'], pct=True):>9}  "
                    f"maxDD {_fmt(d['max_drawdown'], pct=True):>9}  (n={d['n']})"
                )
            else:
                lines.append(f"  {label:<16} {NOT_ESTIMATED} (no periods in this run's window)")
        lines.append("  (Regime windows: dot-com 2000-2002, GFC 2007-2009, COVID 2020, 2022 "
                     "drawdown; else normal.)")
        return lines

    def _locked_test_lines(self) -> list[str]:
        """In-sample vs out-of-sample (locked-tail) split -- the structural OOS-discipline report."""
        if self.locked_test_start is None:
            return ["", "Out-of-sample discipline: no locked test boundary set for this run "
                        "(pass --locked-test-start to hold out a tail; selection must never peek)."]
        lines = [
            "",
            f"Out-of-sample discipline (locked test set starts {self.locked_test_start}):",
            f"  {'Window':<16}{'n':>4}{'CAGR':>12}{'vol':>12}{'Sharpe':>10}{'maxDD':>12}",
            f"  {'In-sample':<16}{self.is_n:>4}{_fmt(self.is_cagr, pct=True):>12}"
            f"{_fmt(self.is_vol, pct=True):>12}{_fmt(self.is_sharpe):>10}"
            f"{_fmt(self.is_max_drawdown, pct=True):>12}",
            f"  {'Out-of-sample':<16}{self.oos_n:>4}{_fmt(self.oos_cagr, pct=True):>12}"
            f"{_fmt(self.oos_vol, pct=True):>12}{_fmt(self.oos_sharpe):>10}"
            f"{_fmt(self.oos_max_drawdown, pct=True):>12}",
            "  The locked tail is evaluated ONCE; no strategy/parameter selection may use it.",
        ]
        return lines

    def render(self) -> str:
        stamp = "VALID (survivorship-free)" if self.valid else self.stamp
        lines = [
            f"Backtest - strategy: {self.strategy} | {self.start} to {self.end} | "
            f"rebalanced {self.rebalance}",
            f"Data lane: {self.lane}   Validity: {stamp}",
            f"Periods: {self.n_periods} ({self.periods_per_year}/yr) | "
            f"capital ${self.initial_capital:,.0f} -> ${self.final_nav:,.0f}",
            f"Universe: {self.n_names_traded} distinct names traded"
            + (f" (avg {self.avg_holdings:.0f}/rebalance)"
               if not math.isnan(self.avg_holdings) else ""),
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
            f"{'Prob(SR>0) [PSR]':<22}{_fmt(self.prob_sharpe_positive, pct=True):>22}{'':>20}",
            "",
            f"PSR = P(true Sharpe > 0), corrected for sample length + return skew "
            f"({_fmt(self.ret_skew)}) / kurtosis ({_fmt(self.ret_kurtosis)}).",
            f"Costs charged (ALWAYS included): transaction ${self.total_txn_cost:,.0f} + "
            f"tax ${self.total_tax:,.0f}",
            f"Annualized cost drag (gross CAGR - net CAGR): {_fmt(self.cost_drag_annual, pct=True)}",
        ]
        lines += self._regime_lines()
        lines += self._locked_test_lines()
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
            lines.append(f"*** {self.stamp} ***")
        body = "\n".join(lines)
        return f"{body}\n{'-' * 64}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"


@dataclass
class BacktestCompare:
    """Honest multiple-strategy comparison (Phase C) -- the antidote to crowning the luckiest run.

    Runs N strategies over the same window and reports each one's Deflated Sharpe Ratio with
    `n_trials = N`, so a strategy that merely won a five-horse race is discounted, not celebrated.
    Selection uses ONLY the in-sample window (everything before `locked_test_start`); the locked
    out-of-sample tail is scored ONCE, for the selected strategy alone -- never used to pick it.
    Every row is net of costs and carries the run's lane/validity stamp.
    """

    lane: str
    stamp: str
    start: dt.date
    end: dt.date
    rebalance: str
    periods_per_year: int
    locked_test_start: dt.date | None
    n_trials: int
    sr_variance: float                      # cross-trial variance of the in-sample per-period Sharpes
    rows: list[dict] = field(default_factory=list)   # per strategy (see compare_backtests)
    selected: str = ""
    valid: bool = False
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        stamp = "VALID (survivorship-free)" if self.valid else self.stamp
        lines = [
            f"Backtest comparison - {self.n_trials} strategies | {self.start} to {self.end} | "
            f"rebalanced {self.rebalance}",
            f"Data lane: {self.lane}   Validity: {stamp}",
            f"Deflated Sharpe Ratio (DSR) corrects each strategy's Sharpe for MULTIPLE TESTING "
            f"(n_trials={self.n_trials}),",
            "non-normality, and sample length: DSR = P(true SR > the expected best-of-N SR).",
            "",
            f"  {'Strategy':<18}{'IS n':>5}{'IS CAGR':>11}{'IS Sharpe':>11}"
            f"{'DSR':>9}{'OOS n':>7}{'OOS CAGR':>11}",
        ]
        for r in self.rows:
            mark = "*" if r["strategy"] == self.selected else " "
            lines.append(
                f"{mark} {r['strategy']:<18}{r['is_n']:>5}"
                f"{_fmt(r['is_cagr'], pct=True):>11}{_fmt(r['is_sharpe']):>11}"
                f"{_fmt(r['dsr'], pct=True):>9}{r['oos_n']:>7}{_fmt(r['oos_cagr'], pct=True):>11}"
            )
        lines.append("")
        if self.selected:
            lines.append(f"Selected by IN-SAMPLE DSR (highest): {self.selected}  (marked * above)")
            sel = next((r for r in self.rows if r["strategy"] == self.selected), None)
            if sel is not None and self.locked_test_start is not None:
                lines.append(
                    f"Its locked out-of-sample tail (from {self.locked_test_start}, scored ONCE): "
                    f"CAGR {_fmt(sel['oos_cagr'], pct=True)} | Sharpe {_fmt(sel['oos_sharpe'])} | "
                    f"n={sel['oos_n']}"
                )
        else:
            lines.append("No strategy was selectable (insufficient in-sample data on every run).")
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines += [f"  - {n}" for n in self.notes]
        if not self.valid:
            lines.append("")
            lines.append(f"*** {self.stamp} ***")
        body = "\n".join(lines)
        return f"{body}\n{'-' * 64}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"
