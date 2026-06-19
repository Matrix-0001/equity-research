"""Reference data — free universe seeds (no key required).

S&P 500 constituents from a public CSV. This is the CURRENT membership only, so a
universe built from it for *backtesting* is survivorship-biased (it omits names
removed from the index in the past) — fine for seeding a live screen, not for v3
historical tests. Sharadar's SP500 table (point-in-time membership) is the upgrade.
"""

from __future__ import annotations

import csv
import io

from equity_research.config import Config, config

_SP500_CSV = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
)


def sp500_constituents(cfg: Config = config) -> list[str]:
    """Current S&P 500 tickers, normalized for EDGAR/Yahoo (e.g. BRK.B -> BRK-B)."""
    import requests

    resp = requests.get(_SP500_CSV, headers={"User-Agent": cfg.sec_user_agent}, timeout=30)
    resp.raise_for_status()
    rows = csv.DictReader(io.StringIO(resp.text))
    out: list[str] = []
    for row in rows:
        sym = (row.get("Symbol") or "").strip().upper().replace(".", "-")
        if sym:
            out.append(sym)
    return out
