"""v3 walk-forward backtest engine (Phase A -- free Lane-1 data, indicative-only).

What this does, honestly:
  - Walks a calendar of rebalance dates. At each date `d` it sees ONLY data with `date <= d`
    (point-in-time, non-negotiable #2): the target weights come from `portfolio.construct_portfolio`
    / `factors.factor_profile`, both of which read through `data/pit.py`. No rebalance can peek.
  - Holds positions as "units" of each name's split/dividend-adjusted price series (`closeadj`),
    so a position's value compounds with its total return between rebalances automatically and is
    marked point-in-time at each date.
  - Charges costs on EVERY trade (`costs.trade_cost`: half-spread + commission + sqrt-law market
    impact in ADV) plus a configurable tax drag on realized gains. Tracks a NET-of-cost NAV and,
    alongside it, the GROSS (pre-cost) path -- a pre-cost number is never reported on its own.
  - Reports the strategy against a BENCHMARK -- equal-weight 1/N of the SAME point-in-time
    investable universe, held and costed identically -- because an absolute return is meaningless
    without one. The excess CAGR isolates what the weighting/selection actually added.
  - Survivorship-free at the ENGINE level: a held name that stops trading mid-backtest is marked
    at its last observed price, then liquidated (best case -- delisting OUTCOMES are unknown on
    free data) and dropped. It is never silently survived.

Why every Phase A run is stamped "indicative only": free Yahoo prices drop most delisted names
(the universe over-counts winners) and EDGAR has no point-in-time index membership. The machinery
is honest; the *data* cannot yet support a defensible track record. Phase B (Sharadar) flips that.

Holdings are kept fully invested (weights sum to 1) and a cost haircut is applied as a proportional
reduction of NAV after sizing trades; the trade notional used for the cost charge is therefore the
pre-haircut target, an approximation that is second-order in basis points and documented here.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from equity_research import costs, factors, metrics, portfolio, universe
from equity_research.config import BacktestConfig, Config, config
from equity_research.data import pit
from equity_research.disclaimer import BacktestResult

# "equal_weight" is both a runnable strategy and the default benchmark (naive 1/N diversification).
STRATEGIES = ("hrp", "risk_parity", "inverse_variance", "composite", "equal_weight")
BENCHMARK = "equal_weight"
_FREQ = {"monthly": ("ME", 12), "quarterly": ("QE", 4)}


# --------------------------------------------------------------------------- #
# Calendar + price helpers
# --------------------------------------------------------------------------- #
def _rebalance_dates(start: dt.date, end: dt.date, rebalance: str) -> tuple[int, list[dt.date]]:
    """Period-end rebalance dates in [start, end] + periods-per-year for the cadence."""
    if rebalance not in _FREQ:
        raise ValueError(f"unknown rebalance {rebalance!r}; choose one of {tuple(_FREQ)}")
    freq, ppy = _FREQ[rebalance]
    idx = pd.date_range(start, end, freq=freq)
    return ppy, [ts.date() for ts in idx]


def _close_on(
    conn: duckdb.DuckDBPyConnection, as_of: dt.date, tickers: list[str], lookback: int = 400
) -> dict[str, tuple[dt.date, float]]:
    """ticker -> (last_obs_date, adjusted_close) for the last price known on/before `as_of`.

    Point-in-time: only rows with `date <= as_of` are read. A name that delisted still resolves
    to its last observed price (used to mark/liquidate it); a name with no price in the window is
    simply absent.
    """
    names = [t.upper() for t in dict.fromkeys(tickers)]
    if not names:
        return {}
    px = pit.prices_asof(conn, as_of, lookback, names)
    if px.empty:
        return {}
    out: dict[str, tuple[dt.date, float]] = {}
    for tkr, g in px.sort_values("date").groupby("ticker"):
        row = g.iloc[-1]
        out[str(tkr)] = (pd.Timestamp(row["date"]).date(), float(row["closeadj"]))
    return out


def _adv_map(conn: duckdb.DuckDBPyConnection, as_of: dt.date, cfg: Config) -> dict[str, float]:
    """ticker -> average daily DOLLAR volume from the liquid screen as of `as_of` (for cost impact)."""
    scr = universe.screening_universe(conn, as_of, cfg)
    if scr.empty:
        return {}
    return scr.set_index("ticker")["avg_dollar_volume"].to_dict()


# --------------------------------------------------------------------------- #
# Strategy -> target weights (all point-in-time via the reused v1/v2 layers)
# --------------------------------------------------------------------------- #
def _target_weights(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    strategy: str,
    portfolio_value: float,
    cfg: Config,
) -> pd.Series:
    """Target weights at `as_of` for the chosen fixed strategy (empty Series if none investable).

    Risk methods (hrp / risk_parity / inverse_variance) reuse `construct_portfolio` -- including its
    position/sector/liquidity caps, sized to `portfolio_value`. "composite" equal-weights the top-N
    names by factor composite; "equal_weight" equal-weights the WHOLE investable universe (the
    naive 1/N benchmark). The last two are simple, transparent rules with no caps.
    """
    strategy = strategy.lower()
    if strategy in ("hrp", "risk_parity", "inverse_variance"):
        res = portfolio.construct_portfolio(
            conn, as_of, method=strategy, cfg=cfg, portfolio_value=portfolio_value
        )
        if res.weights is None or len(res.weights) == 0:
            return pd.Series(dtype="float64")
        return res.weights.astype("float64")
    if strategy in ("composite", "equal_weight"):
        prof = factors.factor_profile(conn, as_of, cfg)
        if prof.empty:
            return pd.Series(dtype="float64")
        chosen = prof if strategy == "equal_weight" else prof.head(cfg.portfolio.default_top_n)
        n = len(chosen)
        if n == 0:
            return pd.Series(dtype="float64")
        return pd.Series(1.0 / n, index=chosen.index, dtype="float64")
    raise ValueError(f"unknown strategy {strategy!r}; choose one of {STRATEGIES}")


# --------------------------------------------------------------------------- #
# Core walk (one strategy over a fixed date grid) -- reused for both the
# strategy and its benchmark, so they share costs/dates/delisting handling.
# --------------------------------------------------------------------------- #
@dataclass
class _Walk:
    nav_curve: list[tuple[dt.date, float]] = field(default_factory=list)
    net_rets: list[float] = field(default_factory=list)
    gross_rets: list[float] = field(default_factory=list)
    turnovers: list[float] = field(default_factory=list)
    total_txn: float = 0.0
    total_tax: float = 0.0
    empty_periods: int = 0
    delisting_liquidations: int = 0
    final_nav: float = 0.0


def _walk(
    conn: duckdb.DuckDBPyConnection,
    rdates: list[dt.date],
    strategy: str,
    cfg: Config,
    bt: BacktestConfig,
) -> _Walk:
    """Run one strategy over `rdates`, returning the net/gross return path, costs, and diagnostics."""
    units: dict[str, float] = {}     # ticker -> shares of the adjusted-price series
    basis: dict[str, float] = {}     # ticker -> dollar cost basis (for tax on realized gains)
    nav = float(bt.initial_capital_usd)
    cash = nav                       # capital not (yet) deployed -- retains value, never evaporates
    prev_nav = nav
    out = _Walk()
    last = len(rdates) - 1

    for k, d in enumerate(rdates):
        # --- 1. Mark current holdings to market at d (point-in-time); add carried cash ---
        cur_value: dict[str, float] = {}
        stale_held: set[str] = set()
        if units:
            marks = _close_on(conn, d, list(units))
            for t, u in units.items():
                m = marks.get(t)
                if m is None:
                    continue  # no price within lookback -> treated as worthless (rare; noted below)
                last_date, price = m
                cur_value[t] = u * price
                if (d - last_date).days > bt.staleness_days:
                    stale_held.add(t)
        invested = float(sum(cur_value.values()))
        # Undeployed capital (caps too tight for the universe, or no investable names) sits in cash
        # and is carried at face value -- it must NOT silently disappear (that bug compounded a 10%
        # undeployment in a thin early universe into a fake -72% drawdown).
        V = invested + cash

        gross_r = (V / prev_nav - 1.0) if k > 0 else None

        # --- 2. Final date is a valuation point only (no trade, no friction) ---
        if k == last:
            if gross_r is not None:
                out.gross_rets.append(gross_r)
                out.net_rets.append(gross_r)  # no rebalance friction at the terminal mark
            nav = V
            out.nav_curve.append((d, nav))
            break

        # --- 3. Choose target weights using only data <= d ---
        w = _target_weights(conn, d, strategy, V, cfg)
        if len(w) == 0:
            out.empty_periods += 1

        # --- 4. Price the union of held + target names at d (to size trades in shares) ---
        target_names = [str(t) for t in w.index]
        px = _close_on(conn, d, list(set(cur_value) | set(target_names)))
        adv = _adv_map(conn, d, cfg)
        target_value = {t: float(w.get(t, 0.0)) * V for t in set(cur_value) | set(target_names)}

        # --- 5. Trades -> transaction cost + tax on realized gains; update cost basis ---
        txn = 0.0
        tax = 0.0
        traded = 0.0
        for t in set(cur_value) | set(target_value):
            cur = cur_value.get(t, 0.0)
            tgt = target_value.get(t, 0.0)
            trade = tgt - cur
            if abs(trade) < 1e-9:
                continue
            traded += abs(trade)
            txn += costs.trade_cost(abs(trade), adv.get(t, float("nan")), bt)
            if trade < 0.0 and cur > 0.0:                      # a sell -> may realize a gain
                frac_sold = min(1.0, (-trade) / cur)
                cost_basis = basis.get(t, cur)
                gain = frac_sold * (cur - cost_basis)
                tax += bt.tax_rate * max(0.0, gain)
                basis[t] = cost_basis - frac_sold * cost_basis
                if t in stale_held:
                    out.delisting_liquidations += 1
            elif trade > 0.0:                                  # a buy -> add to basis
                basis[t] = basis.get(t, 0.0) + trade

        friction = txn + tax
        out.total_txn += txn
        out.total_tax += tax
        nav_new = V - friction

        # --- 6. Record the period's returns / turnover (k=0 is the one-time entry build) ---
        if k > 0:
            out.gross_rets.append(gross_r)            # gross of this date's friction
            out.net_rets.append(nav_new / prev_nav - 1.0)
            out.turnovers.append(traded / V if V > 0 else float("nan"))

        # --- 7. Re-establish holdings at the post-cost NAV; keep the undeployed fraction as cash ---
        new_units: dict[str, float] = {}
        deployed = 0.0
        for t in target_names:
            wt = float(w.get(t, 0.0))
            m = px.get(t)
            if wt <= 0.0 or m is None or m[1] <= 0.0:
                continue
            value = wt * nav_new
            new_units[t] = value / m[1]
            deployed += value
        units = new_units
        basis = {t: basis.get(t, 0.0) for t in units}  # forget basis of fully-exited names
        cash = nav_new - deployed     # undeployed capital is HELD as cash, never lost

        nav = nav_new
        out.nav_curve.append((d, nav))
        prev_nav = nav

    out.final_nav = nav
    return out


# --------------------------------------------------------------------------- #
# Engine entry point
# --------------------------------------------------------------------------- #
def run_backtest(
    conn: duckdb.DuckDBPyConnection,
    start: dt.date,
    end: dt.date,
    strategy: str = "hrp",
    cfg: Config = config,
    bt: BacktestConfig | None = None,
    lane: str = "free",
) -> BacktestResult:
    """Run a walk-forward, net-of-cost backtest of `strategy` over [start, end], vs an equal-weight benchmark.

    Phase A only supports `lane="free"` (always indicative-only). `lane="sharadar"` is Phase B and
    is refused here rather than silently producing an invalid-but-unstamped result.
    """
    bt = bt or cfg.backtest
    strategy = strategy.lower()
    ppy, rdates = _rebalance_dates(start, end, bt.rebalance)
    rf = bt.risk_free_annual / ppy

    assumptions = [
        f"half-spread {bt.spread_bps:.1f} bps + commission {bt.commission_bps:.1f} bps per trade",
        f"market impact {bt.impact_coef:.1f} bps * sqrt(participation in ADV), "
        f"participation capped at {bt.impact_participation_cap:.0%}",
        f"tax on realized gains {bt.tax_rate:.0%} per rebalance "
        f"({'tax-deferred account' if bt.tax_rate == 0 else 'taxable account'})",
        f"risk-free rate {bt.risk_free_annual:.1%}/yr for Sharpe/Sortino",
    ]

    def _empty(note: str) -> BacktestResult:
        return BacktestResult(
            strategy=strategy, lane=lane, valid=False, start=start, end=end,
            rebalance=bt.rebalance, periods_per_year=ppy, n_periods=0,
            initial_capital=bt.initial_capital_usd, final_nav=bt.initial_capital_usd,
            cost_assumptions=assumptions, notes=[note],
        )

    if lane != "free":
        return _empty(
            f"lane '{lane}' is not built yet (Phase B / Sharadar). Phase A runs free/indicative-only."
        )
    if len(rdates) < 2:
        return _empty(
            f"need at least two {bt.rebalance} rebalance dates in [{start}, {end}]; got {len(rdates)}"
        )

    strat = _walk(conn, rdates, strategy, cfg, bt)

    notes: list[str] = []
    if strat.empty_periods:
        notes.append(
            f"{strat.empty_periods} rebalance date(s) had no investable universe -> held cash that "
            f"period (pull more names / widen the window / deepen price history if unexpected)"
        )
    if strat.delisting_liquidations:
        notes.append(
            f"{strat.delisting_liquidations} holding(s) went stale/delisted mid-run and were "
            f"liquidated at their last observed price (delisting outcomes are unknown on free data)"
        )
    notes.append(
        "free Lane-1 prices are survivorship-biased and lack point-in-time index membership; "
        "this run validates the engine, it is not a defensible track record"
    )

    if not strat.net_rets:
        res = _empty("no holding-period returns were realized (universe empty throughout)")
        res.notes = notes
        return res

    sharpe_net, sharpe_se = metrics.sharpe(strat.net_rets, ppy, rf)
    sharpe_gross, _ = metrics.sharpe(strat.gross_rets, ppy, rf)
    cagr_net = metrics.cagr(strat.net_rets, ppy)
    cagr_gross = metrics.cagr(strat.gross_rets, ppy)
    cost_drag = (
        cagr_gross - cagr_net
        if np.isfinite(cagr_gross) and np.isfinite(cagr_net)
        else float("nan")
    )

    # Benchmark: equal-weight 1/N of the same investable universe, same costs/dates. Skipped when
    # the strategy IS the benchmark (would be a tautology).
    bench_label = ""
    bench_cagr = bench_vol = bench_sharpe = bench_mdd = excess = float("nan")
    if strategy != BENCHMARK:
        bench = _walk(conn, rdates, BENCHMARK, cfg, bt)
        if bench.net_rets:
            bench_label = "equal-weight 1/N investable universe, net of same costs"
            bench_cagr = metrics.cagr(bench.net_rets, ppy)
            bench_vol = metrics.annualized_volatility(bench.net_rets, ppy)
            bench_sharpe, _ = metrics.sharpe(bench.net_rets, ppy, rf)
            bench_mdd = metrics.max_drawdown(bench.net_rets)
            if np.isfinite(cagr_net) and np.isfinite(bench_cagr):
                excess = cagr_net - bench_cagr

    return BacktestResult(
        strategy=strategy, lane=lane, valid=False, start=start, end=end,
        rebalance=bt.rebalance, periods_per_year=ppy, n_periods=len(strat.net_rets),
        initial_capital=float(bt.initial_capital_usd), final_nav=float(strat.final_nav),
        cagr_net=cagr_net,
        vol_net=metrics.annualized_volatility(strat.net_rets, ppy),
        sharpe_net=sharpe_net, sharpe_se=sharpe_se,
        sortino_net=metrics.sortino(strat.net_rets, ppy, rf),
        max_drawdown=metrics.max_drawdown(strat.net_rets),
        hit_rate=metrics.hit_rate(strat.net_rets),
        avg_turnover=(float(np.mean(strat.turnovers)) if strat.turnovers else float("nan")),
        cagr_gross=cagr_gross,
        vol_gross=metrics.annualized_volatility(strat.gross_rets, ppy),
        sharpe_gross=sharpe_gross,
        total_txn_cost=strat.total_txn, total_tax=strat.total_tax, cost_drag_annual=cost_drag,
        benchmark_label=bench_label, bench_cagr_net=bench_cagr, bench_vol_net=bench_vol,
        bench_sharpe_net=bench_sharpe, bench_max_drawdown=bench_mdd, excess_cagr=excess,
        cost_assumptions=assumptions, notes=notes, nav_curve=strat.nav_curve,
    )
