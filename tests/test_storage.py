"""Tests for the persistent-universe upsert (replace-by-ticker)."""

from __future__ import annotations

import pandas as pd

from equity_research.config import Config
from equity_research.data import storage


def test_upsert_accumulates_and_refreshes(tmp_path):
    conn = storage.connect(Config(data_dir=tmp_path))

    storage.upsert_by_ticker(
        conn, pd.DataFrame({"ticker": ["AAA", "BBB"], "v": [1, 2]}), "fundamentals"
    )
    # Re-pull BBB (updated) + add CCC; AAA must survive untouched.
    storage.upsert_by_ticker(
        conn, pd.DataFrame({"ticker": ["BBB", "CCC"], "v": [20, 3]}), "fundamentals"
    )

    out = conn.execute("SELECT ticker, v FROM fundamentals ORDER BY ticker").df()
    assert out["ticker"].tolist() == ["AAA", "BBB", "CCC"]
    assert out.set_index("ticker").loc["AAA", "v"] == 1   # untouched
    assert out.set_index("ticker").loc["BBB", "v"] == 20  # refreshed
    conn.close()


def test_upsert_handles_column_drift(tmp_path):
    conn = storage.connect(Config(data_dir=tmp_path))
    storage.upsert_by_ticker(conn, pd.DataFrame({"ticker": ["AAA"], "a": [1]}), "fundamentals")
    # New pull introduces a new column (e.g. a newly added EDGAR concept).
    storage.upsert_by_ticker(conn, pd.DataFrame({"ticker": ["BBB"], "a": [2], "b": [9]}), "fundamentals")
    out = conn.execute("SELECT * FROM fundamentals ORDER BY ticker").df()
    assert set(out.columns) == {"ticker", "a", "b"}
    assert pd.isna(out.set_index("ticker").loc["AAA", "b"])  # old row backfilled NULL
    conn.close()
