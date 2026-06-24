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
from equity_research.disclaimer import (
    INDICATIVE_STAMP, SURVIVORSHIP_STAMP, BacktestCompare, BacktestResult,
)

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
    survivorship: bool = False,
) -> pd.Series:
    """Target weights at `as_of` for the chosen fixed strategy (empty Series if none investable).

    Risk methods (hrp / risk_parity / inverse_variance) reuse `construct_portfolio` -- including its
    position/sector/liquidity caps, sized to `portfolio_value`. "composite" equal-weights the top-N
    names by factor composite; "equal_weight" equal-weights the WHOLE investable universe (the
    naive 1/N benchmark). The last two are simple, transparent rules with no caps.

    When `survivorship` is set, the basket is the point-in-time index membership
    (`universe.survivorship_universe`, delisted names included) restricted to names that actually
    have a price on `as_of` -- so price-only strategies are survivorship-free among covered names.
    Composite is unsupported here (it needs point-in-time fundamentals for the delisted names).
    """
    strategy = strategy.lower()
    if survivorship:
        basket = universe.survivorship_universe(conn, as_of)
        if basket:
            priced = _close_on(conn, as_of, basket)
            basket = [t for t in basket if t in priced]
        if not basket:
            return pd.Series(dtype="float64")
        if strategy in ("hrp", "risk_parity", "inverse_variance"):
            res = portfolio.construct_portfolio(
                conn, as_of, tickers=basket, method=strategy, cfg=cfg,
                portfolio_value=portfolio_value,
            )
            if res.weights is None or len(res.weights) == 0:
                return pd.Series(dtype="float64")
            return res.weights.astype("float64")
        if strategy == "equal_weight":
            return pd.Series(1.0 / len(basket), index=basket, dtype="float64")
        raise ValueError(f"strategy {strategy!r} is not supported in the survivorship lane")

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
    period_end_dates: list[dt.date] = field(default_factory=list)  # aligned 1:1 with net_rets
    turnovers: list[float] = field(default_factory=list)
    total_txn: float = 0.0
    total_tax: float = 0.0
    empty_periods: int = 0
    delisting_liquidations: int = 0
    final_nav: float = 0.0
    names: set[str] = field(default_factory=set)   # distinct tickers ever held (coverage signal)
    holding_counts: list[int] = field(default_factory=list)  # names held per rebalance


def _walk(
    conn: duckdb.DuckDBPyConnection,
    rdates: list[dt.date],
    strategy: str,
    cfg: Config,
    bt: BacktestConfig,
    survivorship: bool = False,
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
                out.period_end_dates.append(d)
            nav = V
            out.nav_curve.append((d, nav))
            break

        # --- 3. Choose target weights using only data <= d ---
        w = _target_weights(conn, d, strategy, V, cfg, survivorship)
        if len(w) == 0:
            out.empty_periods += 1
        held_names = [str(t) for t in w.index if float(w.get(t, 0.0)) > 0.0]
        out.names.update(held_names)
        out.holding_counts.append(len(held_names))

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
            out.period_end_dates.append(d)
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
# Phase C helpers -- higher-moment Sharpe + locked-test (in/out-of-sample) split
# --------------------------------------------------------------------------- #
def _window_metrics(rets: list[float], ppy: int, rf: float) -> dict:
    """Summary stats for a slice of the return path (empty slice -> n=0 and NaNs, never faked)."""
    sr, _ = metrics.sharpe(rets, ppy, rf)
    return {
        "n": len(rets),
        "cagr": metrics.cagr(rets, ppy),
        "vol": metrics.annualized_volatility(rets, ppy),
        "sharpe": sr,
        "max_drawdown": metrics.max_drawdown(rets),
    }


def _per_period_sharpe(annual_sharpe: float, ppy: int) -> float:
    """The non-annualized Sharpe the PSR/DSR formulas expect (annual = per-period * sqrt(ppy))."""
    if not np.isfinite(annual_sharpe):
        return float("nan")
    return annual_sharpe / np.sqrt(ppy)


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

    Lanes:
      - "free"   -- Yahoo prices, liquid-screen universe; survivorship-biased -> INDICATIVE ONLY.
      - "simfin" -- point-in-time index membership (fja05680) + delisted prices (SimFin >=2019);
        best-effort survivorship-free. Price-only strategies (hrp/risk_parity/inverse_variance/
        equal_weight) are supported; composite needs PIT fundamentals for delisted names and is not.
      - "sharadar" -- a future paid upgrade; refused rather than faked.
    """
    bt = bt or cfg.backtest
    strategy = strategy.lower()
    ppy, rdates = _rebalance_dates(start, end, bt.rebalance)
    rf = bt.risk_free_annual / ppy
    survivorship = lane == "simfin"

    assumptions = [
        f"half-spread {bt.spread_bps:.1f} bps + commission {bt.commission_bps:.1f} bps per trade",
        f"market impact {bt.impact_coef:.1f} bps * sqrt(participation in ADV), "
        f"participation capped at {bt.impact_participation_cap:.0%}",
        f"tax on realized gains {bt.tax_rate:.0%} per rebalance "
        f"({'tax-deferred account' if bt.tax_rate == 0 else 'taxable account'})",
        f"risk-free rate {bt.risk_free_annual:.1%}/yr for Sharpe/Sortino",
    ]

    stamp = SURVIVORSHIP_STAMP if survivorship else INDICATIVE_STAMP

    def _empty(note: str) -> BacktestResult:
        return BacktestResult(
            strategy=strategy, lane=lane, valid=False, start=start, end=end,
            rebalance=bt.rebalance, stamp=stamp, periods_per_year=ppy, n_periods=0,
            initial_capital=bt.initial_capital_usd, final_nav=bt.initial_capital_usd,
            cost_assumptions=assumptions, notes=[note],
        )

    if lane not in ("free", "simfin"):
        return _empty(
            f"lane '{lane}' is not built (use 'free' or 'simfin'); Sharadar is a future paid upgrade."
        )
    if survivorship and strategy == "composite":
        return _empty(
            "the survivorship (simfin) lane needs point-in-time fundamentals for delisted names, "
            "which we don't have; use hrp / risk_parity / inverse_variance / equal_weight."
        )
    if len(rdates) < 2:
        return _empty(
            f"need at least two {bt.rebalance} rebalance dates in [{start}, {end}]; got {len(rdates)}"
        )

    strat = _walk(conn, rdates, strategy, cfg, bt, survivorship)

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
    if survivorship:
        notes.append(
            "survivorship-free via point-in-time index membership (fja05680) + delisted prices "
            "(SimFin free, >=2019); names/dates SimFin does not cover are excluded (coverage gap, "
            "not survivorship). Composite/factor strategies need PIT fundamentals and are unsupported here."
        )
    else:
        notes.append(
            "free Lane-1 prices are survivorship-biased and lack point-in-time index membership; "
            "this run validates the engine, it is not a defensible track record"
        )
    if survivorship and len(strat.names) < 50:
        notes.insert(0, (
            f"LOW COVERAGE: only {len(strat.names)} distinct names were priced/traded (a full S&P "
            f"universe is ~500). This run is NOT meaningfully survivorship-free -- load delisted "
            f"prices with pull-simfin into THIS data dir (set EQR_DATA_DIR to a dedicated dir first)."
        ))

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

    # Phase C - Probabilistic Sharpe (P(true SR > 0)): correct the single-path Sharpe for the
    # series' actual skew/kurtosis + sample length. A near-normal series (skew 0, kurt 3) needs no
    # adjustment, so missing higher moments fall back to those rather than blocking the PSR.
    skew = metrics.skewness(strat.net_rets)
    kurt = metrics.kurtosis(strat.net_rets)
    psr = metrics.probabilistic_sharpe_ratio(
        _per_period_sharpe(sharpe_net, ppy), len(strat.net_rets),
        skew if np.isfinite(skew) else 0.0, kurt if np.isfinite(kurt) else 3.0,
    )

    # Phase C - multi-regime stress: bucket each realized period by the regime of its END date.
    regime_stats = metrics.regime_breakdown(strat.period_end_dates, strat.net_rets, ppy)

    # Phase C - locked test set: split the path at the hold-out boundary and summarize each side.
    lock = bt.locked_test_start
    is_stats = _window_metrics([], ppy, rf)
    oos_stats = _window_metrics([], ppy, rf)
    if lock is not None:
        pairs = list(zip(strat.period_end_dates, strat.net_rets, strict=True))
        is_stats = _window_metrics([r for d, r in pairs if d < lock], ppy, rf)
        oos_stats = _window_metrics([r for d, r in pairs if d >= lock], ppy, rf)
        if oos_stats["n"] == 0:
            notes.append(
                f"locked_test_start {lock} is at/after the end of the run -> no out-of-sample tail "
                f"to evaluate (move it earlier to hold out a meaningful period)."
            )

    # Benchmark: equal-weight 1/N of the same investable universe, same costs/dates. Skipped when
    # the strategy IS the benchmark (would be a tautology).
    bench_label = ""
    bench_cagr = bench_vol = bench_sharpe = bench_mdd = excess = float("nan")
    if strategy != BENCHMARK:
        bench = _walk(conn, rdates, BENCHMARK, cfg, bt, survivorship)
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
        rebalance=bt.rebalance, stamp=stamp, periods_per_year=ppy, n_periods=len(strat.net_rets),
        initial_capital=float(bt.initial_capital_usd), final_nav=float(strat.final_nav),
        n_names_traded=len(strat.names),
        avg_holdings=(float(np.mean(strat.holding_counts)) if strat.holding_counts else float("nan")),
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
        prob_sharpe_positive=psr,
        ret_skew=(skew if np.isfinite(skew) else float("nan")),
        ret_kurtosis=(kurt if np.isfinite(kurt) else float("nan")),
        regime_stats=regime_stats,
        locked_test_start=lock,
        is_n=is_stats["n"], is_cagr=is_stats["cagr"], is_vol=is_stats["vol"],
        is_sharpe=is_stats["sharpe"], is_max_drawdown=is_stats["max_drawdown"],
        oos_n=oos_stats["n"], oos_cagr=oos_stats["cagr"], oos_vol=oos_stats["vol"],
        oos_sharpe=oos_stats["sharpe"], oos_max_drawdown=oos_stats["max_drawdown"],
        benchmark_label=bench_label, bench_cagr_net=bench_cagr, bench_vol_net=bench_vol,
        bench_sharpe_net=bench_sharpe, bench_max_drawdown=bench_mdd, excess_cagr=excess,
        cost_assumptions=assumptions, notes=notes, nav_curve=strat.nav_curve,
        net_returns=list(strat.net_rets), period_end_dates=list(strat.period_end_dates),
    )


# --------------------------------------------------------------------------- #
# Phase C entry point -- honest multiple-strategy comparison (Deflated Sharpe)
# --------------------------------------------------------------------------- #
def _split_returns(
    res: BacktestResult, lock: dt.date | None
) -> tuple[list[float], list[float]]:
    """(in-sample, out-of-sample) realized returns, split at the locked-test boundary.

    With no lock the whole path is in-sample (and there is no held-out tail to score once).
    """
    pairs = list(zip(res.period_end_dates, res.net_returns, strict=True))
    if lock is None:
        return [r for _, r in pairs], []
    return [r for d, r in pairs if d < lock], [r for d, r in pairs if d >= lock]


def compare_backtests(
    conn: duckdb.DuckDBPyConnection,
    start: dt.date,
    end: dt.date,
    strategies: list[str],
    cfg: Config = config,
    bt: BacktestConfig | None = None,
    lane: str = "free",
) -> BacktestCompare:
    """Compare N strategies honestly: Deflated-Sharpe each (n_trials=N), select IN-SAMPLE ONLY.

    Runs each strategy over [start, end] (reusing `run_backtest`, so every run is net of costs and
    point-in-time). For each, the Deflated Sharpe Ratio uses `n_trials = len(strategies)` and the
    cross-trial variance of the in-sample per-period Sharpes as the deflation dispersion -- so a
    strategy that merely won an N-horse race is discounted. SELECTION uses only the in-sample window
    (before `bt.locked_test_start`); the locked out-of-sample tail is reported once for the winner
    and never used to choose it. This is the structural multiple-testing + out-of-sample discipline
    non-negotiable #5 demands.
    """
    bt = bt or cfg.backtest
    strategies = [s.lower() for s in dict.fromkeys(strategies)]  # de-dup, preserve order
    ppy = _FREQ.get(bt.rebalance, ("ME", 12))[1]
    rf = bt.risk_free_annual / ppy
    lock = bt.locked_test_start
    n_trials = len(strategies)
    stamp = SURVIVORSHIP_STAMP if lane == "simfin" else INDICATIVE_STAMP
    notes: list[str] = []

    # 1. Run every strategy and collect its in-sample per-period Sharpe / skew / kurtosis.
    runs: dict[str, BacktestResult] = {}
    is_pp_sharpes: dict[str, float] = {}
    is_moments: dict[str, tuple[int, float, float]] = {}   # strategy -> (n_is, skew, kurt)
    for s in strategies:
        res = run_backtest(conn, start, end, strategy=s, cfg=cfg, bt=bt, lane=lane)
        runs[s] = res
        is_rets, _ = _split_returns(res, lock)
        is_pp_sharpes[s] = _per_period_sharpe(metrics.sharpe(is_rets, ppy, rf)[0], ppy)
        sk, ku = metrics.skewness(is_rets), metrics.kurtosis(is_rets)
        is_moments[s] = (len(is_rets), sk if np.isfinite(sk) else 0.0,
                         ku if np.isfinite(ku) else 3.0)

    # 2. Deflation dispersion = variance of the in-sample per-period Sharpes ACROSS the trials.
    finite_sr = [v for v in is_pp_sharpes.values() if np.isfinite(v)]
    sr_variance = float(np.var(finite_sr, ddof=1)) if len(finite_sr) >= 2 else 0.0

    # 3. Per-strategy DSR (in-sample) + display rows (in-sample selection metrics, OOS for show only).
    rows: list[dict] = []
    for s in strategies:
        res = runs[s]
        is_rets, oos_rets = _split_returns(res, lock)
        is_w = _window_metrics(is_rets, ppy, rf)
        oos_w = _window_metrics(oos_rets, ppy, rf)
        n_is, sk, ku = is_moments[s]
        dsr = (metrics.deflated_sharpe_ratio(is_pp_sharpes[s], n_is, n_trials, sk, ku,
                                             sr_variance=sr_variance)
               if n_is >= 2 and np.isfinite(is_pp_sharpes[s]) else float("nan"))
        rows.append({
            "strategy": s, "is_n": is_w["n"], "is_cagr": is_w["cagr"],
            "is_sharpe": is_w["sharpe"], "dsr": dsr,
            "oos_n": oos_w["n"], "oos_cagr": oos_w["cagr"], "oos_sharpe": oos_w["sharpe"],
        })

    # 4. Select the winner by IN-SAMPLE DSR only (tie-break: in-sample Sharpe). Never peek at OOS.
    selectable = [r for r in rows if np.isfinite(r["dsr"])]
    selected = ""
    if selectable:
        best = max(selectable, key=lambda r: (r["dsr"],
                                              r["is_sharpe"] if np.isfinite(r["is_sharpe"]) else -1e9))
        selected = best["strategy"]

    if n_trials < 2:
        notes.append("only one strategy compared -> no multiple-testing correction applies "
                     "(DSR reduces to the probabilistic Sharpe). Pass >=2 strategies to deflate.")
    if lock is None:
        notes.append("no locked_test_start set -> selection used the WHOLE sample and there is no "
                     "held-out tail. Set --locked-test-start for true out-of-sample discipline.")
    else:
        notes.append(f"selection used IN-SAMPLE data only (before {lock}); the out-of-sample tail "
                     f"was scored once, after selection.")
    notes.append(f"DSR deflation dispersion (cross-trial variance of in-sample per-period Sharpes): "
                 f"{sr_variance:.6f}")
    if lane == "simfin":
        notes.append("survivorship-free lane: price-only strategies only (composite is refused).")
    else:
        notes.append("free lane: survivorship-biased -> the comparison is INDICATIVE ONLY.")

    return BacktestCompare(
        lane=lane, stamp=stamp, start=start, end=end, rebalance=bt.rebalance,
        periods_per_year=ppy, locked_test_start=lock, n_trials=n_trials,
        sr_variance=sr_variance, rows=rows, selected=selected, valid=False, notes=notes,
    )
