"""Offline tests for the EDGAR extractor — guards the flow-duration bug.

EDGAR reports several durations ending on the same date (a 10-Q carries both the
3-month and the year-to-date figure). extract_fundamentals must pick the duration
matching the form (quarter for 10-Q, year for 10-K), or every income-statement
number is silently overstated.
"""

from __future__ import annotations

from equity_research.data.edgar import extract_fundamentals

FACTS = {
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "units": {
                    "USD": [
                        # 10-Q: 3-month (90d) and 6-month YTD (181d), same end date.
                        {"accn": "q1", "start": "2026-01-01", "end": "2026-03-31",
                         "val": 100, "filed": "2026-05-01", "form": "10-Q"},
                        {"accn": "q1", "start": "2025-10-01", "end": "2026-03-31",
                         "val": 250, "filed": "2026-05-01", "form": "10-Q"},
                        # 10-K: 12-month (365d) and Q4 (90d), same end date.
                        {"accn": "k1", "start": "2025-01-01", "end": "2025-12-31",
                         "val": 400, "filed": "2026-02-01", "form": "10-K"},
                        {"accn": "k1", "start": "2025-10-01", "end": "2025-12-31",
                         "val": 120, "filed": "2026-02-01", "form": "10-K"},
                    ]
                }
            },
            "Assets": {  # instant (no start)
                "units": {"USD": [
                    {"accn": "q1", "end": "2026-03-31", "val": 5000,
                     "filed": "2026-05-01", "form": "10-Q"},
                ]}
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [
                    {"accn": "q1", "end": "2026-04-20", "val": 1000,
                     "filed": "2026-05-01", "form": "10-Q"},
                ]}
            }
        },
    }
}


def test_quarterly_and_annual_durations_selected_correctly():
    df = extract_fundamentals("TEST", FACTS).set_index("form")

    # 10-Q must take the 3-month figure (100), not the 6-month YTD (250).
    assert df.loc["10-Q", "revenue"] == 100
    # 10-K must take the 12-month figure (400), not the Q4 quarter (120).
    assert df.loc["10-K", "revenue"] == 400


def test_instant_and_shares_extracted():
    row = extract_fundamentals("TEST", FACTS).set_index("form").loc["10-Q"]
    assert row["total_assets"] == 5000              # instant balance-sheet item
    assert row["shares_outstanding"] == 1000        # cover-page count (latest end)
    assert str(row["reportperiod"].date()) == "2026-03-31"
    assert str(row["datekey"].date()) == "2026-05-01"
