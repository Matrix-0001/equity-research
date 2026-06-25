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
import os
from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update

from equity_research import backtest, factors, portfolio, report
from equity_research.config import BacktestConfig, config
from equity_research.data import pit
from equity_research.disclaimer import PERSISTENT_DISCLAIMER
from equity_research.ui.jobs import ERROR, MANAGER

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
# Backtest tab -- background jobs (a run is ~25-35s, far too slow for a web request)
# --------------------------------------------------------------------------- #
_STAMP_OK = {"background": "#EAF6EE", "color": "#1E6B3A", "border": "1px solid #BFE3CC"}
_STAMP_WARN = {"background": "#FBEAEA", "color": "#8A2326", "border": "1px solid #E8B7B7"}


def _nav_figure(result):
    """Net-of-cost NAV path for a single backtest (empty curve -> an honest empty frame, no fake line)."""
    curve = list(getattr(result, "nav_curve", []) or [])
    if not curve:
        return px.line(title="No NAV path -- the universe was empty over this window (see notes)")
    df = pd.DataFrame(curve, columns=["date", "nav"])
    fig = px.line(df, x="date", y="nav", labels={"date": "", "nav": "NAV ($, net of costs)"})
    fig.update_traces(line=dict(width=2))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
    return fig


def _compare_figure(compare):
    """In-sample Deflated-Sharpe per strategy (the SELECTION metric), winner highlighted.

    DSR (not raw Sharpe) is what `compare_backtests` selects on, so the chart shows exactly the
    multiple-testing-corrected number used to pick -- never a naive best-Sharpe race.
    """
    rows = [r for r in getattr(compare, "rows", []) if pd.notna(r.get("dsr"))]
    if not rows:
        return px.bar(title="No deflated Sharpe to plot (insufficient in-sample data on every run)")
    df = pd.DataFrame([
        {"strategy": r["strategy"], "dsr%": round(float(r["dsr"]) * 100, 1),
         "selected": r["strategy"] == getattr(compare, "selected", "")}
        for r in rows
    ]).sort_values("dsr%")
    fig = px.bar(df, x="dsr%", y="strategy", orientation="h", color="selected",
                 color_discrete_map={True: "#1E6B3A", False: "#9CC3AD"},
                 labels={"dsr%": "in-sample Deflated Sharpe = P(true SR > best-of-N)  (%)",
                         "strategy": ""})
    fig.update_layout(height=max(240, 34 * len(df)), margin=dict(l=10, r=10, t=10, b=10),
                      showlegend=False)
    return fig


def _stamp_banner(lane: str, valid: bool, stamp: str):
    """The loud lane/validity stamp -- honesty chrome that must never be a footnote."""
    style = _STAMP_OK if valid else _STAMP_WARN
    text = "VALID (survivorship-free)" if valid else stamp
    return html.Div(f"Data lane: {lane}   |   {text}",
                    style={**style, "padding": "8px 14px", "borderRadius": "8px",
                           "fontSize": "12px", "fontWeight": "600", "marginBottom": "10px"})


def _render_block(text: str):
    """The audited honest render() text verbatim (stamp, costs, ranges, regimes, lock, notes, disclaimer).

    Rendering the dataclass's own render() means the UI cannot drop or soften any honesty content --
    it shows exactly what the CLI shows, byte for byte.
    """
    return html.Pre(text, style={"fontFamily": "monospace", "fontSize": "12px",
                                 "whiteSpace": "pre-wrap", "lineHeight": "1.45",
                                 "background": "#FAFAFA", "border": "0.5px solid #eee",
                                 "borderRadius": "8px", "padding": "12px 14px", "margin": "0",
                                 "maxHeight": "560px", "overflowY": "auto"})


def _result_view(result):
    """Children for a completed single backtest: loud stamp + the full audited render() text."""
    return [_stamp_banner(result.lane, result.valid, result.stamp), _render_block(result.render())]


def _compare_view(compare):
    """Children for a completed compare run: loud stamp + the full audited render() text."""
    return [_stamp_banner(compare.lane, compare.valid, compare.stamp),
            _render_block(compare.render())]


# Methods whose construction cost is heavy on a large basket (HRP single-linkage is O(n^3); ERC is
# iterative). On the free lane the basket is the top-N by composite (small), so this never bites; on
# the simfin lane there is no composite to rank DELISTED names, so the basket is the whole ~500-name
# index membership -- and a long monthly run of these can take many minutes.
_SLOW_METHODS = ("hrp", "risk_parity")


def _slow_combo_warning(lane, mode, strategy, strategies) -> str:
    """A soft heads-up string for slow simfin combos (empty when the run will be quick). Pure/tested."""
    if lane != "simfin":
        return ""
    chosen = list(strategies or []) if mode == "compare" else ([strategy] if strategy else [])
    slow = sorted({s for s in chosen if s in _SLOW_METHODS})
    if not slow:
        return ""
    return (f"Heads up: on the simfin lane there is no factor composite to rank delisted names, so "
            f"{'/'.join(slow)} constructs over the FULL ~500-name index membership every rebalance "
            f"(HRP clustering is O(n^3)) -- a long monthly run can take MANY MINUTES. equal_weight "
            f"and inverse_variance keep all names and finish in seconds; or shorten the window / use "
            f"quarterly rebalance.")


def _bt_config(rebalance: str, lock: dt.date | None) -> BacktestConfig:
    """A BacktestConfig with the UI-chosen cadence + optional locked-test boundary (rest = defaults)."""
    return replace(config.backtest, rebalance=(rebalance or "monthly"), locked_test_start=lock)


def _parse_date(value, default: dt.date | None = None) -> dt.date | None:
    if not value:
        return default
    return dt.date.fromisoformat(str(value)[:10])


def _simfin_db_path() -> Path:
    """Where the survivorship-free (simfin) lane's DuckDB lives -- a SEPARATE dir from the free lane.

    SimFin and Yahoo prices must never share a data dir (recycled tickers / source collisions), so
    the simfin lane reads its own DB. Override with EQR_SIMFIN_DATA_DIR; the default is a
    `data_simfin` sibling of the free lane's data dir (`pull-index-history` + `pull-simfin` target).
    """
    override = os.getenv("EQR_SIMFIN_DATA_DIR")
    base = Path(override).resolve() if override else (config.data_dir.parent / "data_simfin")
    return base / config.db_path.name


def _ro_conn_for_lane(lane: str) -> duckdb.DuckDBPyConnection:
    """Read-only connection for a backtest lane: free uses the main DB, simfin its own dir.

    This lets ONE UI process serve both lanes: the Screen/Construct/free-backtest surfaces keep
    reading the full free-lane DB (universe + fundamentals), while a simfin run transparently reads
    the survivorship-free DB (PIT index membership + delisted prices). A missing simfin DB raises a
    clear, actionable error (surfaced as the job's failure) rather than silently returning 0 names.
    """
    if lane == "simfin":
        p = _simfin_db_path()
        if not p.exists():
            raise FileNotFoundError(
                f"simfin lane has no data dir (looked for {p}). Build it once: set EQR_DATA_DIR to a "
                f"dedicated dir, run 'pull-index-history' then 'pull-simfin', or point "
                f"EQR_SIMFIN_DATA_DIR at an existing one."
            )
        return duckdb.connect(str(p), read_only=True)
    return _ro_conn()


def _run_single_thunk(strategy, start, end, rebalance, lane, lock):
    """A zero-arg thunk the job worker runs; it opens its OWN read-only connection in that thread."""
    def _thunk():
        conn = _ro_conn_for_lane(lane)    # the engine only READS -> read-only is safe + concurrent
        try:
            return backtest.run_backtest(conn, start, end, strategy=strategy, cfg=config,
                                         bt=_bt_config(rebalance, lock), lane=lane)
        finally:
            conn.close()
    return _thunk


def _run_compare_thunk(strategies, start, end, rebalance, lane, lock):
    def _thunk():
        conn = _ro_conn_for_lane(lane)
        try:
            return backtest.compare_backtests(conn, start, end, strategies, cfg=config,
                                              bt=_bt_config(rebalance, lock), lane=lane)
        finally:
            conn.close()
    return _thunk


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


def _backtest_tab():
    strat_opts = [{"label": s, "value": s} for s in backtest.STRATEGIES]
    today = dt.date.today()
    controls = html.Div(style={"display": "flex", "gap": "18px", "flexWrap": "wrap",
                               "alignItems": "flex-end", **_CARD}, children=[
        html.Div([html.Label("Data lane", style=_LBL),
                  dcc.Dropdown(id="bt-lane", clearable=False, value="free", style={"width": "230px"},
                               options=[{"label": "free (Yahoo) -- INDICATIVE ONLY", "value": "free"},
                                        {"label": "simfin -- survivorship-free", "value": "simfin"}])]),
        html.Div([html.Label("Mode", style=_LBL),
                  dcc.Dropdown(id="bt-mode", clearable=False, value="single", style={"width": "190px"},
                               options=[{"label": "single strategy", "value": "single"},
                                        {"label": "compare (Deflated Sharpe)", "value": "compare"}])]),
        html.Div([html.Label("Strategy (single mode)", style=_LBL),
                  dcc.Dropdown(id="bt-strategy", clearable=False, value="hrp",
                               options=strat_opts, style={"width": "200px"})]),
        html.Div([html.Label("Strategies (compare mode)", style=_LBL),
                  dcc.Dropdown(id="bt-strategies", multi=True,
                               value=["hrp", "risk_parity", "equal_weight"],
                               options=strat_opts, style={"width": "320px"})]),
        html.Div([html.Label("Window (start -> end)", style=_LBL), html.Br(),
                  dcc.DatePickerRange(id="bt-dates", start_date=dt.date(2018, 1, 1).isoformat(),
                                      end_date=today.isoformat(), display_format="YYYY-MM-DD")]),
        html.Div([html.Label("Rebalance", style=_LBL),
                  dcc.Dropdown(id="bt-rebalance", clearable=False, value="monthly",
                               options=[{"label": "monthly", "value": "monthly"},
                                        {"label": "quarterly", "value": "quarterly"}],
                               style={"width": "140px"})]),
        html.Div([html.Label("Locked-test start (optional)", style=_LBL), html.Br(),
                  dcc.DatePickerSingle(id="bt-lock", placeholder="hold-out tail",
                                       clearable=True, display_format="YYYY-MM-DD")]),
        html.Div([html.Button("Run backtest", id="bt-run", n_clicks=0,
                              style={"padding": "9px 18px", "fontSize": "13px", "fontWeight": "600",
                                     "background": "#72243E", "color": "white", "border": "0",
                                     "borderRadius": "8px", "cursor": "pointer"})]),
    ])
    return html.Div([
        controls,
        html.Div("A run is ~25-35s, so it is computed OFF the web thread (a background job) and this "
                 "panel polls for the result -- the UI stays responsive. The free lane reads the main "
                 "data dir; the simfin (survivorship-free) lane reads its own 'data_simfin' dir "
                 "automatically (override: EQR_SIMFIN_DATA_DIR). If that dir is missing, the run fails "
                 "with a clear message -- pull-index-history + pull-simfin build it.",
                 style={**_NOTE, "marginBottom": "8px"}),
        html.Div(id="bt-warn"),
        dcc.Store(id="bt-job"),
        dcc.Interval(id="bt-poll", interval=1500, disabled=True),
        html.Div(id="bt-status", style={"fontSize": "13px", "margin": "4px 0 10px",
                                        "minHeight": "20px"}),
        html.Div(style=_CARD, children=[
            html.Div("NAV path (single) / Deflated-Sharpe race (compare)",
                     style={"fontSize": "13px", "fontWeight": "500", "marginBottom": "6px"}),
            dcc.Graph(id="bt-nav", config={"displayModeBar": False},
                      figure=px.line(title="Set the controls and click Run backtest"))]),
        html.Div(id="bt-result", style=_CARD),
        html.Div("Every run is net of costs, point-in-time, and carries its lane/validity stamp; the "
                 "Sharpe is a range (+/- 1 SE), comparisons are deflated for multiple testing, and the "
                 f"locked tail is scored once. {PERSISTENT_DISCLAIMER}",
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
                dcc.Tab(label="Backtest", value="backtest", children=[_backtest_tab()]),
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

    # Soft, non-blocking heads-up the moment a slow simfin combo is selected (before any run).
    @app.callback(
        Output("bt-warn", "children"),
        Input("bt-lane", "value"), Input("bt-mode", "value"),
        Input("bt-strategy", "value"), Input("bt-strategies", "value"),
    )
    def _backtest_warning(lane, mode, strategy, strategies):
        msg = _slow_combo_warning(lane, mode, strategy, strategies)
        if not msg:
            return ""
        return html.Div(msg, style={"background": "#FFF4E0", "color": "#8A5A00",
                                    "border": "1px solid #F0D49B", "borderRadius": "8px",
                                    "padding": "8px 14px", "fontSize": "12px",
                                    "margin": "0 0 8px"})

    # One callback drives the whole backtest tab: it is triggered both by the Run button (submit a
    # background job) and by the polling Interval (check the job + render when done). Using
    # callback_context to tell the two triggers apart keeps it to a single set of outputs -- no
    # duplicate-output juggling.
    @app.callback(
        Output("bt-job", "data"), Output("bt-poll", "disabled"), Output("bt-status", "children"),
        Output("bt-nav", "figure"), Output("bt-result", "children"),
        Input("bt-run", "n_clicks"), Input("bt-poll", "n_intervals"),
        State("bt-lane", "value"), State("bt-mode", "value"), State("bt-strategy", "value"),
        State("bt-strategies", "value"), State("bt-dates", "start_date"),
        State("bt-dates", "end_date"), State("bt-rebalance", "value"), State("bt-lock", "date"),
        State("bt-job", "data"),
        prevent_initial_call=True,
    )
    def _drive_backtest(n_clicks, n_intervals, lane, mode, strategy, strategies, start_date,
                        end_date, rebalance, lock_date, job_data):
        if ctx.triggered_id == "bt-run":
            start, end = _parse_date(start_date), _parse_date(end_date)
            if start is None or end is None:
                return no_update, True, "Pick a start and end date first.", no_update, no_update
            lock = _parse_date(lock_date)
            if mode == "compare":
                chosen = [s for s in (strategies or []) if s]
                if len(chosen) < 2:
                    return (no_update, True, "Compare mode needs at least 2 strategies.",
                            no_update, no_update)
                label = f"compare {'/'.join(chosen)} [{lane}] {start}->{end} {rebalance}"
                jid = MANAGER.submit("compare", label,
                                     _run_compare_thunk(chosen, start, end, rebalance, lane, lock))
                kind = "compare"
            else:
                if not strategy:
                    return no_update, True, "Pick a strategy first.", no_update, no_update
                label = f"{strategy} [{lane}] {start}->{end} {rebalance}"
                jid = MANAGER.submit("single", label,
                                     _run_single_thunk(strategy, start, end, rebalance, lane, lock))
                kind = "single"
            running = html.Span(f"Running: {label} ... (~25-35s; off the web thread, UI stays live)",
                                style={"color": "#8A6D1E"})
            placeholder = px.line(title="Running -- the result will appear here when the job finishes")
            return {"id": jid, "kind": kind}, False, running, placeholder, []

        # --- polling tick ---
        job = MANAGER.get((job_data or {}).get("id"))
        if job is None:
            return no_update, True, no_update, no_update, no_update
        if not job.is_terminal():
            status = html.Span(f"Running: {job.label} ... {job.elapsed():.0f}s elapsed",
                               style={"color": "#8A6D1E"})
            return no_update, False, status, no_update, no_update
        if job.status == ERROR:
            status = html.Span(f"Backtest failed: {job.error}", style={"color": "#8A2326"})
            return no_update, True, status, px.line(title="Backtest failed -- see message above"), []
        # job.status == DONE
        status = html.Span(f"Done in {job.elapsed():.0f}s: {job.label}", style={"color": "#1E6B3A"})
        if job.kind == "compare":
            return no_update, True, status, _compare_figure(job.result), _compare_view(job.result)
        return no_update, True, status, _nav_figure(job.result), _result_view(job.result)

    return app


def main() -> None:
    app = build_app()
    print("Equity research UI -> http://127.0.0.1:8050  (read-only; Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=8050, debug=False)


if __name__ == "__main__":
    main()
