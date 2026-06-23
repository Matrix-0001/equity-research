"""Offline tests for the SimFin price adapter parser (synthetic v3 compact payload)."""

from __future__ import annotations

import pandas as pd
import pytest

from equity_research.config import Config
from equity_research.data import simfin, storage

# Shape of a real SimFin v3 /companies/prices/compact response (one company record with a
# columns header + row-major data). Includes a delisted-style final near-zero close.
_PAYLOAD = [{
    "id": 708168,
    "ticker": "SIVB",
    "columns": ["Date", "Dividend Paid", "Common Shares Outstanding", "Last Closing Price",
                "Adjusted Closing Price", "Highest Price", "Lowest Price", "Opening Price",
                "Trading Volume"],
    "data": [
        ["2023-03-08", None, 59000000, 267.83, 267.83, 270.0, 265.0, 268.0, 1200000],
        ["2023-03-09", None, 59000000, 106.04, 106.04, 176.0, 105.0, 175.0, 9000000],
        ["2024-11-07", None, 59112507, 0.01, 0.01, 0.01, 0.01, 0.01, 934],
    ],
}]


def test_parse_prices_maps_columns_to_schema():
    df = simfin._parse_prices(_PAYLOAD, "SIVB")
    assert list(df.columns) == ["ticker", "date", "closeadj", "closeunadj", "volume",
                                "shares_outstanding"]
    assert (df["ticker"] == "SIVB").all()
    assert len(df) == 3
    last = df.iloc[-1]
    assert pd.Timestamp(last["date"]) == pd.Timestamp("2024-11-07")
    assert last["closeadj"] == pytest.approx(0.01)        # real delisted collapse, not a gap
    assert last["closeunadj"] == pytest.approx(0.01)
    assert df.iloc[0]["shares_outstanding"] == pytest.approx(59_000_000)


def test_parse_prices_drops_rows_without_adjusted_close():
    payload = [{
        "ticker": "X",
        "columns": ["Date", "Adjusted Closing Price", "Last Closing Price", "Trading Volume"],
        "data": [["2020-01-02", 10.0, 10.0, 100], ["2020-01-03", None, 11.0, 120]],
    }]
    df = simfin._parse_prices(payload, "X")
    assert len(df) == 1                                   # the None-adjclose row is dropped
    assert pd.Timestamp(df.iloc[0]["date"]) == pd.Timestamp("2020-01-02")


def test_parse_prices_empty_and_illformed_payloads():
    assert simfin._parse_prices([], "X").empty
    assert simfin._parse_prices({}, "X").empty
    assert simfin._parse_prices([{"ticker": "X", "columns": [], "data": []}], "X").empty


def test_parse_prices_uses_record_ticker_over_arg():
    df = simfin._parse_prices(_PAYLOAD, "wrong")
    assert (df["ticker"] == "SIVB").all()                 # trusts the payload's own ticker


def _row(t):
    return pd.DataFrame({"ticker": [t], "date": [pd.Timestamp("2020-01-02")], "closeadj": [5.0],
                         "closeunadj": [5.0], "volume": [1], "shares_outstanding": [1]})


def test_ingest_skips_existing_and_stops_gracefully_on_rate_limit(tmp_path, monkeypatch):
    # Resumability: a name already in `prices` is skipped; a persistent 429 stops the run gracefully
    # (rate_limited=True) rather than raising, so re-running resumes. No network (fetch is patched).
    cfg = Config(data_dir=tmp_path)
    c = storage.connect(cfg)
    storage.load_table(c, _row("AAA"), "prices")          # AAA already pulled -> should be skipped
    c.close()

    calls: list[str] = []

    def fake_fetch(ticker, cfg=None, start=None, **kw):
        calls.append(ticker)
        if ticker == "RL":
            raise simfin.SimFinRateLimit("429")
        return _row(ticker)

    monkeypatch.setattr(simfin, "fetch_prices", fake_fetch)
    monkeypatch.setattr(simfin.time, "sleep", lambda *_a, **_k: None)

    s = simfin.ingest(["AAA", "BBB", "RL", "CCC"], cfg=cfg)
    assert s["skipped"] == 1 and "AAA" not in calls        # existing name skipped, not refetched
    assert s["rate_limited"] is True                       # hit the 429 -> stopped gracefully
    assert "BBB" in s["tickers_resolved"]
    assert "CCC" not in calls                              # stopped at RL; CCC left for a re-run
