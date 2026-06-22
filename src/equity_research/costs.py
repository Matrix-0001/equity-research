"""Transaction-cost model for the v3 backtester.

Non-negotiable #2 (CLAUDE.md / README): **costs are always included; a pre-cost number is
never shown alone.** This module is the single, auditable place where a trade's frictional
cost is defined. It is pure (no I/O, no DB, no global state) so every number it produces is
hand-checkable in a test.

A trade of `traded_notional` dollars pays, in basis points (bps) of that notional:
  - a **half-spread** (`spread_bps`): you cross from the mid to the touch on each trade;
  - a **commission** (`commission_bps`): broker / exchange fees;
  - **market impact**: a square-root law in the trade's *participation* in average daily
    dollar volume (ADV) -- `impact_coef * sqrt(participation)`. Bigger trades relative to
    available liquidity move the price more (cf. Almgren et al.). Participation is capped
    (`impact_participation_cap`) so a thin ADV cannot produce an absurd estimate, and an
    UNKNOWN ADV is charged at that cap -- conservatively, never skipped (skipping would
    understate costs and break non-negotiable #2).

    Cost(bps) = spread_bps + commission_bps + impact_coef * sqrt(min(participation, cap))
    Cost($)   = traded_notional * Cost(bps) / 10_000

These are explicit, configurable ASSUMPTIONS (see `config.BacktestConfig`), not observed
vendor fills -- which is one of the reasons a free-lane backtest is stamped "indicative only".
"""

from __future__ import annotations

import math

from equity_research.config import BacktestConfig

_BPS = 10_000.0


def participation(traded_notional: float, adv_dollar: float | None, cap: float) -> float:
    """Trade's share of one day's dollar volume, capped at `cap`.

    Unknown / non-positive / non-finite ADV -> `cap` (charge impact conservatively rather than
    fabricate a low-impact estimate). `traded_notional` is taken as an absolute size.
    """
    notional = abs(float(traded_notional))
    if adv_dollar is None or not math.isfinite(float(adv_dollar)) or float(adv_dollar) <= 0.0:
        return cap
    return min(notional / float(adv_dollar), cap)


def impact_bps(part: float, cfg: BacktestConfig) -> float:
    """Square-root market-impact term in bps: impact_coef * sqrt(participation)."""
    return cfg.impact_coef * math.sqrt(max(0.0, part))


def trade_cost(
    traded_notional: float, adv_dollar: float | None, cfg: BacktestConfig
) -> float:
    """Dollar cost of trading `traded_notional` dollars of a name with daily dollar volume `adv_dollar`.

    Half-spread + commission + sqrt-law market impact, all in bps of the (absolute) notional.
    A zero-size trade costs nothing. `adv_dollar` is average daily *dollar* volume, matching
    `universe.screening_universe`'s `avg_dollar_volume`.
    """
    notional = abs(float(traded_notional))
    if notional == 0.0:
        return 0.0
    part = participation(notional, adv_dollar, cfg.impact_participation_cap)
    bps = cfg.spread_bps + cfg.commission_bps + impact_bps(part, cfg)
    return notional * bps / _BPS
