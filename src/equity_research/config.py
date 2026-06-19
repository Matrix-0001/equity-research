"""Configuration — deliberately plain and transparent (auditability over magic).

All tunable assumptions live here as explicit, documented values rather than
being scattered through the code. A reader should be able to audit every
assumption that affects an output from this one file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # reads ./.env if present (git-ignored); no-op otherwise


@dataclass(frozen=True)
class UniverseFloor:
    """v1 screening/portfolio universe filter (Phase 0 decision, 2026-06-18).

    NOTE: this is the *investable* universe only. The *backtest* universe is
    always the full delisted-inclusive Sharadar coverage — survivorship-bias
    freedom is non-negotiable and is never filtered by these floors.
    """

    min_market_cap_usd: float = 300_000_000.0   # exclude micro-caps
    min_avg_dollar_volume_usd: float = 1_000_000.0  # 20-day ADV liquidity floor
    min_price_usd: float = 1.0                   # exclude penny stocks
    avg_dollar_volume_window_days: int = 20


@dataclass(frozen=True)
class PortfolioConfig:
    """v2 portfolio-construction assumptions (one auditable place).

    These govern risk-based weighting (inverse-variance / equal-risk-contribution /
    HRP) and the capital-preservation guardrails. None of them introduce a return
    forecast -- weighting needs only a covariance matrix estimated from point-in-time
    prices. Fractional-Kelly is a separate library function that REQUIRES an explicit
    expected-return input; the live tool never manufactures one.
    """

    # Covariance estimation (from point-in-time daily returns).
    cov_lookback_days: int = 504      # ~2 trading years of calendar lookback
    min_return_obs: int = 252         # drop a name with fewer aligned daily returns

    # Capital-preservation guardrails.
    max_position_weight: float = 0.10   # hard cap per name (10%)
    max_adv_participation: float = 0.05  # a position's $ value <= 5% of its ADV
    default_portfolio_value_usd: float = 1_000_000.0

    # Fractional-Kelly (library-only; needs explicit expected returns).
    kelly_fraction: float = 0.25      # quarter-Kelly default (fraction < 1 is a guardrail)

    # Name selection / default behaviour.
    default_top_n: int = 20           # names by factor composite when tickers not given
    default_method: str = "hrp"


@dataclass(frozen=True)
class ExposureConfig:
    """Macro rate/recession exposure analytics (point-in-time time-series regressions).

    Betas come from regressing a security's periodic returns on a macro factor's periodic
    CHANGES -- a measured historical sensitivity, never a forecast. The recession reading is
    a base rate (return distribution by NBER regime), explicitly "base rate, not a forecast".
    """

    frequency: str = "W-FRI"          # resample frequency for returns + factor changes
    lookback_days: int = 504          # ~2 trading years of history for the regression
    min_obs: int = 24                 # need enough periods for a meaningful beta
    min_regime_obs: int = 8           # min periods in a regime to report its base rate
    # Daily/computed rate factors regressed univariately (each its own sensitivity).
    rate_factors: tuple[str, ...] = ("DGS10", "T10Y2Y", "BAMLH0A0HYM2")
    recession_series: str = "USREC"


@dataclass(frozen=True)
class Config:
    # --- Data location -----------------------------------------------------
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("EQR_DATA_DIR", "./data")).resolve()
    )

    # --- Vendor credentials (never hard-coded; read from env / .env) -------
    nasdaq_data_link_api_key: str | None = field(
        default_factory=lambda: os.getenv("NASDAQ_DATA_LINK_API_KEY") or None
    )

    # SEC EDGAR fair-access policy requires a User-Agent with real contact info.
    # Override via EQR_SEC_USER_AGENT if you prefer a different contact.
    sec_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "EQR_SEC_USER_AGENT", "equity-research/0.0.1 (darrelljagdeo21@gmail.com)"
        )
    )

    # FRED/ALFRED macro API key (free at fredaccount.stlouisfed.org). Vintage-aware
    # pulls (ALFRED) need it; without it the macro adapter raises a clear message.
    fred_api_key: str | None = field(
        default_factory=lambda: os.getenv("FRED_API_KEY") or None
    )

    # --- Universe ----------------------------------------------------------
    universe_floor: UniverseFloor = field(default_factory=UniverseFloor)

    # --- Portfolio construction (v2) ---------------------------------------
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)

    # --- Macro exposure analytics ------------------------------------------
    exposures: ExposureConfig = field(default_factory=ExposureConfig)

    # --- Point-in-time fundamentals ----------------------------------------
    # Sharadar SF1 'dimension'. AR* = As-Reported (original filing), which is
    # what point-in-time correctness requires. MR* = Most-Recent (restated) and
    # must NOT be used for any historical/backtest claim (lookahead bias).
    fundamentals_dimension: str = "ARQ"  # As-Reported Quarterly

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "equity_research.duckdb"

    def require_api_key(self) -> str:
        if not self.nasdaq_data_link_api_key:
            raise RuntimeError(
                "NASDAQ_DATA_LINK_API_KEY is not set. Subscribe to Sharadar SF1+SEP, "
                "then copy .env.example to .env and add your key. See README / Phase 0 doc."
            )
        return self.nasdaq_data_link_api_key

    def require_fred_api_key(self) -> str:
        if not self.fred_api_key:
            raise RuntimeError(
                "FRED_API_KEY is not set. Get a free key at "
                "https://fredaccount.stlouisfed.org/apikeys, then add it to your .env."
            )
        return self.fred_api_key


# Module-level singleton for convenience; construct your own for tests.
config = Config()
