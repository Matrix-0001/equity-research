# v3 — Validated Backtesting & Multi-Regime Stress (build spec)

**Status:** Phase A + B + C COMPLETE (all on free data) · pushed to GitHub `main`.
**Owner:** Darrell. **This is decision-support, not advice.**

> Read first: `CLAUDE.md` (non-negotiables, architecture), `README.md`, and
> `docs/phase0_data_inventory.md` (the data-lane honesty report). v3 is where the data lane
> finally bites — read Phase 0 §5-§6 before writing engine code.

---

## 1. Goal

Backtest the v1 screen + v2 portfolios **honestly**: point-in-time, survivorship-aware, **net of
costs**, with **out-of-sample discipline**. The deliverable is an engine that can say, with a
straight face, "this is what the strategy would have returned, net of costs, using only data
knowable at each date" — or else **refuse and stamp the result "indicative only."**

## 2. Non-negotiables this phase must honor (from CLAUDE.md)

1. **Point-in-time or nothing.** Every rebalance sees only `datekey/date <= as_of`. Delisted names
   are never silently dropped; a name that delists contributes its return *to* delisting plus the
   delisting outcome. (`data/pit.py` already enforces the read side.)
2. **Costs always included.** Transaction costs = half-spread + commission (bps) + market impact for
   the *actual position size* vs ADV, plus a configurable tax drag. Pre-cost results never shown alone.
3. **Out-of-sample discipline is structural.** Locked test set (hold out the most recent period),
   **walk-forward only** (no parameter or selection uses future data), and **multiple-testing
   correction (Deflated Sharpe)** whenever more than one strategy/parameter set is compared.
4. **Uncertainty as ranges + confidence tiers**, never a single hero number.
5. **Honesty stamp on every run:** the data lane + whether the run is *valid* or
   *"indicative only — survivorship/lookahead risk present."*

## 3. The data gate (decide before coding the survivorship-free path)

Free Lane-1 prices (Yahoo) are **survivorship-biased** and EDGAR has **no point-in-time index
membership**. Consequences:

- **Lane 1 (free):** the engine, cost model, walk-forward plumbing, and stats are all buildable and
  valuable — but any backtest is **stamped "indicative only"** (winners over-counted, universe not
  survivorship-free). Good for validating the machinery, not for a defensible track record.
- **Lane 2 (Sharadar SF1 + SEP + SP500):** survivorship-free fundamentals + delisted prices + PIT
  index membership → **valid** backtests back to ~1998 (fundamentals ~2000; dot-com only partial).
  The adapter is stubbed at `data/sharadar.py`; needs `NASDAQ_DATA_LINK_API_KEY` (Phase 0 §7.1 —
  confirm price + non-professional license at signup). Storage/PIT/universe/factor layers don't change.

**Recommended path:** build **Phase A on free data (indicative-only)** first to prove the engine, then
authorize Sharadar and flip the lane for Phase B. Ask Darrell to confirm the spend before Phase B.

## 4. Architecture (new code; reuse the rest)

- **`backtest.py`** — walk-forward engine. At each rebalance date `d`:
  1. universe = `universe.screening_universe(conn, d)` (or backtest_universe for survivorship-free);
  2. signal = `factors.factor_profile` / a fixed rule; target weights = `portfolio.construct_portfolio`
     (reuse v2: risk parity / HRP + caps);
  3. hold to next rebalance; realized return from `pit.prices_asof` (delisted-aware);
  4. charge turnover costs at `d` and tax drag on realized gains.
  Tracks a net-of-cost NAV series, turnover, and per-period returns.
- **`costs.py`** — `trade_cost(weight_change, adv, price, cfg)` = half-spread + commission_bps +
  impact(participation). Pure, hand-testable. Config in a new `BacktestConfig` (spread bps, commission
  bps, impact coefficient, tax rate, rebalance frequency, locked-test-set start).
- **`metrics.py`** — CAGR, ann. vol, **net Sharpe**, Sortino, max drawdown, turnover, hit rate;
  **Deflated Sharpe Ratio** (Bailey & López de Prado) for multiple-testing correction; per-regime
  breakdown (tag dates: dot-com 2000-02, GFC 2007-09, COVID 2020, 2022 drawdown, else "normal").
- **`disclaimer.py`** — add a `BacktestResult` dataclass (NAV, stats, regime table, costs summary,
  **lane + validity stamp**, "Not estimated" where unfillable) with a `render()` like `SecurityReport`.
- **`cli.py`** — `backtest [--strategy hrp|risk_parity|composite] [--start --end] [--rebalance
  monthly|quarterly] [--lane free|sharadar]`. ASCII output, persistent disclaimer.

## 5. Build order (validate each before the next)

- **Phase A — engine on free data (indicative-only).** Cost model + single walk-forward backtest of one
  fixed strategy on the current universe; net-of-cost NAV + core stats; every output stamped
  "indicative only — survivorship/lookahead risk present." Proves the machinery honestly.
- **Phase B — survivorship-free (needs Sharadar).** Wire `data/sharadar.py`; backtest_universe becomes
  delisted-inclusive with PIT membership; drop the indicative stamp for runs that qualify.
- **Phase C — discipline + stress.** Locked test set + walk-forward enforcement; multi-regime stress
  table; Deflated Sharpe whenever multiple strategies/params are compared.

## 6. Tests (synthetic fixtures, hand-computed — same norm as the rest)

- **No-lookahead:** a fixture where future data *would* change selection; assert the rebalance at `d`
  ignores it (uses only `<= d`).
- **Cost accounting:** known turnover × known cost rate → exact expected drag; pre- vs post-cost differ.
- **NAV compounding:** synthetic period returns → hand-computed NAV and max drawdown.
- **Deflated Sharpe:** hand-checked against the published formula for a known (SR, n_trials, skew, kurt).
- **Regime tagging:** dates map to the right regime buckets.
- **Delisting:** a name that stops trading mid-backtest contributes correctly (no silent survivorship).

## 7. Decision required from Darrell (Phase-0-style gate)

- [x] **Lane for v3:** build **Phase A on free Lane-1 data first (indicative-only)** to prove the
      engine. *(Decided 2026-06-22 — see decision log below.)*
- [x] **Phase B lane: FREE DIY (not Sharadar).** Sharadar declined on cost; Phase B built on
      `fja05680` PIT membership (MIT) + SimFin free delisted prices. *(Decided 2026-06-23 — below.)*
- [x] **Phase C: out-of-sample discipline + stress, built on the free lanes.** Deflated Sharpe +
      `backtest-compare`, multi-regime stress table, locked-test enforcement. *(Done 2026-06-24 — §8.)*

All gates resolved. Phases A, B, and C are complete on free data.

### Decision log

**2026-06-23 - Phase B on a FREE DIY lane (Sharadar declined).** Darrell was unwilling to pay for
Sharadar (~$100-150/mo) or even ~$22 (FMP) for a personal project. We confronted the data problem
honestly: the survivorship gap is (a) a point-in-time universe and (b) delisted prices. (a) is public
fact -> rebuilt for free from **fja05680/sp500** (MIT, Wikipedia-derived) PIT membership windows.
(b) is the scarce piece; we verified Yahoo is unusable (drops recent delistings, returns
recycled-ticker collisions for old ones), Stooq is bot-walled, and FMP's free tier gates delisted
prices -- but **SimFin's free tier serves real delisted history (~2019+, stable entity ids)**: SIVB
runs to a $0.01 close (SVB's actual collapse), PXD ends at its acquisition price. Built `--lane
simfin`: basket = PIT membership (priced names), price-only strategies (hrp/risk_parity/
inverse_variance/equal_weight) -> best-effort survivorship-free; a delisted name's crash is now felt.
*Limits accepted:* SimFin free history ~2019+ (no GFC/dot-com); coverage gaps for some names/dates are
excluded and reported (a coverage gap, not silent survivorship); composite/factor strategies need PIT
fundamentals for delisted names (we only have EDGAR for current names) so they are refused on this
lane. Stamp: BEST-EFFORT SURVIVORSHIP-FREE (not a pristine vendor-grade "valid"). The `sharadar.py`
stub remains for a future upgrade but is unused.

**2026-06-22 - Phase A on Lane 1 (free), indicative-only.** Build the walk-forward engine, the
cost model, and the net-of-cost statistics on free EDGAR + Yahoo data first, to prove the machinery
honestly. **Every run is stamped "indicative only - survivorship/lookahead risk present"**: free
Yahoo prices drop delisted names (survivorship bias) and EDGAR has no point-in-time index
membership, so a backtest over-counts winners and the universe is not survivorship-free. This is
acceptable for *validating the machinery*, never for a defensible track record.

*Limits accepted (carried from Phase 0, restated for v3):*
1. **Survivorship-biased universe** -> the validity stamp is INDICATIVE-ONLY on every Phase A run.
2. **Costs are modelled, not observed** -> spread/commission/impact/tax are explicit, auditable
   assumptions in `BacktestConfig` (not vendor fills); delisting *outcomes* (acquired vs bankrupt)
   are not modelled on free data, so a delisted name is liquidated at its last observed price
   (best case) - another reason the run is indicative-only.
3. **Out-of-sample discipline is partial in Phase A** -> the engine is point-in-time and
   walk-forward by construction (each rebalance sees only `date <= as_of`), but the *locked test
   set*, *multiple-testing correction (Deflated Sharpe)*, and *multi-regime stress table* are
   Phase C. A single realized path is reported with a Sharpe +/- 1 standard-error range, never as
   a lone hero number.

**Sharadar spend (Phase B) deferred.** No authorization to spend today. When v3 has proven useful,
revisit Phase 0 §7.1 (confirm SF1/SEP price + non-professional license at signup) and flip the lane.

---

## 8. Phase C — out-of-sample discipline + stress (COMPLETE 2026-06-24)

Phase A (engine, free/indicative), Phase B (free survivorship-free lane: fja05680 PIT membership +
SimFin free delisted prices), and **Phase C (out-of-sample discipline + multi-regime stress) are all
COMPLETE**. Each piece has hand-computed synthetic-fixture tests. What was built:

1. **Deflated Sharpe Ratio (DSR)** in `metrics.py` (Bailey & Lopez de Prado). Pure
   `deflated_sharpe_ratio(observed_sr, n_obs, n_trials, skew, kurt, sr_variance=None)` built from
   `probabilistic_sharpe_ratio` (PSR, corrects for sample length + non-normality) deflated by
   `expected_max_sharpe(n_trials, sr_variance)` (the expected best-of-N Sharpe under the null). The
   5-argument form falls back to the null sampling variance `1/(n_obs-1)`; the compare flow passes the
   real cross-trial variance. Self-contained normal CDF (`erf`) + inverse CDF (Acklam + one Halley
   step) -- the venv has no scipy. **`backtest.compare_backtests(...)`** + CLI **`backtest-compare`**
   races N strategies, reports each one's DSR with `n_trials = N`, and selects on IN-SAMPLE DSR only.
   Every single run now also reports its PSR (`Prob(SR>0)`) with the return skew/kurtosis it used.
2. **Multi-regime stress table.** `metrics.regime_of(date)` -> {dot-com 2000-2002, GFC 2007-2009,
   COVID 2020, 2022 drawdown, else normal}; `metrics.regime_breakdown` buckets each realized period by
   the regime of its end date; `BacktestResult.regime_stats` + `render()` show per-regime return / vol /
   max-drawdown. Regimes a run's window/lane never reaches read **"Not estimated"** (never a fabricated
   0). So a `simfin` run (>=2019) honestly shows dot-com/GFC as "Not estimated"; a deep `free` run can
   reach them but stays survivorship-biased/indicative.
3. **Locked test set.** `BacktestConfig.locked_test_start` is now enforced: every run splits its realized
   path into in-sample (before the lock) and the locked out-of-sample tail (on/after), reported
   separately. In `compare_backtests`, SELECTION uses only the in-sample window; the locked tail is
   scored ONCE for the winner and never used to pick it. `--locked-test-start` is on both `backtest`
   and `backtest-compare`.

Posture unchanged: ranges + confidence tiers, costs always in, stamp the lane/validity, "Not estimated"
where unfillable. CLI/print output is ASCII (Windows cp1252).

Optional follow-ons after Phase C: portfolio-level macro exposures; multi-factor joint regression;
true point-in-time GICS sectors; incremental writes in `data/simfin.py` for very large pulls.

### Phase C decision log

**2026-06-24 - Phase C built on the free lanes (no new data).** Phase C is pure analytics over the
existing engine output, so it needed no new data source. Choices made for honesty:
- **DSR `n_trials` = number of strategies actually raced** (the honest count of things tried). The
  deflation dispersion is the cross-trial variance of the in-sample per-period Sharpes; when only one
  strategy is compared, DSR reduces to the PSR (no multiple-testing claim) and the run says so.
- **Per-period vs annualized Sharpe:** PSR/DSR use the per-period Sharpe (`annual / sqrt(ppy)`); the
  rendered tables still show the familiar annualized Sharpe. Both are stated.
- **Regime windows are approximate but fixed** (documented in `metrics.REGIME_WINDOWS`); a period is
  tagged by its END date. Unreached regimes are "Not estimated", consistent with non-negotiable #1.
- **No scipy dependency added** -- the normal CDF/inverse-CDF are implemented and unit-tested against
  known quantiles, keeping the install foot-print unchanged.

### Post-Phase-C hardening (2026-06-24): survivorship-robust covariance

A code audit found that `portfolio.estimate_covariance` aligned survivors with `dropna(how="any")`
before taking the sample covariance -- so a single name that delisted mid-lookback truncated EVERY
name to the days before that delisting. That quietly compromised hrp / risk_parity / inverse_variance
on the `simfin` (survivorship-free) lane -- exactly the lane built to include delisted names. Fix: the
diagonal now uses each name's OWN full history; off-diagonals are PAIRWISE over each pair's overlap (a
pair overlapping < `min_return_obs` days carries 0 covariance, counted in `n_thin_pairs`, never
fabricated); the result is projected to the nearest PSD matrix so risk_parity / HRP stay well-defined.
**Effect on prior results:** the free lane (current names, full overlap) is bit-for-bit unchanged --
only the ragged/delisted case changes, and for the better. Pinned by three new tests (a delisted name
no longer truncates others' variance; non-overlapping pairs -> 0 covariance + flagged; ragged
end-to-end hrp). Shrinkage (e.g. Ledoit-Wolf) remains a possible future upgrade.

A second audit pass (2026-06-24) re-derived every formula (Altman/Beneish/Piotroski constants, the
two-stage reverse-DCF, the Lo-2002 Sharpe SE, PSR/DSR, ERC/HRP/Kelly/OLS) and checked every API field
mapping (EDGAR filing-date keying, ALFRED vintages incl. NBER USREC, fja05680 membership, SIC->sector).
All formulas verified correct. Two low-severity hardening fixes landed: (1) EDGAR `_pick_value` now
**refuses to mislabel a wrong-duration flow** -- a 10-Q cash-flow statement is YTD-only, so if even the
nearest flow duration is >40% off the period target the field reads "Not estimated" instead of
overstating it (latent; annual reads, which valuation/flags/factors use, are unchanged); (2) the
reverse-DCF read now carries an explicit **indicative-not-fair-value caveat**, strengthened when a firm
carries material leverage (liabilities > 60% of assets), since the model equates FCF to equity value.
Remaining known items (perf/edge, not correctness): HRP O(n^3) on large baskets; a holding gone
>staleness-window stale is marked to its last price by the engine but a name absent for >400 calendar
days would mark to 0 (near-unreachable).

### Engine perf pass (2026-06-24): run-scoped memoization + benchmark skip in compare

A backtest re-derives the SAME point-in-time views many times (the screening universe, factor profile,
and the chosen basket's covariance, each requested more than once per rebalance; and compare runs N
strategies over identical dates). Added `_cache.py` -- a plain run-scoped dict, created at the top of
`run_backtest` / `compare_backtests` and threaded (default None = off, so every existing caller/test is
unchanged) through `screening_universe`, `factor_profile`, `estimate_covariance`, and `sectors_for`,
keyed by date + config (+ ticker-set for covariance). It memoizes only PIT-deterministic views, so it
is **numerically lossless** (the hand-computed exact-NAV tests still pass) and is discarded when the run
ends (no global staleness). `compare_backtests` now shares ONE cache across strategies (the three risk
methods reuse the first's basket covariance) and runs each strategy with `with_benchmark=False` (compare
ranks the strategies, it never reads the 1/N benchmark). Measured on the local 57-name set, 2018-2025
monthly: **compare 90s -> 35s (2.5x)**; the test suite 33s -> 16s. A single hrp run is ~unchanged (~25s)
-- its cost is the genuinely per-date covariance + HRP construction, not redundancy. **Scaling to the
full S&P 500 (~500 names) is the NEXT perf step** and is algorithmic, not caching: the HRP single-linkage
is O(n^3) and the dense covariance is O(n^2) per rebalance, so 500 names is ~hundreds of times the
clustering work. Plan: cap the *construction* universe to the names that can actually hold weight under
the position cap (a 10% cap means at most ~10 names matter, so clustering 500 is wasteful), and/or
precompute standard runs + background custom runs in the UI layer rather than computing synchronously.

---

## How to start the next session (paste into the new chat)

> I'm Darrell, continuing the Local Equity Research & Portfolio Analysis Platform at
> `C:\Users\User\Documents\Projects\equity-research`. Address me as Darrell.
>
> FIRST read: `CLAUDE.md`, `README.md`, `docs/phase0_data_inventory.md`, and `docs/v3_backtesting.md`
> (the v3 build spec — especially **§8 "Phase C — how to proceed"**).
>
> State of play: **v1 + v2 + v3 Phase A, B & C are COMPLETE** (Phase C committed when you ask) on GitHub
> `Matrix-0001/equity-research` (`main`); **174/174 tests pass** (`.venv\Scripts\python -m pytest -q`).
> ALL data is FREE: EDGAR fundamentals + Yahoo prices (deep daily) + FRED/ALFRED macro; and for
> survivorship-free backtests, **fja05680 point-in-time S&P 500 membership (MIT) + SimFin free delisted
> prices (~2019+)**. Keys `FRED_API_KEY`, `FMP_API_KEY`, `SIMFIN_API_KEY` are in the local `.env`
> (git-ignored — NEVER commit them). Sharadar was evaluated and DECLINED on cost; its stub stays unused.
>
> Backtest lanes: `backtest --lane free` (Yahoo/screen = INDICATIVE ONLY) and `backtest --lane simfin`
> (PIT membership + delisted prices = best-effort survivorship-free; price-only strategies hrp /
> risk_parity / inverse_variance / equal_weight; composite refused). For the simfin lane use a dedicated
> data dir: `setx`/`$env:EQR_DATA_DIR="./data_simfin"`, then `pull-index-history` then `pull-simfin`,
> then `backtest --lane simfin`. Phase C adds `backtest-compare` (Deflated-Sharpe race) and
> `--locked-test-start` (in-sample vs out-of-sample tail) on both `backtest` and `backtest-compare`.
>
> Non-negotiables: no fabricated numbers ("Not estimated"); point-in-time or nothing; costs always
> included; uncertainty as ranges + Low/Med/High confidence; out-of-sample discipline (locked test set,
> walk-forward, Deflated Sharpe); stamp the lane/validity on every run.
>
> v3 is COMPLETE (Phases A, B, C). Optional follow-ons (see §8 end): portfolio-level macro exposures;
> multi-factor joint regression; true point-in-time GICS sectors; incremental writes in `data/simfin.py`.
> Working norms: tests for every analytical piece; ASCII CLI output (Windows cp1252); commit only when I
> ask (or via `scripts\checkpoint.ps1 "msg"`).
