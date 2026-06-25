"""Tests for the SEC-submissions company-profile parser (`data/profile.py`).

Pure `parse_profile` over a synthetic submissions JSON -- no network. Pins the field extraction and
the honest "earliest SEC filing on record" proxy (min of the recent filingDate index).
"""

from __future__ import annotations

from equity_research.data.profile import parse_profile, parse_recent_filings

_SUBMISSIONS = {
    "name": "Apple Inc.",
    "exchanges": ["Nasdaq"],
    "stateOfIncorporation": "CA",
    "fiscalYearEnd": "0927",
    "category": "Large accelerated filer",
    "entityType": "operating",
    "addresses": {"business": {"city": "Cupertino", "stateOrCountry": "CA"}},
    "formerNames": [{"name": "Apple Computer Inc.", "from": "1990-01-01", "to": "2007-01-09"}],
    "filings": {"recent": {"filingDate": ["2024-11-01", "2010-10-27", "2015-10-28"]}},
}


def test_parse_profile_extracts_core_fields():
    p = parse_profile(_SUBMISSIONS)
    assert p["name"] == "Apple Inc."
    assert p["exchanges"] == "Nasdaq"
    assert p["state_of_incorp"] == "CA"
    assert p["fiscal_year_end"] == "0927"
    assert p["hq_city"] == "Cupertino" and p["hq_state"] == "CA"
    assert "Apple Computer Inc." in p["former_names"]


def test_first_filing_is_earliest_in_recent_index():
    # The proxy for "public since" is the EARLIEST filing date in the recent index, not the latest.
    assert parse_profile(_SUBMISSIONS)["first_filing_date"] == "2010-10-27"


def test_missing_fields_become_blank_not_crash():
    p = parse_profile({})  # an empty submissions blob must not raise
    assert p["name"] == "" and p["exchanges"] == "" and p["first_filing_date"] == ""
    assert p["former_names"] == "" and p["hq_city"] == ""


def test_multiple_exchanges_joined():
    p = parse_profile({"exchanges": ["NYSE", "CBOE"]})
    assert p["exchanges"] == "NYSE, CBOE"


_FILINGS = {"filings": {"recent": {
    "form": ["8-K", "10-Q", "4"],
    "filingDate": ["2026-05-01", "2026-05-01", "2026-04-15"],
    "accessionNumber": ["0000320193-26-000050", "0000320193-26-000049", "0000320193-26-000048"],
    "primaryDocument": ["aapl-8k.htm", "aapl-10q.htm", "form4.xml"],
    "primaryDocDescription": ["8-K", "10-Q", ""],
}}}


def test_parse_recent_filings_builds_edgar_links_newest_first():
    f = parse_recent_filings(_FILINGS, cik=320193, n=15)
    assert [r["form"] for r in f] == ["8-K", "10-Q", "4"]
    # Accession dashes are stripped in the EDGAR archive path; primary document appended.
    assert f[0]["url"] == ("https://www.sec.gov/Archives/edgar/data/320193/"
                           "000032019326000050/aapl-8k.htm")
    assert f[0]["filed_date"] == "2026-05-01"


def test_parse_recent_filings_respects_limit_and_empty():
    assert len(parse_recent_filings(_FILINGS, cik=320193, n=2)) == 2
    assert parse_recent_filings({}, cik=1) == []
