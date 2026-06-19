"""Offline tests for the FRED/ALFRED macro adapter (synthetic fixtures, no network).

Pins the two things that matter: parsing ALFRED observations (including "." -> NaN), and
the point-in-time vintage logic in `pit.macro_asof` -- the value KNOWN on a date, not a
later revision (no macro lookahead).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from equity_research.config import Config
from equity_research.data import fred, pit, storage


def _gdp_payload() -> dict:
    # One series, two vintages for the 2026-01-01 observation (first 2.0, revised to 2.5),
    # a later 2026-02-01 observation (3.0), and a "." (missing) for 2026-02-15.
    return {"observations": [
        {"date": "2026-01-01", "realtime_start": "2026-01-15", "realtime_end": "2026-02-19",
         "value": "2.0"},
        {"date": "2026-01-01", "realtime_start": "2026-02-20", "realtime_end": "9999-12-31",
         "value": "2.5"},
        {"date": "2026-02-01", "realtime_start": "2026-02-25", "realtime_end": "9999-12-31",
         "value": "3.0"},
        {"date": "2026-02-15", "realtime_start": "2026-02-16", "realtime_end": "9999-12-31",
         "value": "."},
    ]}


def test_parse_observations_columns_and_missing():
    df = fred._parse_observations(_gdp_payload(), "GDP")
    assert list(df.columns) == ["series_id", "date", "value", "realtime_start", "realtime_end"]
    assert df["series_id"].unique().tolist() == ["GDP"]
    assert len(df) == 4
    # "." -> NaN, never fabricated.
    missing = df[df["date"] == pd.Timestamp("2026-02-15")]["value"].iloc[0]
    assert pd.isna(missing)
    # The revised vintage carries the new value.
    revised = df[df["realtime_start"] == pd.Timestamp("2026-02-20")]["value"].iloc[0]
    assert revised == 2.5


def test_parse_stamps_obs_date_for_non_revised():
    # Non-revised series pulled plain: realtime defaults to "today"; we rewrite the vintage to
    # the observation date (the rate of day d is knowable from day d on).
    payload = {"observations": [
        {"date": "2026-06-17", "realtime_start": "2026-06-19", "realtime_end": "2026-06-19",
         "value": "4.49"},
    ]}
    df = fred._parse_observations(payload, "DGS10", stamp_obs_date_vintage=True)
    assert df.iloc[0]["realtime_start"] == pd.Timestamp("2026-06-17")  # = obs date, not today
    assert df.iloc[0]["value"] == 4.49


@pytest.fixture
def macro_conn(tmp_path):
    c = storage.connect(Config(data_dir=tmp_path))
    storage.load_table(c, fred._parse_observations(_gdp_payload(), "GDP"), "macro")
    yield c
    c.close()


def test_macro_asof_uses_as_released_value_not_revision(macro_conn):
    # On 2026-02-01 only the FIRST release of the Jan observation is known (2.0); the 2.5
    # revision (realtime 2026-02-20) and the Feb obs (realtime 2026-02-25) are still future.
    res = pit.macro_asof(macro_conn, dt.date(2026, 2, 1))
    assert len(res) == 1
    assert res.iloc[0]["value"] == 2.0  # as-released, NOT the later 2.5 -> no lookahead
    assert pd.Timestamp(res.iloc[0]["date"]) == pd.Timestamp("2026-01-01")
    assert pd.Timestamp(res.iloc[0]["vintage"]) == pd.Timestamp("2026-01-15")


def test_macro_asof_advances_with_vintages(macro_conn):
    # By 2026-02-28 the Feb observation has been released (3.0) and is the latest known;
    # the missing "." obs for 2026-02-15 is skipped rather than returned.
    res = pit.macro_asof(macro_conn, dt.date(2026, 2, 28))
    assert res.iloc[0]["value"] == 3.0
    assert pd.Timestamp(res.iloc[0]["date"]) == pd.Timestamp("2026-02-01")


def test_macro_asof_unknown_series_is_empty(macro_conn):
    assert pit.macro_asof(macro_conn, dt.date(2026, 2, 28), series_ids=["NOPE"]).empty


def test_macro_asof_before_first_release_is_empty(macro_conn):
    # Nothing released on/before 2026-01-10 -> no rows (never a fabricated value).
    assert pit.macro_asof(macro_conn, dt.date(2026, 1, 10)).empty
