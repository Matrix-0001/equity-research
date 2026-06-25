"""Local Dash UI over the loaded DuckDB (read-only). Two tabs: Screen and Construct portfolio.

Honesty-first chrome, by design:
  - the persistent disclaimer is ALWAYS visible (a banner, not a footnote);
  - data freshness (last pull per source) is shown -- freshness is part of accuracy;
  - `completeness` is surfaced and the composite is the same completeness-shrunk number the CLI uses,
    so a sparse-data name can't masquerade as a top pick;
  - it shows ALL the data (factor ranks, full SecurityReport, portfolio weights/risk), and it is a
    decision-support view, never an oracle -- it makes no recommendations.

Reads the DuckDB **read-only** (multi-user safe; many readers, no writer). Pulls/refreshes are done
from the CLI (`pull-universe`), not the UI. Run: `python -m equity_research.ui`.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, dash_table, dcc, html, no_update

from equity_research import factors, portfolio, report
from equity_research.config import config
from equity_research.data import pit
from equity_research.disclaimer import PERSISTENT_DISCLAIMER

_GROUPS = list(factors.FACTOR_GROUPS)               # value/quality/profitability/momentum/low_vol/size
_TABLE_COLS = ["ticker", "company", "sector", "mcap_$B", *_GROUPS, "composite", "complete%"]
_WEIGHT_COLS = ["ticker", "company", "sector", "weight%", "risk_contrib%"]

_BANNER = {"background": "#FBEAF0", "color": "#72243E", "padding": "8px 14px",
           "borderRadius": "8px", "fontSize": "13px", "marginBottom": "10px"}
_CARD = {"border": "0.5px solid #ddd", "borderRadius": "10px", "padding": "12px 16px",
         "marginBottom": "14px", "background": "white"}
_LBL = {"fontSize": "12px", "color": "#555"}
_NOTE = {"fontSize": "11px", "color": "#888"}


# --------------------------------------------------------------------------- #
# Data access (read-only) + pure builders (unit-tested)
# --------------------------------------------------------------------------- #
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
        info = pit.security_info(c)                 # ticker -> company / sector
    finally:
        c.close()
    if prof.empty:
        return prof
    prof = prof.reset_index()  # ticker -> column
    if not info.empty:
        prof = prof.merge(info[["ticker", "company", "sector"]], on="ticker", how="left")
    for col, default in (("company", ""), ("sector", "Unknown")):
        prof[col] = prof[col].fillna(default) if col in prof.columns else default
    if min_complete:
        prof = prof[prof["completeness"] >= min_complete]
    prof["mcap_$B"] = (prof["market_cap"] / 1e9).round(2)
    prof["complete%"] = (prof["completeness"] * 100).round(0)
    for g in (*_GROUPS, "composite"):
        prof[g] = prof[g].round(0)
    return prof


def _sector_options() -> list[dict]:
    try:
        c = _ro_conn()
        try:
            df = c.execute("SELECT DISTINCT sector FROM securities WHERE sector IS NOT NULL "
                           "ORDER BY sector").df()
        finally:
            c.close()
    except Exception:
        return []
    return [{"label": s, "value": s} for s in df["sector"].tolist()]


def _company_options() -> list[dict]:
    """All names as 'TICKER - Company' for the searchable pickers; [] if not loaded."""
    try:
        c = _ro_conn()
        try:
            df = c.execute("SELECT ticker, company FROM securities ORDER BY ticker").df()
        finally:
            c.close()
    except Exception:
        return []
    return [{"label": f"{r.ticker} - {r.company}" if str(r.company) else str(r.ticker),
             "value": str(r.ticker)} for r in df.itertuples()]


def _scatter_figure(df: pd.DataFrame):
    """Market-cap (log) vs composite scatter, coloured by completeness, top names labelled."""
    fig = px.scatter(
        df, x="mcap_$B", y="composite", color="complete%", hover_name="ticker", text="label",
        hover_data={"company": True, "sector": True, **{g: True for g in _GROUPS},
                    "mcap_$B": ":.2f", "complete%": False, "label": False},
        color_continuous_scale="Tealgrn", log_x=True,
        labels={"mcap_$B": "market cap ($B, log)", "composite": "composite rank"},
    )
    fig.update_traces(marker=dict(size=8, opacity=0.7), textposition="top center",
                      textfont=dict(size=9))
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=420, dragmode="select",
                      coloraxis_colorbar=dict(title="data %"))
    return fig


def _select_rows(data: list[dict] | None, selected: dict | None) -> tuple[list[dict], str]:
    """Rows + caption for the table: the chart's box/lasso selection, else the whole current view."""
    if not data:
        return [], "No universe loaded -- run `pull-universe --index sp1500` first."
    n = len(data)
    if selected and selected.get("points"):
        idx = sorted({
            p.get("pointNumber", p.get("pointIndex")) for p in selected["points"]
            if isinstance(p.get("pointNumber", p.get("pointIndex")), int)
        })
        rows = [data[i] for i in idx if 0 <= i < n]
        return rows, (f"{len(rows)} companies selected on the chart (of {n} shown), ranked by "
                      f"composite. Double-click the chart to clear.")
    return data, (f"{n} names shown, ranked by composite. Tip: drag a box over dots on the chart "
                  f"to list just those companies here.")


def _report_card(ticker: str | None):
    """The full SecurityReport for one ticker as HTML (computed figures only; no LLM narrative)."""
    if not ticker:
        return html.Div("Pick a company above, or click a dot on the chart, to see its full report.",
                        style={"color": "#888", "fontSize": "13px"})
    c = _ro_conn()
    try:
        rep = report.build_security_report(c, dt.date.today(), ticker, config, use_llm=False)
    finally:
        c.close()
    label_st = {"color": "#666", "fontSize": "12px", "padding": "5px 12px 5px 0",
                "verticalAlign": "top", "textAlign": "left", "width": "120px"}
    val_st = {"fontSize": "13px", "padding": "5px 0", "textAlign": "left"}
    rows = [
        ("Confidence", rep.confidence_tier),
        ("Completeness", rep.data_completeness),
        ("Valuation", rep.valuation_read),
        ("Factor profile", rep.factor_profile),
        ("Quality / risk", rep.quality_risk_flags),
        ("Key risks", rep.key_risks_machine),
        ("Base rate", rep.historical_base_rate),
        ("What would change", rep.what_would_change_this),
    ]
    table = html.Table(
        [html.Tr([html.Td(lbl, style=label_st), html.Td(str(val), style=val_st)])
         for lbl, val in rows],
        style={"width": "100%", "borderCollapse": "collapse"},
    )
    return html.Div([
        html.H3(f"{rep.ticker} - {rep.company}", style={"margin": "0 0 8px"}),
        table,
        html.Div("Bull/bear narrative needs the optional LLM (off here); every figure above is "
                 f"computed. {PERSISTENT_DISCLAIMER}", style={**_NOTE, "marginTop": "8px"}),
    ])


def _weights_figure(rows: list[dict]):
    """Horizontal bar of portfolio weights (testable; built only when the callback runs)."""
    df = pd.DataFrame(rows).sort_values("weight%")
    fig = px.bar(df, x="weight%", y="ticker", orientation="h", color="weight%",
                 hover_data={"company": True, "sector": True, "weight%": ":.1f", "ticker": False},
                 color_continuous_scale="Tealgrn", labels={"weight%": "weight (%)", "ticker": ""})
    fig.update_layout(height=max(260, 24 * len(df)), margin=dict(l=10, r=10, t=10, b=10),
                      coloraxis_showscale=False)
    return fig


def _construct_notes(res) -> "html.Div":
    items = [html.Div(
        f"Method: {res.method} | selection: {res.selection or 'n/a'} | cov obs: {res.n_obs} | "
        f"value ${res.portfolio_value:,.0f}", style={**_LBL, "marginBottom": "8px"})]
    if res.sector_weights:
        items.append(html.Div("Sector exposure:", style={"fontWeight": "500", "fontSize": "13px"}))
        items += [html.Div(f"  {sec}: {wv * 100:.1f}%", style={"fontSize": "12px", "color": "#444"})
                  for sec, wv in res.sector_weights.items()]
    if res.caps_binding:
        items.append(html.Div(f"Caps binding: {', '.join(res.caps_binding)}",
                              style={"fontSize": "12px", "color": "#444", "marginTop": "6px"}))
    if res.dropped:
        items.append(html.Div("Dropped (insufficient data, never imputed):",
                              style={"fontSize": "12px", "fontWeight": "500", "marginTop": "6px"}))
        items += [html.Div(f"  {t}: {why}", style=_NOTE) for t, why in res.dropped.items()]
    items += [html.Div(f"- {n}", style={**_NOTE, "marginTop": "3px"}) for n in res.notes]
    if res.assumptions:
        items.append(html.Div("Expected returns (YOUR inputs, not computed):",
                              style={"fontSize": "12px", "fontWeight": "500", "marginTop": "6px"}))
        items += [html.Div(f"  {a}", style=_NOTE) for a in res.assumptions]
    items.append(html.Div(PERSISTENT_DISCLAIMER, style={**_NOTE, "marginTop": "8px"}))
    return html.Div(items)


def _construct_outputs(tickers, method, value):
    """(weights figure, weights table rows, notes) for the construct tab. Pure-ish (DB read-only)."""
    method = method or "hrp"
    value = float(value) if value else 1_000_000.0
    c = _ro_conn()
    try:
        res = portfolio.construct_portfolio(c, dt.date.today(), tickers=tickers or None,
                                            method=method, cfg=config, portfolio_value=value)
        held = list(res.weights.index) if res.weights is not None else []
        info = pit.security_info(c, held) if held else pit.security_info(c)
    finally:
        c.close()
    notes = _construct_notes(res)
    if res.weights is None or len(res.weights) == 0:
        return (px.bar(title="No portfolio yet -- pick >=2 names with price history, or leave empty "
                       "for the top-N by composite"), [], notes)
    nm = dict(zip(info["ticker"], info["company"])) if not info.empty else {}
    sm = dict(zip(info["ticker"], info["sector"])) if not info.empty else {}
    w, rc = res.weights, res.risk_contributions
    rows = []
    for t in w.index:
        rcv = (round(float(rc[t]) * 100, 1)
               if rc is not None and t in rc.index and pd.notna(rc[t]) else None)
        rows.append({"ticker": t, "company": nm.get(t, ""), "sector": sm.get(t, "Unknown"),
                     "weight%": round(float(w[t]) * 100, 1), "risk_contrib%": rcv})
    return _weights_figure(rows), rows, notes


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def _screen_tab():
    controls = html.Div(style={"display": "flex", "gap": "18px", "flexWrap": "wrap",
                               "alignItems": "flex-end", **_CARD}, children=[
        html.Div([html.Label("Composite profile", style=_LBL),
                  dcc.Dropdown(id="profile", clearable=False, value="potential", style={"width": "330px"},
                               options=[{"label": "balanced (equal weight)", "value": "balanced"},
                                        {"label": "potential (value+profitability+size; momentum light)",
                                         "value": "potential"}])]),
        html.Div([html.Label("Min data completeness", style=_LBL),
                  dcc.Slider(id="min-complete", min=0, max=1, step=0.2, value=1.0,
                             marks={0: "0", 0.5: "50%", 1: "100%"})], style={"width": "240px"}),
        html.Div([html.Label("Max market cap ($B)", style=_LBL),
                  dcc.Input(id="max-cap", type="number", value=0, min=0, step=1, style={"width": "110px"}),
                  html.Span(" 0 = no cap", style={"fontSize": "11px", "color": "#999"})]),
        html.Div([html.Label("Sectors", style=_LBL),
                  dcc.Dropdown(id="sector", options=_sector_options(), multi=True,
                               placeholder="All sectors", style={"width": "280px"})]),
    ])
    chart = html.Div(style={"flex": "1 1 58%", **_CARD}, children=[
        dcc.Graph(id="scatter",
                  config={"displayModeBar": True, "modeBarButtonsToRemove": ["autoScale2d"]}),
        html.Div("Top names are labelled. Drag a box (or lasso) over dots to list just those in the "
                 "table below; click a dot to load its report on the right; double-click to clear.",
                 style={**_NOTE, "marginTop": "4px"}),
    ])
    report_panel = html.Div(style={"flex": "1 1 40%", **_CARD}, children=[
        html.H3("Full report", style={"marginTop": "0", "marginBottom": "6px"}),
        dcc.Dropdown(id="report-ticker", options=_company_options(),
                     placeholder="Search a company / ticker...", style={"marginBottom": "10px"}),
        html.Div(id="report-card"),
    ])
    table = html.Div(style=_CARD, children=[dash_table.DataTable(
        id="table", columns=[{"name": c, "id": c} for c in ["rank", *_TABLE_COLS]],
        sort_action="native", filter_action="native", page_size=25, tooltip_duration=None,
        style_cell={"fontFamily": "monospace", "fontSize": "12px", "padding": "4px 8px",
                    "textAlign": "right"},
        style_cell_conditional=[
            {"if": {"column_id": "rank"}, "width": "44px", "color": "#888"},
            {"if": {"column_id": "ticker"}, "textAlign": "left", "fontWeight": "bold"},
            {"if": {"column_id": "company"}, "textAlign": "left", "maxWidth": "210px",
             "overflow": "hidden", "textOverflow": "ellipsis"},
            {"if": {"column_id": "sector"}, "textAlign": "left", "fontSize": "11px", "color": "#666"}],
        style_header={"fontWeight": "bold", "backgroundColor": "#f5f5f5"},
        style_data_conditional=[{"if": {"filter_query": "{complete%} < 100"}, "color": "#999"}],
    )])
    return html.Div([
        controls,
        html.Div(id="summary", style={"fontSize": "13px", "color": "#444", "marginBottom": "8px"}),
        dcc.Store(id="screen-data"),
        html.Div(style={"display": "flex", "gap": "14px", "alignItems": "flex-start"},
                 children=[chart, report_panel]),
        table,
        html.Div("composite is completeness-shrunk toward the neutral 50th percentile by the "
                 "unmeasured factor fraction; missing inputs read 'Not estimated', never a fabricated "
                 f"0. Sparse-data rows (complete% < 100) are muted. | {PERSISTENT_DISCLAIMER}",
                 style={**_NOTE, "marginTop": "6px"}),
    ])


def _construct_tab():
    controls = html.Div(style={"display": "flex", "gap": "18px", "flexWrap": "wrap",
                               "alignItems": "flex-end", **_CARD}, children=[
        html.Div([html.Label("Basket (leave empty = top 20 by composite)", style=_LBL),
                  dcc.Dropdown(id="basket", options=_company_options(), multi=True,
                               placeholder="Pick names you found on the Screen tab...",
                               style={"width": "470px"})]),
        html.Div([html.Label("Weighting method", style=_LBL),
                  dcc.Dropdown(id="method", clearable=False, value="hrp", style={"width": "190px"},
                               options=[{"label": m, "value": m}
                                        for m in ("hrp", "risk_parity", "inverse_variance")])]),
        html.Div([html.Label("Portfolio value ($)", style=_LBL),
                  dcc.Input(id="port-value", type="number", value=1_000_000, min=0, step=100000,
                            style={"width": "140px"})]),
    ])
    return html.Div([
        controls,
        html.Div(style={"display": "flex", "gap": "14px", "alignItems": "flex-start"}, children=[
            html.Div(style={"flex": "1 1 56%", **_CARD}, children=[
                html.Div("Risk-based weights (position / sector / liquidity caps applied)",
                         style={"fontSize": "13px", "fontWeight": "500", "marginBottom": "6px"}),
                dcc.Graph(id="weights-fig", config={"displayModeBar": False})]),
            html.Div(style={"flex": "1 1 42%", **_CARD}, children=[html.Div(id="construct-notes")]),
        ]),
        html.Div(style=_CARD, children=[dash_table.DataTable(
            id="weights-table", columns=[{"name": c, "id": c} for c in _WEIGHT_COLS],
            sort_action="native", page_size=30,
            style_cell={"fontFamily": "monospace", "fontSize": "12px", "padding": "4px 8px",
                        "textAlign": "right"},
            style_cell_conditional=[
                {"if": {"column_id": "ticker"}, "textAlign": "left", "fontWeight": "bold"},
                {"if": {"column_id": "company"}, "textAlign": "left", "maxWidth": "230px",
                 "overflow": "hidden", "textOverflow": "ellipsis"},
                {"if": {"column_id": "sector"}, "textAlign": "left", "fontSize": "11px",
                 "color": "#666"}],
            style_header={"fontWeight": "bold", "backgroundColor": "#f5f5f5"},
        )]),
        html.Div("Weights need no return forecast (risk-based on a point-in-time covariance); names "
                 f"with too little history are dropped, never imputed. {PERSISTENT_DISCLAIMER}",
                 style={**_NOTE, "marginTop": "6px"}),
    ])


def build_app() -> Dash:
    app = Dash(__name__, suppress_callback_exceptions=True)   # tabs render one panel at a time
    app.title = "Equity Research - local"
    app.layout = html.Div(
        style={"maxWidth": "1380px", "margin": "0 auto", "padding": "18px",
               "fontFamily": "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"},
        children=[
            html.H2("Equity research - local", style={"marginBottom": "2px"}),
            html.Div(_freshness_text(), style={"color": "#666", "fontSize": "12px",
                                               "marginBottom": "10px"}),
            html.Div("Research only - not investment advice - this tool makes no recommendations. "
                     "Ranks are within-universe percentiles, NOT predictions of return.", style=_BANNER),
            dcc.Tabs(id="tabs", value="screen", children=[
                dcc.Tab(label="Screen", value="screen", children=[_screen_tab()]),
                dcc.Tab(label="Construct portfolio", value="construct", children=[_construct_tab()]),
            ]),
        ],
    )

    @app.callback(
        Output("scatter", "figure"), Output("screen-data", "data"),
        Input("profile", "value"), Input("min-complete", "value"), Input("max-cap", "value"),
        Input("sector", "value"),
    )
    def _update_chart(profile, min_complete, max_cap, sectors):
        df = _screen(profile, min_complete or 0.0)
        if df.empty:
            return px.scatter(title="No data loaded -- run `pull-universe --index sp1500` first"), []
        if sectors:
            df = df[df["sector"].isin(sectors)]
        if max_cap and max_cap > 0:
            df = df[df["mcap_$B"] <= max_cap]
        if df.empty:
            return px.scatter(title="No names match these filters"), []
        df = df.sort_values("composite", ascending=False).reset_index(drop=True)
        df["rank"] = df.index + 1
        df["label"] = ""
        top = min(15, len(df))
        df.loc[df.index < top, "label"] = df.loc[df.index < top, "ticker"]
        return _scatter_figure(df), df.to_dict("records")

    @app.callback(
        Output("table", "data"), Output("table", "tooltip_data"), Output("summary", "children"),
        Input("screen-data", "data"), Input("scatter", "selectedData"),
    )
    def _update_table(data, selected):
        rows, note = _select_rows(data, selected)
        if not rows:
            return [], [], note
        df = pd.DataFrame(rows)
        tips = [{"company": {"value": str(c), "type": "text"}} for c in df["company"]]
        return df[["rank", *_TABLE_COLS]].to_dict("records"), tips, note

    @app.callback(Output("report-ticker", "value"), Input("scatter", "clickData"))
    def _click_to_report(click):
        if click and click.get("points"):
            tkr = click["points"][0].get("hovertext")
            if tkr:
                return tkr
        return no_update

    @app.callback(Output("report-card", "children"), Input("report-ticker", "value"))
    def _render_report(ticker):
        return _report_card(ticker)

    @app.callback(
        Output("weights-fig", "figure"), Output("weights-table", "data"),
        Output("construct-notes", "children"),
        Input("basket", "value"), Input("method", "value"), Input("port-value", "value"),
    )
    def _update_construct(basket, method, value):
        return _construct_outputs(basket, method, value)

    return app


def main() -> None:
    app = build_app()
    print("Equity research UI -> http://127.0.0.1:8050  (read-only; Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=8050, debug=False)


if __name__ == "__main__":
    main()
