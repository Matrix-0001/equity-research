"""Offline tests for survivorship-free index membership (fja05680 windows) + universe."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from equity_research import universe
from equity_research.config import Config
from equity_research.data import index_membership as im
from equity_research.data import storage


# --------------------------------------------------------------------------- #
# fja05680 membership windows
# --------------------------------------------------------------------------- #
def _fja_frame() -> pd.DataFrame:
    # Mirrors fja05680/sp500 ticker_start_end.csv, incl. a multi-stint ticker (AAL) and a current
    # member (A, blank end_date) and a delisted one (AAMRQ).
    return pd.DataFrame({
        "ticker": ["A", "AAL", "AAL", "AAMRQ", "ABNB"],
        "start_date": ["2000-06-05", "1996-01-02", "2015-03-23", "1996-01-02", "2023-09-18"],
        "end_date": ["", "1997-01-15", "2024-09-23", "2003-03-14", ""],
    })


def test_normalize_windows_parses_dates_and_blank_end():
    mem = im.normalize_windows(_fja_frame())
    assert len(mem) == 5                                   # one row per window (AAL appears twice)
    aal = mem[mem["ticker"] == "AAL"].sort_values("start_date")
    assert pd.Timestamp(aal.iloc[0]["start_date"]) == pd.Timestamp("1996-01-02")
    assert pd.Timestamp(aal.iloc[0]["end_date"]) == pd.Timestamp("1997-01-15")
    a = mem[mem["ticker"] == "A"].iloc[0]
    assert pd.isna(a["end_date"])                          # blank end_date -> NaT (still a member)


def test_normalize_windows_drops_rows_without_ticker_or_start():
    bad = pd.DataFrame({"ticker": ["", "ZZZ"], "start_date": ["2010-01-01", ""],
                        "end_date": ["", ""]})
    mem = im.normalize_windows(bad)
    assert mem.empty                                       # both rows invalid -> dropped, not imputed


def test_survivorship_universe_point_in_time_windows(tmp_path):
    cfg = Config(data_dir=tmp_path)
    c = storage.connect(cfg)
    try:
        storage.load_table(c, im.normalize_windows(_fja_frame()), "index_membership")
        u1996 = universe.survivorship_universe(c, dt.date(1996, 6, 30))
        u2010 = universe.survivorship_universe(c, dt.date(2010, 6, 30))
        u2024c = universe.survivorship_universe(c, dt.date(2024, 12, 31))
    finally:
        c.close()
    # 1996: AAL (1996-97 stint) + AAMRQ (in until 2003). Not A (joined 2000), not ABNB (2023).
    assert set(u1996) == {"AAL", "AAMRQ"}
    # 2010: A (current), AAMRQ delisted 2003 -> out, AAL out of index 1997-2015 -> out.
    assert "A" in u2010 and "AAMRQ" not in u2010 and "AAL" not in u2010
    # End of 2024: AAL left index 2024-09-23 -> out; A + ABNB current.
    assert "AAL" not in u2024c and {"A", "ABNB"}.issubset(set(u2024c))


def test_survivorship_universe_absent_table_returns_empty(tmp_path):
    cfg = Config(data_dir=tmp_path)
    c = storage.connect(cfg)
    try:
        assert universe.survivorship_universe(c, dt.date(2020, 1, 1)) == []
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# Optional delist-reason enrichment (Norgate-style export)
# --------------------------------------------------------------------------- #
def test_parse_symbol_current_and_delisted():
    assert im.parse_symbol("AAPL") == ("AAPL", None, None)
    assert im.parse_symbol("BRK.B") == ("BRK.B", None, None)   # share class, not a delisting
    base, d, reason = im.parse_symbol("AAMRQ.20131206.C")
    assert base == "AAMRQ" and d == dt.date(2013, 12, 6) and "bankrupt" in reason
    base, d, reason = im.parse_symbol("ABI.20081121.A")
    assert base == "ABI" and d == dt.date(2008, 11, 21) and "acquired" in reason
    base, d, reason = im.parse_symbol("FOO.20150101.Z")
    assert base == "FOO" and "code Z" in reason                # unknown code passed through


def test_delist_reasons_from_export():
    raw = pd.DataFrame({"Symbol": ["AAPL", "AAMRQ.20131206.C", "ABI.20081121.A"]})
    out = im.delist_reasons(raw).set_index("ticker")
    assert set(out.index) == {"AAMRQ", "ABI"}                 # only delisted rows
    assert pd.Timestamp(out.loc["AAMRQ", "delist_date"]) == pd.Timestamp("2013-12-06")
