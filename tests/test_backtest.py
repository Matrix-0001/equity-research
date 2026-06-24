"""Offline tests for the v3 walk-forward engine (synthetic DuckDB fixtures, hand-checked).

Pins the properties that make a backtest honest: costs actually charged (and netted out),
NAV compounds correctly, no rebalance peeks at future data (point-in-time), and a name that
delists mid-run is liquidated rather than silently survived. No network.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from equity_research import backtest as bt
from equity_research.config import BacktestConfig, Config, PortfolioConfig
from equity_research.data import storage
from equity_research.disclaimer import INDICATIVE_STAMP, SURVIVORSHIP_STAMP

ZERO_COST = BacktestConfig(spread_bps=0.0, commission_bps=0.0, impact_coef=0.0,
                           tax_rate=0.0, rebalance="monthly", initial_capital_usd=1_000_000.0)


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #
def _fundamentals(tickers: list[str], datekey="2023-10-01") -> pd.DataFrame:
    df = pd.DataFrame([
        {"ticker": t, "net_income": 1e8, "total_equity": 1e9, "gross_profit": 5e8,
         "total_assets": 1e10, "revenue": 2e9, "shares_outstanding": 1e9}
        for t in tickers
    ])
    df["datekey"] = pd.Timestamp(datekey)
    df["reportperiod"] = pd.Timestamp("2023-09-30")
    df["form"] = "10-K"
    return df


def _prices(ticker: str, dates, closes, volume: float = 1_000_000.0) -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": ticker, "date": d, "closeadj": float(c), "closeunadj": float(c),
         "volume": volume}
        for d, c in zip(dates, closes, strict=True)
    ])


def _connect(tmp_path, **portfolio_over):
    pcfg = PortfolioConfig(**portfolio_over) if portfolio_over else PortfolioConfig()
    cfg = Config(data_dir=tmp_path, portfolio=pcfg)
    return storage.connect(cfg), cfg


# --------------------------------------------------------------------------- #
# Cost accounting (exact, hand-computed)
# --------------------------------------------------------------------------- #
def test_entry_cost_is_charged_exactly(tmp_path):
    # One flat-priced name, composite (single name -> 100% weight). Only the initial build trades;
    # after that weights never drift, so no further cost. spread 10 bps on a $1,000,000 build = $1,000.
    c, cfg = _connect(tmp_path)
    days = pd.bdate_range("2023-11-01", "2024-05-01")
    storage.load_table(c, _prices("AAA", days, [100.0] * len(days)), "prices")
    storage.load_table(c, _fundamentals(["AAA"]), "fundamentals")
    cost_cfg = replace(ZERO_COST, spread_bps=10.0)  # 10 bps, no commission/impact/tax
    try:
        res = bt.run_backtest(c, dt.date(2024, 2, 15), dt.date(2024, 4, 30),
                              strategy="composite", cfg=cfg, bt=cost_cfg)
    finally:
        c.close()
    assert res.n_periods == 2                       # 3 month-ends -> 2 holding periods
    assert res.total_txn_cost == pytest.approx(1_000.0)
    assert res.total_tax == 0.0
    assert res.final_nav == pytest.approx(999_000.0)
    assert res.cagr_net == pytest.approx(0.0)       # flat prices
    assert res.valid is False
    assert INDICATIVE_STAMP in res.render()


def test_costs_reduce_returns_and_are_netted(tmp_path):
    # Two names whose prices cross each month force rebalancing turnover -> recurring cost.
    c, cfg = _connect(tmp_path)
    days = pd.bdate_range("2023-11-01", "2024-06-30")

    def stepped(seed_high: bool):
        out = []
        for d in days:
            month = d.month
            hi, lo = (130.0, 70.0) if (month % 2 == 0) == seed_high else (70.0, 130.0)
            out.append((d, hi, lo))
        return out

    rows = stepped(True)
    storage.load_table(c, _prices("AA", days, [r[1] for r in rows]), "prices")
    storage.upsert_by_ticker(c, _prices("BB", days, [r[2] for r in rows]), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB"]), "fundamentals")
    with_cost = replace(ZERO_COST, spread_bps=20.0, commission_bps=2.0)
    try:
        free = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 6, 30),
                               strategy="composite", cfg=cfg, bt=ZERO_COST)
        costed = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 6, 30),
                                 strategy="composite", cfg=cfg, bt=with_cost)
    finally:
        c.close()
    assert free.total_txn_cost == 0.0
    assert costed.total_txn_cost > 0.0              # turnover actually charged
    assert costed.cagr_net < free.cagr_net          # costs drag net returns down
    assert costed.cost_drag_annual > 0.0


def test_tax_charged_on_realized_gains(tmp_path):
    # Two names held 50/50. AA rises, BB is flat, so each rebalance TRIMS the appreciated winner
    # back to 50% -- a partial sale that realizes a gain a positive tax rate must bite. BB has no
    # gain. We compare tax=0 vs tax>0: tax is charged only when configured, and it drags NAV.
    c, cfg = _connect(tmp_path, default_top_n=2)
    days = pd.bdate_range("2023-11-01", "2024-06-30")

    def rising(d):
        return 100.0 + (d - pd.Timestamp("2023-11-01")).days * 0.3   # AA appreciates

    storage.load_table(c, _prices("AA", days, [rising(d) for d in days]), "prices")
    storage.upsert_by_ticker(c, _prices("BB", days, [100.0] * len(days)), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB"]), "fundamentals")
    try:
        taxed = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 6, 30),
                                strategy="composite", cfg=cfg,
                                bt=replace(ZERO_COST, tax_rate=0.30))
        untaxed = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 6, 30),
                                  strategy="composite", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert untaxed.total_tax == 0.0          # no tax when rate is 0
    assert taxed.total_tax > 0.0             # trimming the winner realizes a taxed gain
    assert taxed.final_nav < untaxed.final_nav


# --------------------------------------------------------------------------- #
# NAV compounding (engine-level, exact)
# --------------------------------------------------------------------------- #
def test_nav_compounds_with_a_known_price_path(tmp_path):
    # Single name, zero cost, step-up price: +10% per month at month-ends -> NAV must compound it.
    c, cfg = _connect(tmp_path)
    days = pd.bdate_range("2023-11-01", "2024-05-01")

    def stepped_close(d):
        if d <= pd.Timestamp("2024-02-29"):
            return 100.0
        if d <= pd.Timestamp("2024-03-31"):
            return 110.0
        return 121.0

    storage.load_table(c, _prices("AAA", days, [stepped_close(d) for d in days]), "prices")
    storage.load_table(c, _fundamentals(["AAA"]), "fundamentals")
    try:
        res = bt.run_backtest(c, dt.date(2024, 2, 15), dt.date(2024, 4, 30),
                              strategy="composite", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.total_txn_cost == 0.0
    assert res.final_nav == pytest.approx(1_210_000.0)   # 1e6 * 110/100 * 121/110
    navs = [v for _, v in res.nav_curve]
    assert navs == pytest.approx([1_000_000.0, 1_100_000.0, 1_210_000.0])


# --------------------------------------------------------------------------- #
# No lookahead (point-in-time): future data cannot change a past rebalance
# --------------------------------------------------------------------------- #
def test_no_lookahead_future_data_does_not_change_past_rebalances(tmp_path):
    c, cfg = _connect(tmp_path)
    hist = pd.bdate_range("2023-09-01", "2024-03-31")
    rng = np.random.default_rng(1)
    aa = 50.0 * np.cumprod(1 + rng.normal(0, 0.01, len(hist)))
    bb = 50.0 * np.cumprod(1 + rng.normal(0, 0.01, len(hist)))
    storage.load_table(c, _prices("AA", hist, aa), "prices")
    storage.upsert_by_ticker(c, _prices("BB", hist, bb), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB"]), "fundamentals")

    end = dt.date(2024, 3, 31)
    try:
        run1 = bt.run_backtest(c, dt.date(2024, 1, 15), end, strategy="composite",
                               cfg=cfg, bt=ZERO_COST)
        # Inject WILD future data (after `end`): AA crashes, BB moons, plus a brand-new name that
        # would dominate selection IF the engine ever peeked. None of it is knowable by `end`.
        future = pd.bdate_range("2024-04-01", "2024-06-30")
        storage.upsert_by_ticker(c, _prices("AA", future, [1.0] * len(future)), "prices")
        # re-add AA history too (upsert replaces the whole ticker), so keep past + future:
        storage.upsert_by_ticker(
            c, pd.concat([_prices("AA", hist, aa), _prices("AA", future, [1.0] * len(future))],
                         ignore_index=True), "prices")
        storage.upsert_by_ticker(c, _prices("FUT", future, [9999.0] * len(future)), "prices")
        storage.upsert_by_ticker(c, _fundamentals(["AA", "BB", "FUT"]), "fundamentals")
        run2 = bt.run_backtest(c, dt.date(2024, 1, 15), end, strategy="composite",
                               cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    navs1 = [(d.isoformat(), round(v, 6)) for d, v in run1.nav_curve]
    navs2 = [(d.isoformat(), round(v, 6)) for d, v in run2.nav_curve]
    assert navs1 == navs2          # identical: no rebalance in [start, end] saw the future
    assert run1.final_nav == pytest.approx(run2.final_nav)


# --------------------------------------------------------------------------- #
# Delisting: a name that stops trading is liquidated, never silently survived
# --------------------------------------------------------------------------- #
def test_delisted_name_is_liquidated_not_survived(tmp_path):
    c, cfg = _connect(tmp_path)
    full = pd.bdate_range("2023-09-01", "2024-07-31")
    aa = [50.0 + 0.05 * i for i in range(len(full))]            # AA trades the whole run
    storage.load_table(c, _prices("AA", full, aa), "prices")
    # BB trades only through early March 2024, then delists (no later prices).
    bb_days = pd.bdate_range("2023-09-01", "2024-03-05")
    bb = [40.0 - 0.02 * i for i in range(len(bb_days))]
    storage.upsert_by_ticker(c, _prices("BB", bb_days, bb), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB"]), "fundamentals")
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 7, 31),
                              strategy="composite", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.n_periods == 6                                  # 7 month-ends
    assert res.final_nav > 0.0
    # The honesty property: BB was actively liquidated when it went stale, not silently kept.
    assert any("delisted" in n or "liquidated" in n for n in res.notes)


# --------------------------------------------------------------------------- #
# End-to-end across the risk-based methods + honest refusals
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("strategy",
                         ["hrp", "risk_parity", "inverse_variance", "composite", "equal_weight"])
def test_engine_runs_end_to_end(tmp_path, strategy):
    c, cfg = _connect(tmp_path, min_return_obs=30, cov_lookback_days=400, default_top_n=10)
    days = pd.bdate_range("2023-06-01", "2024-05-01")
    rng = np.random.default_rng(7)
    frames = []
    for t in ("AA", "BB", "CC"):
        px = 50.0 * np.cumprod(1 + rng.normal(0.0003, 0.012, len(days)))
        frames.append(_prices(t, days, px))
    storage.load_table(c, pd.concat(frames, ignore_index=True), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB", "CC"]), "fundamentals")
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                              strategy=strategy, cfg=cfg,
                              bt=replace(ZERO_COST, spread_bps=5.0, commission_bps=1.0))
    finally:
        c.close()
    assert res.n_periods >= 2
    assert res.final_nav > 0.0
    assert res.n_names_traded > 0                          # coverage signal is populated
    assert np.isfinite(res.cagr_net)
    assert np.isfinite(res.vol_net)
    assert res.total_txn_cost > 0.0
    assert res.valid is False
    out = res.render()
    assert INDICATIVE_STAMP in out
    assert "+/- 1 SE" in out or "Not estimated" in out      # Sharpe shown as a range


def test_sharadar_lane_is_refused_until_phase_b(tmp_path):
    c, cfg = _connect(tmp_path)
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                              strategy="hrp", cfg=cfg, bt=ZERO_COST, lane="sharadar")
    finally:
        c.close()
    assert res.valid is False
    assert res.n_periods == 0
    assert any("Phase B" in n or "not built" in n for n in res.notes)


def test_survivorship_lane_feels_a_delisted_crash(tmp_path):
    # The whole point of survivorship-free: a held name that delists/crashes HURTS the portfolio.
    # AA/BB trade throughout; DEAD is an index member until 2021-06 then collapses 40 -> ~0 and is
    # removed. equal_weight holds ~1/3 in DEAD, so the crash must show as a big negative month --
    # which a survivorship-BIASED run (DEAD silently absent) would never feel.
    c, cfg = _connect(tmp_path, min_return_obs=20, cov_lookback_days=400)
    days = pd.bdate_range("2019-01-01", "2022-06-30")
    rng = np.random.default_rng(3)
    storage.load_table(c, _prices("AA", days, 50 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days)))),
                       "prices")
    storage.upsert_by_ticker(
        c, _prices("BB", days, 50 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(days)))), "prices")
    dead = [40.0 if d <= pd.Timestamp("2021-05-15") else 0.01 for d in days]  # collapse, then padded
    storage.upsert_by_ticker(c, _prices("DEAD", days, dead), "prices")
    mem = pd.DataFrame({
        "ticker": ["AA", "BB", "DEAD"],
        "start_date": [pd.Timestamp("2019-01-02")] * 3,
        "end_date": [pd.NaT, pd.NaT, pd.Timestamp("2021-06-01")],   # DEAD leaves the index 2021-06
        "source": ["test"] * 3,
    })
    storage.load_table(c, mem, "index_membership")
    try:
        res = bt.run_backtest(c, dt.date(2020, 6, 30), dt.date(2022, 6, 30),
                              strategy="equal_weight", cfg=cfg, bt=ZERO_COST, lane="simfin")
    finally:
        c.close()
    assert res.lane == "simfin"
    assert SURVIVORSHIP_STAMP in res.render()
    assert INDICATIVE_STAMP not in res.render()           # not the free-lane stamp
    assert res.n_periods >= 12
    assert res.n_names_traded == 3                         # AA, BB, DEAD -- coverage is visible
    assert any("LOW COVERAGE" in n for n in res.notes)     # 3 names is honestly flagged as thin
    navs = [v for _, v in res.nav_curve]
    worst = min(navs[i + 1] / navs[i] - 1.0 for i in range(len(navs) - 1))
    assert worst < -0.15                                   # DEAD's ~1/3 wipeout is felt


def test_survivorship_lane_refuses_composite(tmp_path):
    c, cfg = _connect(tmp_path)
    mem = pd.DataFrame({"ticker": ["AA"], "start_date": [pd.Timestamp("2019-01-02")],
                        "end_date": [pd.NaT], "source": ["test"]})
    storage.load_table(c, mem, "index_membership")
    try:
        res = bt.run_backtest(c, dt.date(2020, 6, 30), dt.date(2021, 6, 30),
                              strategy="composite", cfg=cfg, bt=ZERO_COST, lane="simfin")
    finally:
        c.close()
    assert res.n_periods == 0
    assert any("point-in-time fundamentals" in n for n in res.notes)


def test_too_few_rebalance_dates_refuses(tmp_path):
    c, cfg = _connect(tmp_path)
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 1, 20),
                              strategy="hrp", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.n_periods == 0
    assert any("at least two" in n for n in res.notes)


# --------------------------------------------------------------------------- #
# Benchmark: a strategy return is reported against equal-weight 1/N of the same universe
# --------------------------------------------------------------------------- #
def _rng_world(tmp_path):
    c, cfg = _connect(tmp_path, min_return_obs=30, cov_lookback_days=400, default_top_n=10)
    days = pd.bdate_range("2023-06-01", "2024-05-01")
    rng = np.random.default_rng(11)
    frames = []
    for t in ("AA", "BB", "CC", "DD"):
        px = 50.0 * np.cumprod(1 + rng.normal(0.0003, 0.012, len(days)))
        frames.append(_prices(t, days, px))
    storage.load_table(c, pd.concat(frames, ignore_index=True), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB", "CC", "DD"]), "fundamentals")
    return c, cfg


def test_benchmark_reported_and_excess_is_consistent(tmp_path):
    c, cfg = _rng_world(tmp_path)
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                              strategy="hrp", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.benchmark_label != ""                     # benchmark actually ran
    assert np.isfinite(res.bench_cagr_net)
    assert np.isfinite(res.bench_sharpe_net)
    # excess CAGR is exactly strategy-minus-benchmark (no hidden adjustment).
    assert res.excess_cagr == pytest.approx(res.cagr_net - res.bench_cagr_net)
    out = res.render()
    assert "Benchmark (" in out and "EXCESS CAGR" in out


def test_undeployed_capital_is_held_as_cash_not_leaked(tmp_path):
    # Few names + a tight position cap force part of the book undeployed; that remainder MUST be
    # held as cash (conserved), never silently dropped. Two names whose prices wiggle early (so the
    # covariance is non-degenerate) but sit flat at 100 across the test window; 30% position cap ->
    # 2*0.30 = 0.60 deployable, 0.40 undeployed. With zero costs and flat marks, capital round-trips
    # exactly -> final NAV == initial. (The leak bug compounded the undeployed 40% away each period.)
    c, cfg = _connect(tmp_path, min_return_obs=15, cov_lookback_days=400, max_position_weight=0.30)
    days = pd.bdate_range("2023-09-01", "2024-04-05")
    # Variance only in Dec 2023; the Feb-Mar 2024 test window is flat at 100.
    closes = [102.0 if (d.year == 2023 and d.month == 12 and d.day % 2 == 0) else 100.0
              for d in days]
    storage.load_table(c, _prices("AA", days, closes), "prices")
    storage.upsert_by_ticker(c, _prices("BB", days, closes), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB"]), "fundamentals")
    try:
        res = bt.run_backtest(c, dt.date(2024, 2, 15), dt.date(2024, 3, 31),
                              strategy="inverse_variance", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.final_nav == pytest.approx(1_000_000.0, rel=1e-6)   # capital conserved, not leaked
    assert res.max_drawdown == pytest.approx(0.0, abs=1e-9)        # flat window -> no real drawdown


def test_equal_weight_strategy_skips_self_benchmark(tmp_path):
    c, cfg = _rng_world(tmp_path)
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                              strategy="equal_weight", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.n_periods >= 2
    assert res.benchmark_label == ""                     # would be a tautology -> skipped
    assert np.isnan(res.excess_cagr)
    assert "Benchmark (" not in res.render()


# --------------------------------------------------------------------------- #
# Phase C - Probabilistic Sharpe on a single run
# --------------------------------------------------------------------------- #
def test_probabilistic_sharpe_is_reported(tmp_path):
    c, cfg = _rng_world(tmp_path)
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                              strategy="hrp", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert 0.0 <= res.prob_sharpe_positive <= 1.0        # it's a probability
    out = res.render()
    assert "Prob(SR>0)" in out and "PSR" in out


# --------------------------------------------------------------------------- #
# Phase C - multi-regime stress tagging
# --------------------------------------------------------------------------- #
def test_regime_stress_tags_covered_and_marks_uncovered(tmp_path):
    # A 2007->2010 run straddles the GFC window (2007-10..2009-06) and normal months on either side;
    # it never reaches COVID/dot-com/2022, which must read "Not estimated", not a fabricated 0.
    c, cfg = _connect(tmp_path)
    days = pd.bdate_range("2006-01-01", "2010-07-31")
    storage.load_table(c, _prices("AAA", days, [100.0] * len(days)), "prices")
    storage.load_table(c, _fundamentals(["AAA"], datekey="2006-01-02"), "fundamentals")
    try:
        res = bt.run_backtest(c, dt.date(2007, 6, 15), dt.date(2010, 6, 30),
                              strategy="composite", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert "GFC" in res.regime_stats
    assert "normal" in res.regime_stats
    assert res.regime_stats["GFC"]["n"] + res.regime_stats["normal"]["n"] == res.n_periods
    assert "COVID crash" not in res.regime_stats         # this run can't speak to it
    out = res.render()
    assert "Multi-regime stress" in out
    assert "COVID crash" in out and "Not estimated (no periods in this run's window)" in out


# --------------------------------------------------------------------------- #
# Phase C - locked test set (in-sample vs out-of-sample tail)
# --------------------------------------------------------------------------- #
def test_locked_test_split_is_reported(tmp_path):
    # 8 month-ends from a 2024-01-15 start -> 7 realized periods. Lock at 2024-05-01 puts the three
    # period-ends in Feb/Mar/Apr in-sample and the four from May..Aug out-of-sample.
    c, cfg = _connect(tmp_path)
    days = pd.bdate_range("2023-09-01", "2024-09-15")
    storage.load_table(c, _prices("AAA", days, [100.0] * len(days)), "prices")
    storage.load_table(c, _fundamentals(["AAA"]), "fundamentals")
    locked = replace(ZERO_COST, locked_test_start=dt.date(2024, 5, 1))
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 8, 31),
                              strategy="composite", cfg=cfg, bt=locked)
    finally:
        c.close()
    assert res.n_periods == 7
    assert res.is_n == 3 and res.oos_n == 4
    assert res.is_n + res.oos_n == res.n_periods
    assert res.locked_test_start == dt.date(2024, 5, 1)
    out = res.render()
    assert "Out-of-sample discipline" in out
    assert "In-sample" in out and "Out-of-sample" in out


def test_no_lock_says_so(tmp_path):
    c, cfg = _connect(tmp_path)
    days = pd.bdate_range("2023-09-01", "2024-05-01")
    storage.load_table(c, _prices("AAA", days, [100.0] * len(days)), "prices")
    storage.load_table(c, _fundamentals(["AAA"]), "fundamentals")
    try:
        res = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                              strategy="composite", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert res.locked_test_start is None
    assert "no locked test boundary set" in res.render()


# --------------------------------------------------------------------------- #
# Phase C - backtest-compare (Deflated Sharpe, in-sample selection)
# --------------------------------------------------------------------------- #
def _compare_world(tmp_path):
    c, cfg = _connect(tmp_path, min_return_obs=30, cov_lookback_days=400, default_top_n=10)
    days = pd.bdate_range("2022-06-01", "2024-12-31")
    rng = np.random.default_rng(19)
    frames = []
    for t in ("AA", "BB", "CC", "DD"):
        px = 50.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, len(days)))
        frames.append(_prices(t, days, px))
    storage.load_table(c, pd.concat(frames, ignore_index=True), "prices")
    storage.load_table(c, _fundamentals(["AA", "BB", "CC", "DD"], datekey="2022-06-02"),
                       "fundamentals")
    return c, cfg


def test_compare_selects_on_in_sample_dsr_and_scores_oos_once(tmp_path):
    c, cfg = _compare_world(tmp_path)
    strategies = ["hrp", "risk_parity", "inverse_variance", "equal_weight"]
    locked = replace(ZERO_COST, locked_test_start=dt.date(2024, 6, 1))
    try:
        cmp = bt.compare_backtests(c, dt.date(2023, 1, 15), dt.date(2024, 12, 31),
                                   strategies, cfg=cfg, bt=locked, lane="free")
    finally:
        c.close()
    assert cmp.n_trials == 4
    assert len(cmp.rows) == 4
    # The winner is exactly the highest IN-SAMPLE DSR -- never an OOS-peeking choice.
    finite = [r for r in cmp.rows if np.isfinite(r["dsr"])]
    assert finite, "expected at least one strategy with a computable DSR"
    assert cmp.selected == max(finite, key=lambda r: r["dsr"])["strategy"]
    # Every row carries an OOS tail score (computed once, not used to select).
    assert all(r["oos_n"] >= 1 for r in cmp.rows)
    out = cmp.render()
    assert "Deflated Sharpe Ratio" in out
    assert "Selected by IN-SAMPLE DSR" in out
    assert "locked out-of-sample tail" in out


def test_with_benchmark_false_skips_benchmark_but_keeps_strategy_stats(tmp_path):
    c, cfg = _rng_world(tmp_path)
    try:
        full = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                               strategy="hrp", cfg=cfg, bt=ZERO_COST)
        nobench = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                                  strategy="hrp", cfg=cfg, bt=ZERO_COST, with_benchmark=False)
    finally:
        c.close()
    assert nobench.benchmark_label == ""                  # benchmark walk skipped
    assert np.isnan(nobench.bench_cagr_net)
    assert nobench.final_nav == pytest.approx(full.final_nav)   # the strategy path is identical
    assert nobench.cagr_net == pytest.approx(full.cagr_net)


def test_shared_cache_across_strategies_is_lossless(tmp_path):
    # The run-scoped cache that compare_backtests shares across strategies must not change ANY
    # result vs running each strategy with its own fresh cache (it only memoizes PIT views by date).
    c, cfg = _rng_world(tmp_path)
    shared: dict = {}
    try:
        a_shared = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                                   strategy="hrp", cfg=cfg, bt=ZERO_COST, _cache=shared)
        b_shared = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                                   strategy="risk_parity", cfg=cfg, bt=ZERO_COST, _cache=shared)
        a_fresh = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                                  strategy="hrp", cfg=cfg, bt=ZERO_COST)
        b_fresh = bt.run_backtest(c, dt.date(2024, 1, 15), dt.date(2024, 4, 30),
                                  strategy="risk_parity", cfg=cfg, bt=ZERO_COST)
    finally:
        c.close()
    assert a_shared.final_nav == pytest.approx(a_fresh.final_nav)
    assert b_shared.final_nav == pytest.approx(b_fresh.final_nav)
    assert a_shared.cagr_net == pytest.approx(a_fresh.cagr_net)
    assert b_shared.cagr_net == pytest.approx(b_fresh.cagr_net)


def test_compare_deflates_more_with_more_trials(tmp_path):
    # The SAME strategy's DSR inside a 4-way race is <= its DSR run alone: multiple testing is
    # actually penalized (a higher expected best-of-N benchmark to clear).
    c, cfg = _compare_world(tmp_path)
    locked = replace(ZERO_COST, locked_test_start=dt.date(2024, 6, 1))
    try:
        solo = bt.compare_backtests(c, dt.date(2023, 1, 15), dt.date(2024, 12, 31),
                                    ["hrp"], cfg=cfg, bt=locked, lane="free")
        race = bt.compare_backtests(c, dt.date(2023, 1, 15), dt.date(2024, 12, 31),
                                    ["hrp", "risk_parity", "inverse_variance", "equal_weight"],
                                    cfg=cfg, bt=locked, lane="free")
    finally:
        c.close()
    solo_hrp = next(r["dsr"] for r in solo.rows if r["strategy"] == "hrp")
    race_hrp = next(r["dsr"] for r in race.rows if r["strategy"] == "hrp")
    assert np.isfinite(solo_hrp) and np.isfinite(race_hrp)
    assert race_hrp <= solo_hrp + 1e-12                  # more trials -> equal-or-lower DSR
    assert race.sr_variance > 0.0                        # the trials actually differed
