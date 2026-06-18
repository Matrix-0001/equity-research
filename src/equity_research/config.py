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

    # --- Universe ----------------------------------------------------------
    universe_floor: UniverseFloor = field(default_factory=UniverseFloor)

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


# Module-level singleton for convenience; construct your own for tests.
config = Config()
