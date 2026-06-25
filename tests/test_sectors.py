"""Offline tests for SIC -> sector mapping + security_info (no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from equity_research.config import Config
from equity_research.data import pit, sectors, storage


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


def test_security_info_returns_company_and_sector(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    storage.load_table(c, pd.DataFrame([
        {"ticker": "AAA", "cik": 1, "company": "Alpha Inc", "sic": 3571,
         "sic_description": "Computers", "sector": "Information Technology"},
        {"ticker": "BBB", "cik": 2, "company": "Beta Corp", "sic": 2834,
         "sic_description": "Drugs", "sector": "Health Care"},
    ]), "securities")
    try:
        info = pit.security_info(c, ["AAA"])
    finally:
        c.close()
    assert list(info.columns) == ["ticker", "company", "sector", "sic_description"]
    assert info.iloc[0]["company"] == "Alpha Inc"
    assert info.iloc[0]["sector"] == "Information Technology"


def test_security_info_blank_when_company_column_absent(tmp_path):
    # A securities table from before the company backfill has no `company` column -> blank, not an error.
    c = storage.connect(Config(data_dir=tmp_path))
    storage.load_table(c, pd.DataFrame([
        {"ticker": "AAA", "cik": 1, "sic": 3571, "sic_description": "Computers", "sector": "IT"},
    ]), "securities")
    try:
        info = pit.security_info(c)
    finally:
        c.close()
    assert info.iloc[0]["company"] == ""
    assert info.iloc[0]["sector"] == "IT"


def test_security_info_no_table_returns_empty(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    try:
        info = pit.security_info(c)
    finally:
        c.close()
    assert info.empty and list(info.columns) == ["ticker", "company", "sector", "sic_description"]
