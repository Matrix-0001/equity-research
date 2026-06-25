"""Smoke test for the optional Dash UI (skipped when the `[ui]` extra isn't installed)."""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("dash")  # UI is an optional extra; skip if dash isn't installed

from equity_research.ui.app import _GROUPS, _scatter_figure, _select_rows, build_app  # noqa: E402


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
    # The persistent disclaimer / honesty framing is baked into the layout, not optional.
    rendered = str(app.layout)
    assert "not investment advice" in rendered
    assert "hidden-gem screen" in rendered


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
