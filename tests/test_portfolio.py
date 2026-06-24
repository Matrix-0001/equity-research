"""Offline tests for v2 portfolio construction (synthetic fixtures, hand-computed values).

Pins the math that matters: covariance annualization + honest dropping, the three weighting
schemes (inverse-variance, equal-risk-contribution, HRP), the position/liquidity caps, the
bounded fractional-Kelly, and the end-to-end orchestrator (including the Kelly-without-mu
refusal). No network.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from equity_research import portfolio as pf
from equity_research.config import Config, PortfolioConfig
from equity_research.data import storage
from equity_research.disclaimer import NOT_ESTIMATED

AS_OF = dt.date(2026, 6, 15)


# --------------------------------------------------------------------------- #
# Weighting schemes (hand-built covariance matrices, no DB)
# --------------------------------------------------------------------------- #
def test_inverse_variance_exact():
    cov = np.diag([0.01, 0.04, 0.16])  # variances; vols 0.1 / 0.2 / 0.4
    w = pf.inverse_variance_weights(cov)
    # w_i proportional to 1/var_i = 100 / 25 / 6.25 -> /131.25
    assert w == pytest.approx([100, 25, 6.25] / np.float64(131.25), rel=1e-9)
    assert w.sum() == pytest.approx(1.0)


def test_erc_uncorrelated_is_inverse_vol():
    # For uncorrelated assets ERC gives inverse-VOLATILITY weights: 1/0.1 : 1/0.2 : 1/0.4.
    cov = np.diag([0.01, 0.04, 0.16])
    w = pf.risk_parity_weights(cov)
    assert w == pytest.approx([4 / 7, 2 / 7, 1 / 7], rel=1e-6)
    # ... and the risk contributions are equalized.
    rc = pf.risk_contributions(cov, w)
    assert rc == pytest.approx(np.full(3, 1 / 3), rel=1e-6)


def test_erc_equal_vol_equal_corr_is_equal_weight():
    v, c = 0.04, 0.02  # equal variance, equal pairwise covariance
    cov = np.array([[v, c, c], [c, v, c], [c, c, v]])
    w = pf.risk_parity_weights(cov)
    assert w == pytest.approx(np.full(3, 1 / 3), rel=1e-6)


def test_erc_general_equalizes_risk_contributions():
    cov = np.array([
        [0.04, 0.012, 0.008],
        [0.012, 0.09, 0.018],
        [0.008, 0.018, 0.16],
    ])
    w = pf.risk_parity_weights(cov)
    rc = pf.risk_contributions(cov, w)
    assert rc == pytest.approx(np.full(3, 1 / 3), abs=1e-6)
    assert w.sum() == pytest.approx(1.0)


def test_hrp_uses_correlation_structure():
    # Cluster {0,1}: vol 0.2, correlated 0.9.  Cluster {2,3}: vol 0.2, uncorrelated.
    cov = np.array([
        [0.04, 0.036, 0.0, 0.0],
        [0.036, 0.04, 0.0, 0.0],
        [0.0, 0.0, 0.04, 0.0],
        [0.0, 0.0, 0.0, 0.04],
    ])
    w = pf.hrp_weights(cov)
    # Hand-computed (cluster vars 0.038 vs 0.020 -> alpha 0.3448 to the correlated half):
    assert w == pytest.approx([0.172414, 0.172414, 0.327586, 0.327586], abs=1e-5)
    assert w.sum() == pytest.approx(1.0)
    # The diversification property: HRP DOWN-weights the internally-correlated cluster
    # relative to inverse-variance (which, with equal vols, would give a flat 25% each).
    ivp = pf.inverse_variance_weights(cov)
    assert ivp == pytest.approx(np.full(4, 0.25))
    assert w[0] < 0.25 < w[2]


def test_hrp_single_and_two_assets():
    assert pf.hrp_weights(np.array([[0.04]])) == pytest.approx([1.0])
    # n=2 HRP reduces to inverse-variance allocation.
    cov = np.diag([0.01, 0.04])
    assert pf.hrp_weights(cov) == pytest.approx(pf.inverse_variance_weights(cov))


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #
def test_position_cap_redistributes_and_flags():
    w, binding, notes = pf.apply_position_cap(np.array([0.7, 0.2, 0.1]), cap=0.4)
    assert w == pytest.approx([0.4, 0.4, 0.2])
    assert 0 in binding  # the name that was over the cap
    assert notes == []   # feasible, fully deployed


def test_position_cap_infeasible_reports_undeployed():
    # 3 names, 0.2 cap -> max deployable is 0.6; 0.4 cannot be placed.
    w, binding, notes = pf.apply_position_cap(np.array([0.6, 0.3, 0.1]), cap=0.2)
    assert w == pytest.approx([0.2, 0.2, 0.2])
    assert w.sum() == pytest.approx(0.6)
    assert notes and "undeployed" in notes[0]


def test_liquidity_cap_binds_illiquid_name():
    weights = np.array([0.5, 0.3, 0.2])
    adv = np.array([1e10, 1e10, 1e6])  # third name is thin
    w, binding, notes = pf.apply_liquidity_cap(weights, adv, portfolio_value=1e8,
                                               max_participation=0.05)
    # Cap on the thin name: 0.05 * 1e6 / 1e8 = 0.0005.
    assert w[2] == pytest.approx(0.0005, abs=1e-9)
    assert binding == [2]
    assert w.sum() == pytest.approx(1.0)  # excess redeployed to liquid names
    assert notes == []


def test_sector_cap_redistributes_and_flags():
    w, binding, notes = pf.apply_sector_cap(
        np.array([0.35, 0.35, 0.30]), ["Tech", "Tech", "Energy"], cap=0.5)
    assert w == pytest.approx([0.25, 0.25, 0.50])  # Tech scaled to cap, freed 0.2 -> Energy
    assert binding == ["Tech"]
    assert notes == []


def test_sector_cap_infeasible_reports_undeployed():
    w, binding, notes = pf.apply_sector_cap(np.array([0.6, 0.4]), ["Tech", "Tech"], cap=0.5)
    assert w.sum() == pytest.approx(0.5)  # only one sector -> 0.5 cannot be deployed
    assert binding == ["Tech"]
    assert notes and "undeployed" in notes[0]


def test_sector_cap_exempts_unknown():
    # Unknown-sector names are not capped as a group, but can absorb redistribution.
    w, binding, notes = pf.apply_sector_cap(np.array([0.6, 0.4]), ["Tech", "Unknown"], cap=0.5)
    assert w == pytest.approx([0.5, 0.5])
    assert binding == ["Tech"]


def test_liquidity_cap_reports_undeployable_capital():
    weights = np.array([0.6, 0.4])
    adv = np.array([1e6, 1e6])
    w, binding, notes = pf.apply_liquidity_cap(weights, adv, portfolio_value=1e9,
                                               max_participation=0.05)
    assert binding == [0, 1]
    assert w.sum() == pytest.approx(1e-4)  # only 0.005% of each name deployable
    assert notes and "cannot be deployed" in notes[0]


# --------------------------------------------------------------------------- #
# Bounded fractional-Kelly (pure math; explicit mu)
# --------------------------------------------------------------------------- #
def test_liquidity_cap_does_not_blame_prior_shortfall():
    # Weights already sum to 0.4 (e.g. a prior position cap left 60% uninvested). With ample
    # ADV the liquidity cap binds nothing and must NOT claim the 60% as a liquidity problem.
    weights = np.array([0.1, 0.1, 0.1, 0.1])
    adv = np.full(4, 1e10)
    w, binding, notes = pf.apply_liquidity_cap(weights, adv, portfolio_value=1e6,
                                               max_participation=0.05)
    assert w == pytest.approx(weights)
    assert binding == []
    assert notes == []


def test_fractional_kelly_hand_computed():
    cov = np.diag([0.04, 0.01])
    mu = np.array([0.08, 0.05])
    res = pf.fractional_kelly(mu, cov, fraction=0.25, cap=0.8)
    # C^-1 mu = (0.08/0.04, 0.05/0.01) = (2, 5).
    assert res.f_full == pytest.approx([2.0, 5.0])
    assert res.f_fractional == pytest.approx([0.5, 1.25])
    assert res.f_capped == pytest.approx([0.5, 0.8])  # 1.25 clipped to the 0.8 cap
    # Long-only normalized weights: (0.5, 1.25)/1.75.
    assert res.long_only_weights == pytest.approx([0.285714, 0.714286], abs=1e-5)


def test_fractional_kelly_singular_cov_does_not_crash():
    # Two perfectly collinear names (e.g. dual-class shares) -> singular covariance. Kelly must
    # fall back to the pseudo-inverse and return finite leverage rather than raising LinAlgError.
    cov = np.array([[0.04, 0.04], [0.04, 0.04]])
    res = pf.fractional_kelly(np.array([0.06, 0.06]), cov, fraction=0.25, cap=0.5)
    assert np.all(np.isfinite(res.f_full))


# --------------------------------------------------------------------------- #
# Covariance estimation + orchestrator (synthetic DuckDB)
# --------------------------------------------------------------------------- #
def _prices_from_returns(ticker: str, returns: list[float], end="2026-06-12",
                         start_price: float = 100.0, volume: float = 1_000_000) -> pd.DataFrame:
    dates = pd.bdate_range(end=end, periods=len(returns) + 1)
    px, p = [], start_price
    px.append(p)
    for r in returns:
        p *= (1.0 + r)
        px.append(p)
    return pd.DataFrame([
        {"ticker": ticker, "date": d, "closeadj": v, "closeunadj": v, "volume": volume}
        for d, v in zip(dates, px, strict=True)
    ])


def _annual(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["datekey"] = pd.Timestamp("2025-12-15")
    df["reportperiod"] = pd.Timestamp("2025-09-30")
    df["form"] = "10-K"
    return df


def test_covariance_annualization_and_honest_dropping(tmp_path):
    cfg = Config(data_dir=tmp_path,
                 portfolio=PortfolioConfig(min_return_obs=10, cov_lookback_days=400))
    c = storage.connect(cfg)
    try:
        osc = [0.02, -0.02] * 20  # 40 returns, mean 0, |r| = 0.02
        a = _prices_from_returns("AAA", osc)
        b = _prices_from_returns("BBB", osc)              # identical -> corr 1
        flat = _prices_from_returns("FLAT", [0.0] * 40)   # zero variance -> dropped
        short = _prices_from_returns("SHRT", [0.01, -0.01])  # 2 returns -> dropped
        storage.load_table(c, pd.concat([a, b, flat, short], ignore_index=True), "prices")

        ce = pf.estimate_covariance(c, AS_OF, ["AAA", "BBB", "FLAT", "SHRT"], cfg)
    finally:
        c.close()

    assert set(ce.tickers) == {"AAA", "BBB"}
    assert "zero return variance" in ce.dropped["FLAT"]
    assert "insufficient history" in ce.dropped["SHRT"]
    assert ce.n_obs == 40
    # Hand-computed annualized variance: sample var = 40*(0.02^2)/(40-1) = 0.016/39;
    # annualized x252.
    expected_var = (40 * 0.02 ** 2 / 39) * 252
    i = ce.tickers.index("AAA")
    assert ce.cov[i, i] == pytest.approx(expected_var, rel=1e-9)
    assert ce.vols[i] == pytest.approx(np.sqrt(expected_var), rel=1e-9)
    # AAA and BBB are identical series -> correlation 1.
    j = ce.tickers.index("BBB")
    assert ce.corr[i, j] == pytest.approx(1.0, abs=1e-9)


def _small_universe(tmp_path, n_days: int = 60, seed: int = 42):
    cfg = Config(
        data_dir=tmp_path,
        portfolio=PortfolioConfig(min_return_obs=30, cov_lookback_days=400,
                                  max_position_weight=0.40, default_top_n=10),
    )
    c = storage.connect(cfg)
    rng = np.random.default_rng(seed)
    tickers = ["AA", "BB", "CC", "DD", "EE"]
    frames = []
    for t in tickers:
        rets = rng.normal(0.0, 0.012, size=n_days).tolist()
        df = _prices_from_returns(t, rets, start_price=50.0)
        frames.append(df)
    storage.load_table(c, pd.concat(frames, ignore_index=True), "prices")
    fund = _annual([
        {"ticker": t, "net_income": 1e8, "total_equity": 1e9, "gross_profit": 5e8,
         "total_assets": 1e10, "revenue": 2e9, "shares_outstanding": 1e9}
        for t in tickers
    ])
    storage.load_table(c, fund, "fundamentals")
    return c, cfg, tickers


@pytest.mark.parametrize("method", ["inverse_variance", "risk_parity", "hrp"])
def test_construct_portfolio_end_to_end(tmp_path, method):
    c, cfg, tickers = _small_universe(tmp_path)
    try:
        res = pf.construct_portfolio(c, AS_OF, tickers=tickers, method=method, cfg=cfg)
    finally:
        c.close()
    assert res.weights is not None
    assert set(res.weights.index) == set(tickers)
    assert res.weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert (res.weights >= -1e-12).all()
    assert (res.weights <= cfg.portfolio.max_position_weight + 1e-9).all()
    assert res.dropped == {}
    assert res.risk_contributions.sum() == pytest.approx(1.0, abs=1e-6)


def test_construct_applies_sector_cap(tmp_path):
    c, cfg, tickers = _small_universe(tmp_path)  # AA..EE, position cap already 0.40
    cfg = replace(cfg, portfolio=replace(cfg.portfolio, max_sector_weight=0.40))
    # 3 names in Tech, 1 Energy, 1 Unknown -> Tech (~0.6 unconstrained) must be reined to <= 0.40.
    secs = pd.DataFrame({
        "ticker": ["AA", "BB", "CC", "DD", "EE"],
        "sector": ["Information Technology", "Information Technology", "Information Technology",
                   "Energy", "Unknown"],
    })
    storage.load_table(c, secs, "securities")
    try:
        res = pf.construct_portfolio(c, AS_OF, tickers=tickers, method="risk_parity", cfg=cfg)
    finally:
        c.close()
    assert res.sector_cap == 0.40
    assert res.sector_weights  # realized per-sector exposure reported
    assert res.sector_weights.get("Information Technology", 0.0) <= 0.40 + 1e-6
    assert res.weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert any("exempt from the sector cap" in n for n in res.notes)  # the Unknown name


def test_construct_kelly_without_mu_refuses(tmp_path):
    c, cfg, tickers = _small_universe(tmp_path)
    try:
        res = pf.construct_portfolio(c, AS_OF, tickers=tickers, method="kelly", cfg=cfg, mu=None)
    finally:
        c.close()
    assert res.weights is None
    assert any("expected-return" in n for n in res.notes)
    assert NOT_ESTIMATED in res.render()


def test_construct_kelly_with_mu_produces_weights(tmp_path):
    c, cfg, tickers = _small_universe(tmp_path)
    mu = {t: 0.05 for t in tickers}  # explicit, caller-supplied expected excess returns
    try:
        res = pf.construct_portfolio(c, AS_OF, tickers=tickers, method="kelly", cfg=cfg, mu=mu)
    finally:
        c.close()
    assert res.weights is not None
    assert res.weights.sum() == pytest.approx(1.0, abs=1e-6)


def test_kelly_edge_basket_and_assumptions(tmp_path):
    c, cfg, tickers = _small_universe(tmp_path)
    mu = {"AA": 0.06, "BB": 0.04, "CC": 0.05}  # a subset: the edge set IS the basket
    try:
        res = pf.construct_portfolio(c, AS_OF, method="kelly", cfg=cfg, mu=mu)
    finally:
        c.close()
    assert res.weights is not None
    assert set(res.weights.index) == {"AA", "BB", "CC"}
    assert res.weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert res.assumptions and any("expected excess return" in a for a in res.assumptions)
    assert any("gross leverage" in n for n in res.notes)
    assert "USER-SUPPLIED" in res.render()


def test_kelly_all_nonpositive_edges_refuses(tmp_path):
    c, cfg, tickers = _small_universe(tmp_path)
    mu = {"AA": -0.05, "BB": -0.05}  # no positive tilt -> nothing to deploy long-only
    try:
        res = pf.construct_portfolio(c, AS_OF, method="kelly", cfg=cfg, mu=mu)
    finally:
        c.close()
    assert res.weights is None
    assert any("positive fractional-Kelly tilt" in n for n in res.notes)
    assert res.assumptions  # the (refused) edges are still echoed as assumptions


def test_parse_edges_pairs_and_validation():
    from equity_research import cli
    assert cli._parse_edges(["AAPL=0.06", "msft=0.045"], None) == {"AAPL": 0.06, "MSFT": 0.045}
    with pytest.raises(SystemExit):
        cli._parse_edges(["AAPL"], None)  # missing '='


def test_parse_edges_file(tmp_path):
    from equity_research import cli
    p = tmp_path / "edges.csv"
    p.write_text("ticker,edge\nAAPL,0.06\nMSFT,0.04\n")
    assert cli._parse_edges(None, str(p)) == {"AAPL": 0.06, "MSFT": 0.04}


def test_covariance_is_point_in_time(tmp_path):
    cfg = Config(data_dir=tmp_path,
                 portfolio=PortfolioConfig(min_return_obs=10, cov_lookback_days=400))
    c = storage.connect(cfg)
    rng = np.random.default_rng(7)
    dates = pd.bdate_range(end="2026-06-12", periods=60)
    try:
        frames = []
        for t in ("AA", "BB"):
            rets = rng.normal(0.0, 0.01, size=60).tolist()
            frames.append(_prices_from_returns(t, rets, start_price=50.0))
        storage.load_table(c, pd.concat(frames, ignore_index=True), "prices")

        early = pf.estimate_covariance(c, dates[29].date(), ["AA", "BB"], cfg)
        late = pf.estimate_covariance(c, dates[59].date(), ["AA", "BB"], cfg)
    finally:
        c.close()
    # The price grid has 61 dates (60 returns + a seed). Only returns knowable on/before
    # as_of are used: as_of at grid index 30 -> 30 returns; at the final index -> 60.
    assert early.n_obs == 30
    assert late.n_obs == 60
    assert early.n_obs < late.n_obs  # the cutoff genuinely binds


# --------------------------------------------------------------------------- #
# Survivorship-robust covariance (the delisted-name fix): a name that stops
# trading mid-window must NOT truncate every other name's history.
# --------------------------------------------------------------------------- #
def _prices_on_grid(ticker: str, returns: list[float], grid, start_price: float = 100.0,
                    volume: float = 1_000_000) -> pd.DataFrame:
    px, p = [start_price], start_price
    for r in returns:
        p *= (1.0 + r)
        px.append(p)
    return pd.DataFrame([
        {"ticker": ticker, "date": d, "closeadj": v, "closeunadj": v, "volume": volume}
        for d, v in zip(grid, px, strict=True)
    ])


def test_delisted_name_does_not_truncate_others_history(tmp_path):
    # AA/BB trade the full 200-return window; DEAD trades only the first 100 then delists. The OLD
    # common-window covariance (dropna how=any) would shrink EVERY name to DEAD's first-100 days,
    # changing AA's variance. The fix keeps AA's variance on its OWN full history.
    cfg = Config(data_dir=tmp_path,
                 portfolio=PortfolioConfig(min_return_obs=30, cov_lookback_days=400))
    c = storage.connect(cfg)
    grid = pd.bdate_range(end="2026-06-12", periods=201)        # 200 returns
    rng = np.random.default_rng(1)
    # AA: calm first half, volatile second half -> full-history var >> first-100 var.
    aa = np.concatenate([rng.normal(0, 0.005, 100), rng.normal(0, 0.02, 100)]).tolist()
    bb = rng.normal(0, 0.01, 200).tolist()
    dead = rng.normal(0, 0.01, 100).tolist()                   # only the first 100 returns
    try:
        storage.load_table(c, pd.concat([
            _prices_on_grid("AA", aa, grid),
            _prices_on_grid("BB", bb, grid),
            _prices_on_grid("DEAD", dead, grid[:101]),         # delists after 100 returns
        ], ignore_index=True), "prices")
        with_dead = pf.estimate_covariance(c, grid[-1].date(), ["AA", "BB", "DEAD"], cfg)
        without_dead = pf.estimate_covariance(c, grid[-1].date(), ["AA", "BB"], cfg)
    finally:
        c.close()

    assert "DEAD" in with_dead.tickers                         # delisted name kept, not dropped
    full_var = float(np.var(aa, ddof=1)) * 252.0
    calm_var = float(np.var(aa[:100], ddof=1)) * 252.0
    assert full_var > 1.5 * calm_var                           # regimes differ -> invariance is non-trivial
    i = with_dead.tickers.index("AA")
    j = without_dead.tickers.index("AA")
    assert with_dead.cov[i, i] == pytest.approx(full_var, rel=1e-6)        # AA uses its FULL history
    assert with_dead.cov[i, i] == pytest.approx(without_dead.cov[j, j], rel=1e-6)  # DEAD doesn't truncate AA
    assert with_dead.n_obs == 100 and without_dead.n_obs == 200           # DEAD is the thin leg only
    assert float(np.linalg.eigvalsh(with_dead.cov).min()) >= -1e-8        # matrix is PSD


def test_nonoverlapping_pair_gets_zero_covariance_and_is_flagged(tmp_path):
    # Two names whose trading windows don't overlap at all -> no joint history. Covariance is set
    # to 0 (no information) and counted, never fabricated; the matrix stays PSD (diagonal).
    cfg = Config(data_dir=tmp_path,
                 portfolio=PortfolioConfig(min_return_obs=30, cov_lookback_days=400))
    c = storage.connect(cfg)
    grid = pd.bdate_range(end="2026-06-12", periods=201)
    rng = np.random.default_rng(2)
    try:
        storage.load_table(c, pd.concat([
            _prices_on_grid("EARLY", rng.normal(0, 0.01, 60).tolist(), grid[:61]),
            _prices_on_grid("LATE", rng.normal(0, 0.01, 80).tolist(), grid[120:201]),
        ], ignore_index=True), "prices")
        ce = pf.estimate_covariance(c, grid[-1].date(), ["EARLY", "LATE"], cfg)
    finally:
        c.close()
    assert set(ce.tickers) == {"EARLY", "LATE"}
    e, ln = ce.tickers.index("EARLY"), ce.tickers.index("LATE")
    assert ce.cov[e, ln] == 0.0                               # disjoint windows -> 0 covariance
    assert ce.n_thin_pairs == 1
    assert float(np.linalg.eigvalsh(ce.cov).min()) >= -1e-8


def test_construct_on_ragged_universe_allocates_the_delisted_name(tmp_path):
    cfg = Config(data_dir=tmp_path,
                 portfolio=PortfolioConfig(min_return_obs=30, cov_lookback_days=400,
                                           max_position_weight=1.0))
    c = storage.connect(cfg)
    grid = pd.bdate_range(end="2026-06-12", periods=201)
    rng = np.random.default_rng(3)
    try:
        storage.load_table(c, pd.concat([
            _prices_on_grid("AA", rng.normal(0, 0.01, 200).tolist(), grid),
            _prices_on_grid("BB", rng.normal(0, 0.012, 200).tolist(), grid),
            _prices_on_grid("DEAD", rng.normal(0, 0.011, 100).tolist(), grid[:101]),
        ], ignore_index=True), "prices")
        res = pf.construct_portfolio(c, grid[-1].date(), tickers=["AA", "BB", "DEAD"],
                                     method="hrp", cfg=cfg)
    finally:
        c.close()
    assert res.weights is not None
    assert res.weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert "DEAD" in res.weights.index                        # delisted name still allocated
    assert res.dropped == {}
