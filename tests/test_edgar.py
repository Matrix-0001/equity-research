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


# A 10-Q cash-flow statement is reported YEAR-TO-DATE only (no clean 3-month figure). The
# extractor must NOT mislabel that 6-month YTD value as the quarter -> it reads "Not estimated".
YTD_ONLY_FACTS = {
    "facts": {
        "us-gaap": {
            "NetCashProvidedByUsedInOperatingActivities": {
                "units": {"USD": [
                    {"accn": "q2", "start": "2026-01-01", "end": "2026-06-30",  # 181d YTD
                     "val": 500, "filed": "2026-08-01", "form": "10-Q"},
                ]}
            },
            "Assets": {  # instant, same period end -> sets reportperiod
                "units": {"USD": [
                    {"accn": "q2", "end": "2026-06-30", "val": 9000,
                     "filed": "2026-08-01", "form": "10-Q"},
                ]}
            },
        }
    }
}


def test_quarterly_flow_with_only_ytd_is_not_estimated():
    import pandas as pd

    row = extract_fundamentals("T", YTD_ONLY_FACTS).set_index("form").loc["10-Q"]
    # 181-day YTD is > 40% off the 90-day quarter target -> refused, not overstated.
    assert pd.isna(row["operating_cash_flow"])
    assert row["total_assets"] == 9000              # the instant item is still fine
