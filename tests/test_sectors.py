"""Offline tests for SIC -> sector mapping (no network)."""

from __future__ import annotations

import pytest

from equity_research.data import sectors


@pytest.mark.parametrize("sic,expected", [
    (3571, "Information Technology"),   # electronic computers (AAPL)
    (3674, "Information Technology"),   # semiconductors (NVDA)
    (7372, "Information Technology"),   # prepackaged software
    (2834, "Health Care"),              # pharmaceutical preparations (JNJ)
    (3841, "Health Care"),              # surgical/medical instruments
    (1311, "Energy"),                   # crude petroleum & natural gas
    (2911, "Energy"),                   # petroleum refining (XOM)
    (4911, "Utilities"),                # electric services
    (4813, "Communication Services"),   # telephone communications
    (6021, "Financials"),               # national commercial banks (JPM)
    (6798, "Real Estate"),              # REIT
    (6512, "Real Estate"),              # operators of apartment buildings
    (2080, "Consumer Staples"),         # beverages (KO)
    (2820, "Materials"),                # plastics & resins
    (3711, "Consumer Discretionary"),   # motor vehicles
    (3728, "Industrials"),              # aircraft parts
    (9999, "Unknown"),
    (None, "Unknown"),
])
def test_sic_to_sector(sic, expected):
    assert sectors.sic_to_sector(sic) == expected
