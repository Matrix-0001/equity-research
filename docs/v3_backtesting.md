# v3 — Validated Backtesting & Multi-Regime Stress (build spec)

**Status:** Not started · **Prereq:** v1 + v2 complete on `main` (pushed to GitHub) · 87/87 tests green.
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
- [ ] **Authorize Sharadar spend** for valid (survivorship-free) backtests, and confirm price +
      non-professional license at signup (Phase 0 §7.1)? **Deferred** — no spend authorized yet;
      Phase B stays unwritten until this box is ticked.

Until the lane is chosen, Phase B is not written. That gate is working as intended.

### Decision log

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

## How to start the next session (paste into the new chat)

> I'm Darrell, continuing the Local Equity Research & Portfolio Analysis Platform at
> `C:\Users\User\Documents\Projects\equity-research`. Address me as Darrell.
>
> FIRST read: `CLAUDE.md`, `README.md`, `docs/phase0_data_inventory.md`, and
> `docs/v3_backtesting.md` (the v3 build spec).
>
> State of play: **v1 + v2 are COMPLETE and committed on `main`** (pushed to GitHub
> `Matrix-0001/equity-research`); `87/87` tests pass (`.venv\Scripts\python -m pytest -q`). Lane 1
> (free) data: EDGAR fundamentals + Yahoo prices + FRED/ALFRED macro. `FRED_API_KEY` is in the local
> `.env` (git-ignored — never commit it). v2 = risk parity / HRP / inverse-variance weighting,
> fractional Kelly (explicit `--edge` input), and position/sector/liquidity caps (joint fixpoint).
>
> Non-negotiables: no fabricated numbers ("Not estimated"); point-in-time or nothing; costs always
> included in any backtest; uncertainty as ranges + Low/Med/High confidence; out-of-sample discipline
> (locked test set, walk-forward, deflated Sharpe); stamp "indicative only" when survivorship/lookahead
> risk is present.
>
> Today: **start v3 (validated backtesting)** per `docs/v3_backtesting.md`. Begin with the §7 data-lane
> decision, then Phase A (the engine + cost model on free data, indicative-only). Working norms: tests
> for every analytical piece (synthetic fixtures, hand-computed expected values); keep CLI/print output
> ASCII (Windows cp1252); commit only when I ask.
