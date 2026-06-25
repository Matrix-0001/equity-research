"""Local Dash UI -- Phase A: the screen viewer (read-only over the loaded DuckDB).

Honesty-first chrome, by design:
  - the persistent disclaimer is ALWAYS visible (a banner, not a footnote);
  - data freshness (last pull per source) is shown -- freshness is part of accuracy;
  - `completeness` is surfaced and the composite is the same completeness-shrunk number the CLI uses,
    so a sparse-data name can't masquerade as a top pick;
  - it shows ALL the factor data (every group rank + composite + completeness), sortable/filterable;
  - it is a decision-support view, never an oracle -- it makes no recommendations.

Reads the DuckDB **read-only** (multi-user safe; many readers, no writer). Pulls/refreshes are done
from the CLI (`pull-universe`), not the UI. Run: `python -m equity_research.ui`.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, dash_table, dcc, html

from equity_research import factors
from equity_research.config import config
from equity_research.disclaimer import PERSISTENT_DISCLAIMER

_GROUPS = list(factors.FACTOR_GROUPS)               # value/quality/profitability/momentum/low_vol/size
_TABLE_COLS = ["ticker", "mcap_$B", *_GROUPS, "composite", "complete%", "n_metrics"]


def _ro_conn() -> duckdb.DuckDBPyConnection:
    """A read-only connection (so concurrent viewers never block each other or a CLI writer)."""
    return duckdb.connect(str(config.db_path), read_only=True)


def _freshness_text() -> str:
    try:
        c = _ro_conn()
        try:
            df = c.execute(
                "SELECT table_name, max(snapshot_date) d FROM data_snapshots GROUP BY table_name"
            ).df()
        finally:
            c.close()
    except Exception:
        return "data freshness: (no snapshot registry found -- run a pull from the CLI)"
    if df.empty:
        return "data freshness: no pulls recorded yet"
    parts = [f"{r['table_name']} {pd.Timestamp(r['d']).date()}" for _, r in df.iterrows()]
    return "Data last pulled -- " + " | ".join(parts)


def _screen(profile: str, min_complete: float) -> pd.DataFrame:
    c = _ro_conn()
    try:
        prof = factors.factor_profile(c, dt.date.today(), config, profile=profile)
    finally:
        c.close()
    if prof.empty:
        return prof
    prof = prof.reset_index()  # ticker -> column
    if min_complete:
        prof = prof[prof["completeness"] >= min_complete]
    prof["mcap_$B"] = (prof["market_cap"] / 1e9).round(2)
    prof["complete%"] = (prof["completeness"] * 100).round(0)
    for g in (*_GROUPS, "composite"):
        prof[g] = prof[g].round(0)
    return prof


_BANNER = {
    "background": "#FBEAF0", "color": "#72243E", "padding": "8px 14px",
    "borderRadius": "8px", "fontSize": "13px", "marginBottom": "10px",
}
_CARD = {"border": "0.5px solid #ddd", "borderRadius": "10px", "padding": "12px 16px",
         "marginBottom": "14px", "background": "white"}


def build_app() -> Dash:
    app = Dash(__name__)
    app.title = "Equity Research - local"
    app.layout = html.Div(
        style={"maxWidth": "1180px", "margin": "0 auto", "padding": "18px",
               "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"},
        children=[
            html.H2("Equity research - hidden-gem screen", style={"marginBottom": "2px"}),
            html.Div(id="freshness", style={"color": "#666", "fontSize": "12px",
                                            "marginBottom": "10px"}),
            html.Div(
                "Research only - not investment advice - this tool makes no recommendations. "
                "Ranks are within-universe percentiles, NOT predictions of return.",
                style=_BANNER,
            ),
            html.Div(style={"display": "flex", "gap": "18px", "flexWrap": "wrap",
                            "alignItems": "flex-end", **_CARD}, children=[
                html.Div([
                    html.Label("Composite profile", style={"fontSize": "12px", "color": "#555"}),
                    dcc.Dropdown(
                        id="profile",
                        options=[{"label": "balanced (equal weight)", "value": "balanced"},
                                 {"label": "potential (value+profitability+size; momentum light)",
                                  "value": "potential"}],
                        value="potential", clearable=False, style={"width": "330px"}),
                ]),
                html.Div([
                    html.Label("Min data completeness", style={"fontSize": "12px", "color": "#555"}),
                    dcc.Slider(id="min-complete", min=0, max=1, step=0.2, value=1.0,
                               marks={0: "0", 0.5: "50%", 1: "100%"}),
                ], style={"width": "260px"}),
                html.Div([
                    html.Label("Max market cap ($B, hidden-gem focus)",
                               style={"fontSize": "12px", "color": "#555"}),
                    dcc.Input(id="max-cap", type="number", value=0, min=0, step=1,
                              style={"width": "120px"}),
                    html.Span(" 0 = no cap", style={"fontSize": "11px", "color": "#999"}),
                ]),
            ]),
            html.Div(id="summary", style={"fontSize": "13px", "color": "#444",
                                          "marginBottom": "8px"}),
            html.Div(style=_CARD, children=[dcc.Graph(id="scatter", config={"displModeBar": False})]),
            html.Div(style=_CARD, children=[
                dash_table.DataTable(
                    id="table",
                    columns=[{"name": c, "id": c} for c in _TABLE_COLS],
                    sort_action="native", filter_action="native", page_size=25,
                    style_cell={"fontFamily": "monospace", "fontSize": "12px", "padding": "4px 8px",
                                "textAlign": "right"},
                    style_cell_conditional=[{"if": {"column_id": "ticker"},
                                             "textAlign": "left", "fontWeight": "bold"}],
                    style_header={"fontWeight": "bold", "backgroundColor": "#f5f5f5"},
                    style_data_conditional=[
                        {"if": {"filter_query": "{complete%} < 100"}, "color": "#999"},  # sparse = muted
                    ],
                ),
            ]),
            html.Div(
                f"composite is completeness-shrunk toward the neutral 50th percentile by the unmeasured "
                f"factor fraction; missing inputs read 'Not estimated', never a fabricated 0. "
                f"Sparse-data rows (complete% < 100) are muted. | {PERSISTENT_DISCLAIMER}",
                style={"fontSize": "11px", "color": "#888", "marginTop": "6px"},
            ),
        ],
    )

    @app.callback(
        Output("scatter", "figure"), Output("table", "data"), Output("summary", "children"),
        Output("freshness", "children"),
        Input("profile", "value"), Input("min-complete", "value"), Input("max-cap", "value"),
    )
    def _update(profile, min_complete, max_cap):
        df = _screen(profile, min_complete or 0.0)
        fresh = _freshness_text()
        if df.empty:
            empty = px.scatter(title="No data loaded -- run `pull-universe --index sp1500` first")
            return empty, [], "No universe loaded.", fresh
        if max_cap and max_cap > 0:
            df = df[df["mcap_$B"] <= max_cap]
        n = len(df)
        fig = px.scatter(
            df, x="mcap_$B", y="composite", color="complete%",
            hover_name="ticker",
            hover_data={g: True for g in _GROUPS} | {"mcap_$B": ":.2f", "complete%": False},
            color_continuous_scale="Tealgrn", log_x=True,
            labels={"mcap_$B": "market cap ($B, log)", "composite": "composite rank"},
        )
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=340,
                          coloraxis_colorbar=dict(title="data %"))
        fig.update_traces(marker=dict(size=7, opacity=0.7))
        summary = (f"{n} names shown | profile '{profile}' | the cheapest/best small names sit "
                   f"top-left (small cap, high composite).")
        return fig, df[_TABLE_COLS].to_dict("records"), summary, fresh

    return app


def main() -> None:
    app = build_app()
    print("Equity research UI -> http://127.0.0.1:8050  (read-only; Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=8050, debug=False)


if __name__ == "__main__":
    main()
