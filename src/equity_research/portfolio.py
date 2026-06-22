"""v2 portfolio construction -- risk-based weighting + capital-preservation guardrails.

Honesty posture (mirrors the rest of the platform):
  - Risk-based weighting (inverse-variance, equal-risk-contribution, HRP) needs ONLY a
    covariance matrix estimated from point-in-time daily returns. No return forecast is
    introduced, so this stays inside non-negotiable #1 (no fabricated numbers).
  - Names without enough price history (or with zero return variance) are DROPPED and the
    reason is recorded -- never imputed, never given a fabricated risk estimate.
  - Fractional-Kelly is provided as pure math (`fractional_kelly`) that REQUIRES an explicit
    expected-return vector `mu`. The live orchestrator never manufactures `mu`; if a caller
    asks for the Kelly method without supplying one, the result reads "Not estimated".
  - Hard position and liquidity caps are the capital-preservation guardrails; when the
    portfolio cannot be fully deployed under a cap, the un-deployable fraction is reported
    honestly rather than forced.

Math references: equal-risk-contribution via cyclical coordinate descent
(Griveau-Billion, Richard & Roncalli 2013); Hierarchical Risk Parity (Lopez de Prado 2016).
The HRP clustering is implemented in pure numpy (no scipy dependency).
"""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from equity_research import factors, universe
from equity_research.config import Config, config
from equity_research.data import pit
from equity_research.disclaimer import NOT_ESTIMATED, PERSISTENT_DISCLAIMER

WEIGHT_METHODS = ("inverse_variance", "risk_parity", "hrp")


# --------------------------------------------------------------------------- #
# Covariance estimation (point-in-time, honest dropping)
# --------------------------------------------------------------------------- #
@dataclass
class CovEstimate:
    tickers: list[str]            # kept names, in matrix order
    cov: np.ndarray               # annualized covariance (n x n)
    corr: np.ndarray              # correlation (n x n)
    vols: np.ndarray              # annualized volatilities (n,)
    dropped: dict[str, str]       # ticker -> reason it was excluded
    n_obs: int                    # aligned daily-return observations used


def estimate_covariance(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    tickers: list[str],
    cfg: Config = config,
) -> CovEstimate:
    """Annualized covariance/correlation from PIT daily returns over the kept names.

    Names with fewer than `min_return_obs` daily returns, or zero return variance over the
    window, are dropped (with a reason) instead of being imputed. Survivors are aligned on
    their common trading dates before the sample covariance is taken.
    """
    pcfg = cfg.portfolio
    tickers = [t.upper() for t in dict.fromkeys(tickers)]  # de-dup, preserve order
    dropped: dict[str, str] = {}
    empty = CovEstimate([], np.empty((0, 0)), np.empty((0, 0)), np.empty((0,)), dropped, 0)

    px = pit.prices_asof(conn, as_of, pcfg.cov_lookback_days, tickers)
    if px.empty:
        for t in tickers:
            dropped[t] = "no price history within the lookback window"
        return empty

    wide = px.pivot_table(index="date", columns="ticker", values="closeadj").sort_index()
    rets = wide.pct_change()
    counts = rets.count()  # non-NaN returns per name

    keep = []
    for t in tickers:
        n = int(counts[t]) if t in counts.index else 0
        if n >= pcfg.min_return_obs:
            keep.append(t)
        else:
            dropped[t] = f"insufficient history ({n} < {pcfg.min_return_obs} daily returns)"
    if not keep:
        return empty

    aligned = rets[keep].dropna(how="any")  # common trading dates across survivors
    stds = aligned.std(ddof=1)
    nonzero = [t for t in keep if stds.get(t, 0.0) > 0.0]
    for t in keep:
        if t not in nonzero:
            dropped[t] = "zero return variance over the window (flat price)"
    if not nonzero:
        return empty
    aligned = aligned[nonzero]

    cov_daily = aligned.cov()        # ddof=1
    corr = aligned.corr().values
    cov = cov_daily.values * 252.0   # annualize
    vols = np.sqrt(np.diag(cov))
    return CovEstimate(list(aligned.columns), cov, corr, vols, dropped, len(aligned))


# --------------------------------------------------------------------------- #
# Weighting schemes (take an annualized covariance; return weights summing to 1)
# --------------------------------------------------------------------------- #
def inverse_variance_weights(cov: np.ndarray) -> np.ndarray:
    """Naive risk parity: w_i proportional to 1 / variance_i."""
    var = np.diag(cov)
    inv = 1.0 / var
    return inv / inv.sum()


def risk_parity_weights(
    cov: np.ndarray, budgets: np.ndarray | None = None,
    max_iter: int = 10_000, tol: float = 1e-12,
) -> np.ndarray:
    """Equal-risk-contribution weights via cyclical coordinate descent.

    Minimizes 0.5 w'Cw - sum b_i ln(w_i); the first-order condition per asset i is the
    quadratic  C_ii w_i^2 + (sum_{j!=i} C_ij w_j) w_i - b_i = 0, whose positive root is the
    coordinate update. Iterated cyclically to a fixed point, then normalized. With equal
    budgets b_i = 1/n the result equalizes each name's risk contribution.
    """
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    b = np.full(n, 1.0 / n) if budgets is None else np.asarray(budgets, float) / np.sum(budgets)
    w = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        w_prev = w.copy()
        for i in range(n):
            beta = float(cov[i] @ w - cov[i, i] * w[i])  # sum_{j!=i} C_ij w_j
            a = float(cov[i, i])
            w[i] = (-beta + np.sqrt(beta * beta + 4.0 * a * b[i])) / (2.0 * a)
        if np.max(np.abs(w - w_prev)) < tol:
            break
    return w / w.sum()


def risk_contributions(cov: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Fractional risk contributions RC_i = w_i (Cw)_i / (w'Cw); sums to 1 (NaN if no risk)."""
    port_var = float(w @ cov @ w)
    if port_var <= 0:
        return np.full(len(w), np.nan)
    return w * (cov @ w) / port_var


# --- HRP (Lopez de Prado), pure-numpy clustering --------------------------- #
def _cov_to_corr(cov: np.ndarray) -> np.ndarray:
    d = np.sqrt(np.diag(cov))
    corr = cov / np.outer(d, d)
    np.fill_diagonal(corr, 1.0)
    return np.clip(corr, -1.0, 1.0)


def _corr_dist(corr: np.ndarray) -> np.ndarray:
    """Correlation distance d_ij = sqrt(0.5 (1 - rho_ij))."""
    return np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))


def _euclidean_of(dist: np.ndarray) -> np.ndarray:
    """Pairwise Euclidean distance between the rows of a distance matrix (LdP linkage input)."""
    n = dist.shape[0]
    e = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            e[i, j] = e[j, i] = float(np.sqrt(((dist[i] - dist[j]) ** 2).sum()))
    return e


def _single_linkage(e: np.ndarray) -> np.ndarray:
    """Single-linkage agglomerative clustering -> SciPy-style linkage matrix (n-1 x 4).

    Each row is [cluster_id_a, cluster_id_b, merge_distance, merged_size]; original points
    are ids 0..n-1 and merges create ids n, n+1, ... Single linkage = nearest member pair.
    """
    n = e.shape[0]
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    ids = list(range(n))
    z: list[list[float]] = []
    next_id = n

    def cdist(a: int, b: int) -> float:
        return float(e[np.ix_(members[a], members[b])].min())

    while len(ids) > 1:
        best = None
        for a, b in itertools.combinations(ids, 2):
            d = cdist(a, b)
            if best is None or d < best[0]:
                best = (d, a, b)
        d, a, b = best  # type: ignore[misc]
        members[next_id] = members[a] + members[b]
        z.append([float(a), float(b), d, float(len(members[next_id]))])
        ids.remove(a)
        ids.remove(b)
        ids.append(next_id)
        next_id += 1
    return np.array(z)


def _quasi_diag(link: np.ndarray, n: int) -> list[int]:
    """Reorder original items by the cluster tree so correlated names sit adjacently."""
    sort_ix = [int(link[-1, 0]), int(link[-1, 1])]
    while max(sort_ix) >= n:
        expanded: list[int] = []
        for i in sort_ix:
            if i < n:
                expanded.append(i)
            else:
                row = link[i - n]
                expanded.append(int(row[0]))
                expanded.append(int(row[1]))
        sort_ix = expanded
    return sort_ix


def _cluster_var(cov: np.ndarray, items: list[int]) -> float:
    sub = cov[np.ix_(items, items)]
    w = inverse_variance_weights(sub)
    return float(w @ sub @ w)


def _recursive_bisection(cov: np.ndarray, sort_ix: list[int]) -> np.ndarray:
    """Allocate top-down: split each cluster, weight the halves inversely to their variance."""
    w = np.ones(cov.shape[0])
    clusters = [sort_ix]
    while clusters:
        nxt: list[list[int]] = []
        for c in clusters:
            if len(c) <= 1:
                continue
            mid = len(c) // 2
            c0, c1 = c[:mid], c[mid:]
            v0, v1 = _cluster_var(cov, c0), _cluster_var(cov, c1)
            alpha = 1.0 - v0 / (v0 + v1)  # lower-variance half gets the larger share
            for i in c0:
                w[i] *= alpha
            for i in c1:
                w[i] *= 1.0 - alpha
            nxt += [c0, c1]
        clusters = nxt
    return w / w.sum()


def hrp_weights(cov: np.ndarray) -> np.ndarray:
    """Hierarchical Risk Parity (Lopez de Prado): cluster, quasi-diagonalize, bisect."""
    n = cov.shape[0]
    if n == 1:
        return np.array([1.0])
    corr = _cov_to_corr(cov)
    e = _euclidean_of(_corr_dist(corr))
    link = _single_linkage(e)
    order = _quasi_diag(link, n)
    return _recursive_bisection(cov, order)


# --------------------------------------------------------------------------- #
# Capital-preservation guardrails
# --------------------------------------------------------------------------- #
def _cap_and_redistribute(
    weights: np.ndarray, caps: np.ndarray, max_iter: int = 1000
) -> tuple[np.ndarray, list[int], float]:
    """Cap weights at per-name upper bounds, redistributing excess to names with headroom.

    Excess from capped names is spread pro-rata to the current weights of names that still
    have room (falling back to headroom-proportional when those weights are ~0). Returns the
    capped weights, the indices that bind, and the un-deployable fraction (0 unless caps make
    a fully-invested portfolio impossible).
    """
    w = np.asarray(weights, dtype=float).copy()
    caps = np.asarray(caps, dtype=float)
    input_sum = float(w.sum())  # measure undeployment against what we were handed, not 1.0
    for _ in range(max_iter):
        over = w > caps + 1e-12
        if not over.any():
            break
        excess = float((w[over] - caps[over]).sum())
        w[over] = caps[over]
        head = caps - w
        free = head > 1e-12
        if not free.any():
            break  # excess cannot be placed; reflected in undeployed below
        base = w[free]
        weight_for_dist = base if base.sum() > 1e-12 else head[free]
        w[free] += excess * weight_for_dist / weight_for_dist.sum()
    binding = [i for i in range(len(w)) if np.isfinite(caps[i]) and w[i] >= caps[i] - 1e-9]
    undeployed = max(0.0, input_sum - float(w.sum()))  # only the shortfall THIS cap caused
    return w, binding, undeployed


def apply_position_cap(
    weights: np.ndarray, cap: float
) -> tuple[np.ndarray, list[int], list[str]]:
    """Cap each weight at `cap`, redistribute the excess. Reports binding names + notes."""
    caps = np.full(len(weights), float(cap))
    w, binding, undeployed = _cap_and_redistribute(weights, caps)
    notes: list[str] = []
    if undeployed > 1e-6:
        notes.append(
            f"position cap {cap:.0%} leaves {undeployed:.1%} undeployed "
            f"(too few names to satisfy the cap)"
        )
    return w, binding, notes


def apply_sector_cap(
    weights: np.ndarray, sectors: list[str], cap: float, unknown_label: str = "Unknown",
    max_iter: int = 1000,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Cap the AGGREGATE weight of each sector at `cap`, redistributing to non-binding sectors.

    For any sector summing above `cap`, its names are scaled down pro-rata so the sector totals
    `cap`; the freed weight is spread to names outside the binding sectors (pro-rata to current
    weight). Iterated to a fixpoint. `unknown_label` names are exempt from the group cap (their
    sector is indeterminate) -- they can still receive redistribution. Returns the capped weights,
    the list of binding sector labels, and notes (incl. any un-deployable shortfall).
    """
    w = np.asarray(weights, dtype=float).copy()
    n = len(w)
    idx_of: dict[str, list[int]] = {}
    for i, sec in enumerate(sectors):
        idx_of.setdefault(sec, []).append(i)

    binding: set[str] = set()
    undeployed = 0.0
    for _ in range(max_iter):
        totals = {sec: float(w[ix].sum()) for sec, ix in idx_of.items()}
        over = {sec for sec, tot in totals.items()
                if sec != unknown_label and tot > cap + 1e-12}
        if not over:
            break
        binding |= over
        freed = 0.0
        for sec in over:
            scale = cap / totals[sec]
            for i in idx_of[sec]:
                freed += w[i] * (1.0 - scale)
                w[i] *= scale
        recip = [i for i in range(n) if sectors[i] not in over]
        base = w[recip] if recip else np.empty(0)
        if recip and base.sum() > 1e-12:
            w[recip] += freed * base / base.sum()
        elif recip:
            w[recip] += freed / len(recip)
        else:
            undeployed += freed  # nowhere to place it (e.g. a single sector)
            break

    notes: list[str] = []
    if undeployed > 1e-6:
        notes.append(f"sector cap {cap:.0%} leaves {undeployed:.1%} undeployed (too few sectors)")
    return w, sorted(binding), notes


def apply_liquidity_cap(
    weights: np.ndarray,
    adv: np.ndarray,
    portfolio_value: float,
    max_participation: float,
) -> tuple[np.ndarray, list[int], list[str]]:
    """Cap each position so its dollar value <= max_participation * ADV. NaN ADV = uncapped."""
    adv = np.asarray(adv, dtype=float)
    max_w = max_participation * adv / portfolio_value
    caps = np.where(np.isnan(max_w), np.inf, max_w)
    w, binding, undeployed = _cap_and_redistribute(weights, caps)
    notes: list[str] = []
    if undeployed > 1e-6:
        notes.append(
            f"{undeployed:.1%} of the portfolio cannot be deployed at "
            f"${portfolio_value:,.0f} without a position exceeding "
            f"{max_participation:.0%} of its ADV"
        )
    return w, binding, notes


# --------------------------------------------------------------------------- #
# Fractional-Kelly (pure math; REQUIRES an explicit expected-return vector)
# --------------------------------------------------------------------------- #
@dataclass
class KellyResult:
    f_full: np.ndarray            # full-Kelly leverage  = C^-1 mu
    f_fractional: np.ndarray      # fraction * f_full
    f_capped: np.ndarray          # f_fractional clipped to [-cap, cap]
    long_only_weights: np.ndarray  # long-only, position-capped, normalized weights
    fraction: float
    cap: float


def fractional_kelly(
    mu: np.ndarray, cov: np.ndarray, fraction: float, cap: float
) -> KellyResult:
    """Fractional-Kelly leverage f = fraction * C^-1 mu, plus a capped long-only weight set.

    `mu` is the vector of expected EXCESS returns; the caller must supply it -- this function
    never invents one. `fraction` < 1 and the per-position `cap` are the capital-preservation
    guardrails (`f_capped` shows the leverage clipped to +/- cap). `long_only_weights` clip
    shorts to zero and normalize to sum 1; position caps are left to the caller
    (`construct_portfolio`) so all capping happens in one place. Weights are NaN if no asset
    has a positive Kelly tilt.
    """
    mu = np.asarray(mu, dtype=float)
    cov = np.asarray(cov, dtype=float)
    try:
        f_full = np.linalg.solve(cov, mu)
    except np.linalg.LinAlgError:
        f_full = np.linalg.pinv(cov) @ mu  # singular covariance -> min-norm Kelly solution
    f_frac = fraction * f_full
    f_capped = np.clip(f_frac, -cap, cap)

    long_only = np.clip(f_frac, 0.0, None)
    if long_only.sum() > 0:
        w = long_only / long_only.sum()
    else:
        w = np.full(len(mu), np.nan)
    return KellyResult(f_full, f_frac, f_capped, w, float(fraction), float(cap))


# --------------------------------------------------------------------------- #
# Orchestrator + honest result object
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioResult:
    method: str
    as_of: dt.date
    portfolio_value: float
    weights: pd.Series | None = None
    risk_contributions: pd.Series | None = None
    dropped: dict[str, str] = field(default_factory=dict)
    caps_binding: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    sector_weights: dict[str, float] = field(default_factory=dict)
    sector_cap: float | None = None
    selection: str = ""
    n_obs: int = 0

    def render(self) -> str:
        lines = [
            f"Portfolio construction - method: {self.method} | as of {self.as_of}",
            f"Selection: {self.selection or NOT_ESTIMATED} | "
            f"portfolio value ${self.portfolio_value:,.0f} | cov obs: {self.n_obs}",
            "",
        ]
        if self.weights is None or len(self.weights) == 0:
            lines.append(f"Weights: {NOT_ESTIMATED}")
        else:
            lines.append(f"{'Ticker':<10}{'Weight':>10}{'RiskContrib':>14}")
            for t, wv in self.weights.items():
                rc = None
                if self.risk_contributions is not None and t in self.risk_contributions.index:
                    rc = self.risk_contributions[t]
                rc_str = f"{rc * 100:>13.1f}%" if rc is not None and pd.notna(rc) else f"{'NA':>14}"
                lines.append(f"{t:<10}{wv * 100:>9.1f}%{rc_str}")
            lines.append(f"{'TOTAL':<10}{self.weights.sum() * 100:>9.1f}%")
        if self.dropped:
            lines.append("")
            lines.append("Dropped (insufficient data; never imputed):")
            lines += [f"  - {t}: {reason}" for t, reason in self.dropped.items()]
        if self.assumptions:
            lines.append("")
            lines.append("Expected returns used (USER-SUPPLIED inputs, NOT computed by this tool):")
            lines += [f"  - {a}" for a in self.assumptions]
        if self.sector_weights:
            lines.append("")
            cap_str = f" (cap {self.sector_cap:.0%})" if self.sector_cap else ""
            lines.append(f"Sector exposure{cap_str} - current SIC, coarse, not point-in-time:")
            for sec, wv in self.sector_weights.items():
                at_cap = (self.sector_cap is not None and sec != "Unknown"
                          and wv >= self.sector_cap - 1e-9)
                lines.append(f"  {sec:<24}{wv * 100:>6.1f}%{'  <= cap' if at_cap else ''}")
        if self.caps_binding:
            lines.append("")
            lines.append(f"Caps binding: {', '.join(self.caps_binding)}")
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines += [f"  - {n}" for n in self.notes]
        body = "\n".join(lines)
        return f"{body}\n{'-' * 60}\nDISCLAIMER: {PERSISTENT_DISCLAIMER}"


def construct_portfolio(
    conn: duckdb.DuckDBPyConnection,
    as_of: dt.date,
    tickers: list[str] | None = None,
    method: str | None = None,
    cfg: Config = config,
    portfolio_value: float | None = None,
    mu: dict[str, float] | None = None,
    kelly_fraction: float | None = None,
) -> PortfolioResult:
    """Build portfolio weights for a basket via the chosen risk-based method (+ caps).

    Names default to the top-N of the factor screen by composite when `tickers` is omitted.
    `method` is one of inverse_variance / risk_parity / hrp, or "kelly" -- which requires an
    explicit `mu` (expected EXCESS returns, supplied by the caller, never invented). For the
    Kelly method the basket defaults to the names `mu` covers; the expected returns are echoed
    back as user assumptions in the result.
    """
    pcfg = cfg.portfolio
    method = (method or pcfg.default_method).lower()
    pv = portfolio_value if portfolio_value is not None else pcfg.default_portfolio_value_usd
    frac = kelly_fraction if kelly_fraction is not None else pcfg.kelly_fraction
    mu = {k.upper(): float(v) for k, v in mu.items()} if mu else None
    notes: list[str] = []

    # 1. Resolve the basket of names.
    if tickers:
        names = [t.upper() for t in dict.fromkeys(tickers)]
        selection = "user-specified"
    elif method == "kelly" and mu:
        names = list(mu.keys())  # Kelly is view-driven: the edge set IS the basket
        selection = "Kelly edge set (user-supplied)"
    else:
        prof = factors.factor_profile(conn, as_of, cfg)
        if prof.empty:
            return PortfolioResult(
                method, as_of, pv,
                notes=["No screening universe loaded; pull names first (pull / pull-sp500)."],
                selection="top-N by factor composite",
            )
        names = prof.head(pcfg.default_top_n).index.tolist()
        selection = f"top {len(names)} by factor composite"

    # 2. Estimate point-in-time covariance (drops, never imputes).
    ce = estimate_covariance(conn, as_of, names, cfg)
    if not ce.tickers:
        return PortfolioResult(
            method, as_of, pv, dropped=ce.dropped, selection=selection,
            notes=notes + ["Covariance not estimable for any requested name."],
        )
    if ce.n_obs < pcfg.min_return_obs:
        notes.append(
            f"covariance rests on {ce.n_obs} aligned daily returns "
            f"(< {pcfg.min_return_obs} target); confidence reduced"
        )
    kept, cov = ce.tickers, ce.cov
    assumptions: list[str] = []

    # 3. Weight.
    if method == "kelly":
        missing = [t for t in kept if mu is None or t not in mu]
        if missing:
            notes.append(
                "Kelly sizing requires an explicit expected-return vector (mu); the tool does "
                "not fabricate expected returns. Provide an edge for every name to use it."
            )
            if mu is not None:
                notes.append(f"missing edges for: {', '.join(missing)}")
            return PortfolioResult(
                method, as_of, pv, dropped=ce.dropped, selection=selection,
                notes=notes, n_obs=ce.n_obs,
            )
        mu_vec = np.array([mu[t] for t in kept])
        kr = fractional_kelly(mu_vec, cov, frac, pcfg.max_position_weight)
        w = kr.long_only_weights
        assumptions = [f"{t}: {mu[t]:+.2%} expected excess return" for t in kept]
        if np.isnan(w).any():
            notes.append(
                "No asset has a positive fractional-Kelly tilt at these edges; nothing to "
                "deploy long-only."
            )
            return PortfolioResult(
                method, as_of, pv, dropped=ce.dropped, selection=selection,
                notes=notes, assumptions=assumptions, n_obs=ce.n_obs,
            )
        gross = float(np.sum(kr.f_fractional))
        notes.append(
            f"fractional-Kelly at {frac:.2f}x implies {gross:.2f}x gross leverage; deployed "
            f"weights are normalized long-only and position-capped (leverage not applied)"
        )
    elif method == "inverse_variance":
        w = inverse_variance_weights(cov)
    elif method == "risk_parity":
        w = risk_parity_weights(cov)
    elif method == "hrp":
        w = hrp_weights(cov)
    else:
        raise ValueError(f"unknown method {method!r}; choose one of {WEIGHT_METHODS} or 'kelly'")

    # 4. Guardrails. Sector classification + ADV are fixed inputs; the three caps (position,
    #    sector, liquidity) are then iterated to a JOINT fixpoint, so the final weights respect
    #    all three at once -- not just whichever was applied last (a single pass lets a later cap's
    #    redistribution push a name/sector back over an earlier cap).
    sector_map = pit.sectors_for(conn, kept)
    secs: list[str] | None = [sector_map.get(t, "Unknown") for t in kept] if sector_map else None
    if secs is None:
        notes.append("No sector data -> sector cap skipped (run pull-sectors to enable it).")
    else:
        n_unknown = sum(1 for s in secs if s == "Unknown")
        if n_unknown:
            notes.append(f"{n_unknown} name(s) lack a SIC sector -> exempt from the sector cap")

    scr = universe.screening_universe(conn, as_of, cfg)
    adv_map = scr.set_index("ticker")["avg_dollar_volume"].to_dict() if not scr.empty else {}
    adv = np.array([adv_map.get(t, np.nan) for t in kept])
    n_missing_adv = int(np.isnan(adv).sum())
    if n_missing_adv:
        notes.append(
            f"{n_missing_adv} name(s) lack ADV (not in the liquid screen); the liquidity cap "
            f"could not be applied to them"
        )

    pos_binding: list[int] = []
    liq_binding: list[int] = []
    cap_notes: list[str] = []
    for _ in range(50):  # converges in a few passes when the caps are jointly feasible
        w_prev = w.copy()
        w, pos_binding, pos_notes = apply_position_cap(w, pcfg.max_position_weight)
        sec_notes: list[str] = []
        if secs is not None:
            w, _sector_binding, sec_notes = apply_sector_cap(w, secs, pcfg.max_sector_weight)
        w, liq_binding, liq_notes = apply_liquidity_cap(w, adv, pv, pcfg.max_adv_participation)
        cap_notes = pos_notes + sec_notes + liq_notes  # keep only the converged pass's notes
        if float(np.max(np.abs(w - w_prev))) < 1e-12:
            break
    notes += cap_notes

    # Honest diagnostic: if the caps could not be jointly reconciled, say so rather than
    # silently returning weights that breach one of them.
    breaches: list[str] = []
    if w.size and float(w.max()) > pcfg.max_position_weight + 1e-6:
        breaches.append(f"a position exceeds {pcfg.max_position_weight:.0%}")
    if secs is not None:
        sect_tot: dict[str, float] = {}
        for s_, wv in zip(secs, w, strict=True):
            if s_ != "Unknown":
                sect_tot[s_] = sect_tot.get(s_, 0.0) + float(wv)
        if sect_tot and max(sect_tot.values()) > pcfg.max_sector_weight + 1e-6:
            breaches.append(f"a sector exceeds {pcfg.max_sector_weight:.0%}")
    if breaches:
        notes.append("caps not fully reconciled (" + "; ".join(breaches) +
                     ") - likely jointly too tight for this universe")

    # 5. Transparency: realized risk contributions + sector exposure of the final weights.
    rc = risk_contributions(cov, w)
    weights_series = pd.Series(w, index=kept).sort_values(ascending=False)
    rc_series = pd.Series(rc, index=kept).reindex(weights_series.index)
    caps_binding = sorted({kept[i] for i in pos_binding} | {kept[i] for i in liq_binding})

    sector_weights: dict[str, float] = {}
    if secs is not None:
        agg: dict[str, float] = {}
        for t, wv in weights_series.items():
            agg[sector_map.get(t, "Unknown")] = agg.get(sector_map.get(t, "Unknown"), 0.0) + wv
        sector_weights = dict(sorted(agg.items(), key=lambda kv: -kv[1]))

    return PortfolioResult(
        method, as_of, pv, weights=weights_series, risk_contributions=rc_series,
        dropped=ce.dropped, caps_binding=caps_binding, notes=notes,
        assumptions=assumptions, sector_weights=sector_weights,
        sector_cap=(pcfg.max_sector_weight if secs is not None else None),
        selection=selection, n_obs=ce.n_obs,
    )
