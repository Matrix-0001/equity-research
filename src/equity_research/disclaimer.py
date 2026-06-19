"""Compliance & honest-output scaffolding.

Encodes two non-negotiables structurally so they can't be forgotten:
  - the persistent disclaimer (shown on every output surface), and
  - the per-security report format, where every uncomputed field defaults to
    "Not estimated" rather than a fabricated value.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
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
