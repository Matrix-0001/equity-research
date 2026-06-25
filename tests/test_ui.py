"""Smoke test for the optional Dash UI (skipped when the `[ui]` extra isn't installed)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

pytest.importorskip("dash")  # UI is an optional extra; skip if dash isn't installed

from equity_research.disclaimer import (  # noqa: E402
    INDICATIVE_STAMP, BacktestCompare, BacktestResult,
)
from equity_research.ui.app import (  # noqa: E402
    _GROUPS, _company_header, _compare_figure, _compare_view, _filings_block, _fmt_fye, _media_block,
    _meta, _nav_figure, _report_block, _result_view, _ro_conn_for_lane, _scatter_figure,
    _select_rows, _simfin_db_path, _slow_combo_warning, _stamp_banner, _statement_payload,
    _weights_figure, build_app,
)


def test_weights_figure_builds_without_error():
    rows = [{"ticker": "AA", "company": "A Inc", "sector": "IT", "weight%": 40.0,
             "risk_contrib%": 35.0},
            {"ticker": "BB", "company": "B Inc", "sector": "Health", "weight%": 60.0,
             "risk_contrib%": 65.0}]
    fig = _weights_figure(rows)
    assert fig is not None and len(fig.data) >= 1


def test_scatter_figure_builds_without_error():
    # Building the figure is where a bad Plotly property (e.g. cliponaxis) would only surface at
    # request time and 500 the callback -> empty view. Pin that it builds from a minimal frame.
    df = pd.DataFrame({
        "mcap_$B": [1.0, 2.5], "composite": [80.0, 70.0], "complete%": [100.0, 80.0],
        "ticker": ["AA", "BB"], "company": ["Alpha Inc", "Beta Corp"], "sector": ["IT", "Health"],
        "label": ["AA", ""], **{g: [50.0, 60.0] for g in _GROUPS},
    })
    fig = _scatter_figure(df)
    assert fig is not None and len(fig.data) >= 1


def test_ui_app_builds_with_honesty_chrome():
    app = build_app()
    assert app.title == "Equity Research - local"
    assert app.layout is not None
    # The persistent disclaimer / honesty framing is baked into the layout, not optional, and both
    # tabs (Screen + Construct) are present.
    rendered = str(app.layout)
    assert "not investment advice" in rendered
    assert "Construct portfolio" in rendered
    assert "Company" in rendered          # the unified Yahoo-style page (report + financials + news)


def test_select_rows_follows_chart_selection():
    data = [{"rank": i + 1, "ticker": f"T{i}"} for i in range(5)]
    # No selection -> the whole current view.
    rows, note = _select_rows(data, None)
    assert len(rows) == 5 and "5 names shown" in note
    # Box-selecting two dots -> just those two companies, in rank order.
    sel = {"points": [{"pointNumber": 3}, {"pointNumber": 1}]}
    rows, note = _select_rows(data, sel)
    assert [r["ticker"] for r in rows] == ["T1", "T3"]   # sorted by point index (rank order)
    assert "2 companies selected" in note
    # Empty universe.
    rows, note = _select_rows([], None)
    assert rows == [] and "No universe" in note


# --------------------------------------------------------------------------- #
# Backtest tab -- figure/view builders (the place a bad Plotly prop or a missing
# field would only 500 at request time; pin that they build + carry honesty chrome)
# --------------------------------------------------------------------------- #
def _sample_result(nav=True) -> BacktestResult:
    curve = ([(dt.date(2020, 1, 31), 1_000_000.0), (dt.date(2020, 2, 29), 1_010_000.0),
              (dt.date(2020, 3, 31), 980_000.0)] if nav else [])
    return BacktestResult(
        strategy="hrp", lane="free", valid=False, start=dt.date(2020, 1, 31),
        end=dt.date(2020, 3, 31), rebalance="monthly", n_periods=2,
        initial_capital=1_000_000.0, final_nav=980_000.0, cagr_net=-0.08, sharpe_net=0.3,
        sharpe_se=0.5, nav_curve=curve,
    )


def _sample_compare() -> BacktestCompare:
    rows = [
        {"strategy": "hrp", "is_n": 10, "is_cagr": 0.10, "is_sharpe": 0.8, "dsr": 0.62,
         "oos_n": 3, "oos_cagr": 0.05, "oos_sharpe": 0.4},
        {"strategy": "equal_weight", "is_n": 10, "is_cagr": 0.08, "is_sharpe": 0.6, "dsr": 0.41,
         "oos_n": 3, "oos_cagr": 0.03, "oos_sharpe": 0.2},
    ]
    return BacktestCompare(
        lane="free", stamp=INDICATIVE_STAMP, start=dt.date(2018, 1, 31), end=dt.date(2020, 3, 31),
        rebalance="monthly", periods_per_year=12, locked_test_start=None, n_trials=2,
        sr_variance=0.001, rows=rows, selected="hrp",
    )


def test_nav_figure_builds_from_curve_and_when_empty():
    fig = _nav_figure(_sample_result(nav=True))
    assert fig is not None and len(fig.data) >= 1
    # An empty NAV path must still build a frame (it reads "No NAV path"), never crash the callback.
    empty = _nav_figure(_sample_result(nav=False))
    assert empty is not None


def test_compare_figure_builds_from_rows():
    fig = _compare_figure(_sample_compare())
    assert fig is not None and len(fig.data) >= 1


def test_result_view_carries_loud_stamp_and_disclaimer():
    rendered = str(_result_view(_sample_result()))
    # The lane/validity stamp and the persistent disclaimer are non-negotiable honesty chrome.
    assert "INDICATIVE ONLY" in rendered
    assert "Not investment advice" in rendered
    assert "free" in rendered


def test_compare_view_carries_stamp_and_selection():
    rendered = str(_compare_view(_sample_compare()))
    assert "INDICATIVE ONLY" in rendered
    assert "Deflated Sharpe" in rendered
    assert "Not investment advice" in rendered


def test_stamp_banner_warns_when_not_valid():
    rendered = str(_stamp_banner("free", False, INDICATIVE_STAMP))
    assert "INDICATIVE ONLY" in rendered
    valid = str(_stamp_banner("simfin", True, INDICATIVE_STAMP))
    assert "VALID" in valid


def test_backtest_tab_present_in_app():
    rendered = str(build_app().layout)
    assert "Backtest" in rendered
    assert "Run backtest" in rendered


# --------------------------------------------------------------------------- #
# Per-lane data dir: the free lane uses the main DB, simfin reads its own dir so
# ONE UI process can serve both without mixing Yahoo + SimFin prices.
# --------------------------------------------------------------------------- #
def test_simfin_db_path_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EQR_SIMFIN_DATA_DIR", str(tmp_path))
    p = _simfin_db_path()
    assert p.parent == tmp_path.resolve()
    assert p.name.endswith(".duckdb")


def test_simfin_db_path_defaults_to_sibling(monkeypatch):
    monkeypatch.delenv("EQR_SIMFIN_DATA_DIR", raising=False)
    # Default is a `data_simfin` sibling of the free-lane data dir (never the free dir itself).
    assert _simfin_db_path().parent.name == "data_simfin"


def test_ro_conn_for_lane_simfin_missing_raises_clearly(monkeypatch, tmp_path):
    # Point at an empty dir (no DB file) -> a clear, actionable error, never a silent 0-name run.
    monkeypatch.setenv("EQR_SIMFIN_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError) as exc:
        _ro_conn_for_lane("simfin")
    assert "simfin" in str(exc.value) and "pull-simfin" in str(exc.value)


# --------------------------------------------------------------------------- #
# Company tab -- statement-table payload + metadata header builders.
# --------------------------------------------------------------------------- #
def test_statement_payload_columns_data_and_not_estimated():
    periods = [dt.date(2024, 9, 30), dt.date(2023, 9, 30)]
    rows = [("Revenue", "usd", [120e9, 100e9]),
            ("Gross profit", "usd", [None, None]),          # missing tag
            ("Diluted EPS", "per_share", [6.5, 6.0])]
    cols, data = _statement_payload(rows, periods)
    assert [c["name"] for c in cols] == ["Line item", "2024", "2023"]
    assert data[0]["item"] == "Revenue" and data[0]["y0"] == "120,000"   # $ millions
    assert data[1]["y0"] == "Not estimated" and data[1]["y1"] == "Not estimated"  # never "0"
    assert data[2]["y0"] == "6.50"                                       # per-share


def test_fmt_fye_formats_or_passes_through():
    assert _fmt_fye("0927") == "Sep 27"
    assert _fmt_fye("1231") == "Dec 31"
    assert _fmt_fye("") == ""        # blank stays blank -> _meta renders 'Not estimated'


def test_meta_shows_not_estimated_when_blank():
    assert "Not estimated" in str(_meta("Sector", ""))
    assert "Technology" in str(_meta("Sector", "Technology"))


def test_filings_block_renders_rows_and_shareable_stamp():
    import pandas as pd
    df = pd.DataFrame([{"filed_date": "2026-05-01", "form": "8-K", "description": "Results",
                        "url": "https://www.sec.gov/x"}])
    rendered = str(_filings_block(df))
    assert "Recent SEC filings" in rendered and "8-K" in rendered
    assert "freely shareable" in rendered          # SEC filings are the shareable half
    # Empty -> a clear "run pull-profiles" hint, never a fabricated filing.
    assert "pull-profiles" in str(_filings_block(pd.DataFrame()))


def test_media_block_states_and_private_stamp():
    items = [{"title": "Apple unveils a chip", "source": "Reuters", "published": "Fri",
              "url": "https://news/x"}]
    ok = str(_media_block(items, error=False))
    assert "Apple unveils a chip" in ok and "Reuters" in ok
    assert "keep private" in ok.lower() or "PERSONAL use" in ok   # redistribution caveat
    assert "unavailable" in str(_media_block([], error=True))     # graceful failure
    assert "No recent headlines" in str(_media_block([], error=False))


def test_report_block_renders_computed_rows_and_disclaimer():
    from equity_research.disclaimer import SecurityReport
    rep = SecurityReport(ticker="AAPL", company="Apple Inc.", confidence_tier="High",
                         valuation_read="implied growth 13-18%/yr")
    rendered = str(_report_block(rep))
    assert "Research report" in rendered
    assert "High" in rendered and "implied growth 13-18%/yr" in rendered
    assert "Not estimated" in rendered          # unpopulated fields stay honest, never fabricated
    assert "Not investment advice" in rendered


def test_company_header_carries_name_metadata_and_honesty_caveats():
    from equity_research.statements import FinancialStatements
    fs = FinancialStatements("AAPL", "Apple Inc.", dt.date(2026, 1, 1))
    prof = {"company": "Apple Inc.", "exchanges": "Nasdaq", "state_of_incorp": "CA",
            "fiscal_year_end": "0927", "first_filing_date": "2015-05-29", "former_names": "",
            "hq_city": "cupertino", "hq_state": "CA", "entity_type": "operating"}
    rendered = str(_company_header(fs, prof, {"company": "Apple Inc.", "sector": "Technology"}))
    assert "Apple Inc." in rendered and "Nasdaq" in rendered and "Sep 27" in rendered
    assert "lower bound" in rendered                 # the "public since" honesty caveat
    assert "not in EDGAR" in rendered                # founding/description honestly disclaimed


def test_slow_combo_warning_only_fires_for_simfin_hrp_or_risk_parity():
    # The free lane is always fast (top-N basket) -> never warns, whatever the method.
    assert _slow_combo_warning("free", "single", "hrp", None) == ""
    # simfin + a heavy method (single or in a compare set) -> warns about the ~500-name construction.
    assert "MANY MINUTES" in _slow_combo_warning("simfin", "single", "hrp", None)
    assert "MANY MINUTES" in _slow_combo_warning("simfin", "single", "risk_parity", None)
    assert "MANY MINUTES" in _slow_combo_warning(
        "simfin", "compare", None, ["equal_weight", "hrp"])
    # simfin + only the fast methods -> no warning.
    assert _slow_combo_warning("simfin", "single", "equal_weight", None) == ""
    assert _slow_combo_warning("simfin", "compare", None,
                               ["equal_weight", "inverse_variance"]) == ""
