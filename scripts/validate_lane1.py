"""Manual live validation of the Lane-1 (free) data adapters.

Makes real network calls to SEC EDGAR + Stooq. Run:
    EQR_DATA_DIR=...\\equity-research\\data .venv\\Scripts\\python scripts\\validate_lane1.py

Not a unit test (it hits the network); it's a smoke check that the free pipeline
produces point-in-time-correct fundamentals and a working screening universe.
"""

from __future__ import annotations

import datetime as dt

from equity_research.config import config
from equity_research.data import edgar, pit, prices_free, storage
from equity_research import universe

TICKERS = ["AAPL", "MSFT"]


def main() -> None:
    print(f"data_dir = {config.data_dir}")
    print(f"SEC UA   = {config.sec_user_agent}")

    f_summary = edgar.ingest(TICKERS)
    print("EDGAR ingest:", f_summary)
    p_summary = prices_free.ingest(TICKERS)
    print("Prices ingest:", p_summary)

    conn = storage.connect()
    try:
        cols = ["ticker", "datekey", "reportperiod", "form", "revenue",
                "net_income", "shares_outstanding"]
        latest = pit.fundamentals_asof(conn, dt.date.today())
        print("\nLatest fundamentals known today:")
        print(latest[[c for c in cols if c in latest.columns]].to_string(index=False))

        # POINT-IN-TIME CHECK: nothing filed after the as-of date may appear.
        as_of = dt.date(2015, 6, 30)
        past = pit.fundamentals_asof(conn, as_of)
        assert (past["datekey"].dt.date <= as_of).all(), "LOOKAHEAD LEAK!"
        print(f"\nPIT check OK: as of {as_of}, latest datekey = "
              f"{past['datekey'].max().date()} (<= {as_of})")

        scr = universe.screening_universe(conn, dt.date.today())
        print("\nScreening universe today (liquid floor):")
        print(scr.to_string(index=False))
    finally:
        conn.close()

    print("\nVALIDATION OK")


if __name__ == "__main__":
    main()
