# CLAUDE.md — Local Equity Research & Portfolio Analysis Platform

Personal, local, **honesty-first** quant research tool. Decision-support, **not financial advice**.
Owner: **Darrell** (address him as "Darrell" in every response).

## Governing values & non-negotiables (read first)
1. **No fabricated numbers.** Every figure traces to a computation over specific inputs. The LLM
   explains numbers; it never invents them. Missing → the literal string **"Not estimated"**.
2. **Point-in-time or nothing.** Fundamentals are keyed on filing date (`datekey <= as_of`);
   delisted names are never silently dropped. Free prices are survivorship-biased, so backtests
   stay "indicative only" until a delisted-price source is added.
3. **Uncertainty as ranges + Low/Med/High confidence**, never false-precision point probabilities.
4. **Costs always included** in any backtest/projection (not yet relevant — no backtester yet).
5. **Out-of-sample discipline** (locked test set, walk-forward, multiple-testing correction) —
   to be enforced when v3 backtesting is built.

The LLM's role is strictly bounded: translate computed metrics into narrative, check consistency,
surface caveats. It never produces a number of its own and never overrides a computed value.

## Stack & environment
- Python 3.12 at `%LOCALAPPDATA%\Programs\Python\Python312`; project venv at `.venv`.
- DuckDB + Parquet for local storage (`./data`, git-ignored). pandas/pyarrow.
- Run tests: `.venv\Scripts\python -m pytest -q`  (currently **167/167 green**).
- Optional LLM: `pip install -e .[llm]` + `ANTHROPIC_API_KEY` (default model `claude-opus-4-8`,
  override `EQR_LLM_MODEL`). Without it, narratives read "Not estimated".

## CLI (run from repo root so data lands in ./data)
```
python -m equity_research.cli pull-sp500 --limit 50   # seed universe (ACCUMULATES)
python -m equity_research.cli pull AAPL MSFT NVDA      # add/refresh specific names
python -m equity_research.cli universe                 # what's loaded
python -m equity_research.cli screen                   # factor percentile ranks, whole universe
python -m equity_research.cli report AAPL              # full SecurityReport (+ optional LLM bull/bear)
python -m equity_research.cli pull-sectors             # classify loaded names into sectors (SEC SIC)
python -m equity_research.cli construct --method hrp   # risk-based weights + position/sector/liquidity caps
python -m equity_research.cli construct --method kelly --edge AAPL=0.06 --edge MSFT=0.04  # your edges
python -m equity_research.cli pull-macro               # FRED/ALFRED macro, vintage-aware (needs FRED_API_KEY)
python -m equity_research.cli macro                    # macro values known as-of a date (no lookahead)
python -m equity_research.cli exposures AAPL           # rate/curve/credit betas + recession base rate
python -m equity_research.cli backtest --strategy hrp --start 2024-07-01 --rebalance monthly  # v3 walk-forward, net of costs (free lane = INDICATIVE ONLY)
python -m equity_research.cli pull-index-history       # survivorship-free PIT S&P 500 membership (fja05680, MIT)
python -m equity_research.cli pull-simfin              # delisted-inclusive prices (SimFin free, >=2019; needs SIMFIN_API_KEY)
python -m equity_research.cli backtest --lane simfin --strategy hrp --start 2021-07-01  # best-effort survivorship-free (use a dedicated EQR_DATA_DIR)
python -m equity_research.cli backtest --strategy hrp --start 2012-01-01 --locked-test-start 2022-01-01  # v3 Phase C: in-sample vs locked out-of-sample tail
python -m equity_research.cli backtest-compare --strategies hrp risk_parity equal_weight --start 2012-01-01 --locked-test-start 2022-01-01  # v3 Phase C: Deflated Sharpe race (multiple-testing correct); selects in-sample only
```

**Convenience scripts (PowerShell):** `scripts\tour.ps1` runs one example of EVERY capability
(v1 -> v2 -> v3 Phase A -> Phase B) with section banners (read-only, no pulls/commits) — the quickest
way to see the whole app's output. `scripts\checkpoint.ps1 "msg"` runs the test suite then commits +
pushes (only if green).

## Architecture (src/equity_research/)
- `config.py` — all tunable assumptions in one auditable place (universe floors, SEC UA, FRED key,
  `FMP_API_KEY` / `SIMFIN_API_KEY` (both from .env), LLM model, `PortfolioConfig`: covariance/caps/Kelly,
  `ExposureConfig`: regression freq/lookback/factors, `BacktestConfig`: cost bps/impact/tax/cadence/
  initial capital/locked-test boundary).
- `disclaimer.py` — persistent disclaimer + `SecurityReport` dataclass (fields default to "Not
  estimated") + `BacktestResult` (net-of-cost stats, gross alongside, lane + validity stamp, **Phase C:
  PSR, regime stress table, in-sample/out-of-sample split**) + **`BacktestCompare`** (DSR race table,
  in-sample-only selection).
- `data/storage.py` — DuckDB + versioned raw parquet snapshots; `upsert_by_ticker` (persistent universe).
- `data/http.py` — GET with retry/backoff (env network is intermittently flaky).
- `data/edgar.py` — SEC EDGAR `companyfacts` → `fundamentals` table, as-reported, filing-date keyed.
  Handles flow-duration (10-Q quarterly vs YTD) and tag migration (multi-candidate, priority-ranked).
- `data/prices_free.py` — Yahoo chart API → `prices` table (survivorship-biased; documented).
  Fetches full **daily** history via explicit `period1/period2` epoch window (the `range=max`
  keyword silently downsamples to quarterly — never used), so backtests span real regimes.
- `data/reference.py` — current S&P 500 constituents (seed only; not point-in-time membership).
- `data/index_membership.py` — **v3 Phase B** survivorship-free PIT S&P 500 membership windows from
  `fja05680/sp500` (MIT) -> `index_membership` table (ticker, start_date, end_date); ~500 names/date
  since 1996, multiple stints. Public facts only; optional `parse_symbol`/`delist_reasons` read
  acquired/bankrupt codes from a Norgate-style export.
- `data/simfin.py` — **v3 Phase B** SimFin v3 price adapter (free tier, ~2019+). Delisted-inclusive
  daily prices (stable entity ids -> no ticker recycling) into the `prices` schema; closes the
  delisted-price gap Yahoo can't. Pure `_parse_prices` is tested; live fetch retries + handles 429.
- `data/fred.py` — FRED/ALFRED macro adapter (Lane 1). Pulls **vintages** (value as known at each
  date) into the `macro` table, so macro is point-in-time correct (no revision lookahead). Needs FRED_API_KEY.
- `data/sectors.py` — sector classification from SEC SIC (submissions endpoint) -> `securities` table.
  Coarse ~11-bucket map; **current SIC, not point-in-time** (true PIT GICS is a Sharadar upgrade).
- `data/sharadar.py` — **deferred Lane-2 adapter**; same schema, switch on when a key is added.
- `data/pit.py` — point-in-time access: `fundamentals_asof`, `prices_asof`, `annual_history`,
  `last_price`, `macro_asof` (latest vintage in force on `as_of`), `macro_series_asof` (full
  vintage-correct macro time series for regressions), `sectors_for` (ticker -> sector).
- `exposures.py` — macro rate/curve/credit **exposure betas** (univariate OLS of periodic returns
  on factor changes, PIT) + **recession base rate** by NBER (USREC) regime. Measured sensitivities
  and base rates, never forecasts; thin/absent samples read "Not estimated".
- `universe.py` — `backtest_universe` (full, delisted-incl.) vs `screening_universe` (liquid floor;
  returns empty if prices/fundamentals tables are absent) vs `survivorship_universe` (PIT index
  membership from `index_membership`, delisted names included). market cap = shares × last price.
- `factors.py` — value/quality/profitability/momentum/low-vol as within-universe percentile ranks.
- `valuation.py` — reverse-DCF *implied* growth (range over discount rates) + relative multiples vs peers.
- `flags.py` — Piotroski F, Altman Z, Beneish M (scores + components + missing-list).
- `report.py` — assembles the full `SecurityReport` from the above.
- `portfolio.py` — **v2** risk-based weighting (inverse-variance / equal-risk-contribution /
  HRP, all pure-numpy), position + liquidity caps, and bounded fractional-Kelly (pure math;
  requires an explicit expected-return vector -- the tool never fabricates `mu`). PIT covariance is
  **survivorship-robust**: each name's variance uses its OWN full history and off-diagonals are
  PAIRWISE (a delisted name no longer truncates everyone to a common window), then projected to the
  nearest PSD matrix; thin/absent overlaps carry 0 covariance (flagged, never fabricated). Free-lane
  (full-overlap) results are unchanged by this; only the ragged/simfin lane improves.
- `costs.py` — **v3** pure transaction-cost model: half-spread + commission + sqrt-law market
  impact in ADV participation; unknown/thin ADV charged at the participation cap (conservative,
  never skipped). Hand-testable.
- `metrics.py` — **v3** pure performance metrics over a NET return series: CAGR, ann vol, Sharpe
  (+ analytic SE so it's reported as a RANGE), Sortino, max drawdown, hit rate. **Phase C:**
  Probabilistic + **Deflated Sharpe Ratio** (Bailey & Lopez de Prado: PSR corrects for sample
  length + skew/kurtosis, `expected_max_sharpe` deflates for multiple testing), return skew/kurtosis,
  a self-contained normal CDF/inverse-CDF (no scipy), and **regime tagging** (`regime_of` /
  `regime_breakdown`: dot-com / GFC / COVID / 2022 / normal).
- `backtest.py` — **v3 Phase A** walk-forward engine. Reuses pit/universe/portfolio/factors so
  every rebalance is point-in-time; holds positions as units of `closeadj` (returns compound
  automatically) **plus an explicit cash line** (undeployed capital from tight caps / a thin
  universe is HELD, never evaporates -- a leak here once faked a -72% drawdown); charges costs +
  tax each rebalance; tracks net-of-cost NAV, per-period returns, turnover. Delisting-aware (stale
  holdings liquidated at last price, never silently survived). Reports the strategy against an
  **equal-weight 1/N benchmark** (same universe/costs) with excess CAGR. Strategies: hrp /
  risk_parity / inverse_variance / composite / equal_weight. **Lanes:** `free` (Yahoo/screen ->
  INDICATIVE ONLY) and **`simfin` (v3 Phase B): PIT membership + delisted prices -> best-effort
  survivorship-free** (price-only strategies; composite refused; a delisted name's crash is felt).
  **Phase C:** every run reports its PSR + per-regime stress table + (if `locked_test_start` set)
  in-sample vs out-of-sample split; **`compare_backtests`** races N strategies, Deflated-Sharpes each
  (`n_trials=N`), and selects on the IN-SAMPLE window only (locked tail scored once).
- `_cache.py` — run-scoped memoization (`cache_get`) for the backtest hot path: a plain dict created
  in `run_backtest` / `compare_backtests` and threaded (default None = off) through `screening_universe`
  / `factor_profile` / `estimate_covariance` / `sectors_for`. Memoizes only PIT-deterministic views by
  date (+ ticker-set for covariance), so it is numerically lossless and discarded per run (no global
  staleness). `compare_backtests` shares one cache across strategies + skips the per-strategy benchmark
  (`with_benchmark=False`) -> compare 90s->35s on the 57-name set. NEXT perf step (full S&P 500) is
  algorithmic: HRP is O(n^3), so cap the construction universe / precompute in the UI layer.
- `llm.py` — bounded bull/bear narrative (optional; grounded only in computed figures).
- `cli.py` — `pull` / `pull-sp500` / `universe` / `screen` / `report` / `construct` / `pull-macro` /
  `macro` / `exposures` / `pull-sectors` / `backtest` / `backtest-compare` / `pull-index-history` /
  `pull-simfin`.

## Status & data lane
- **Phase 0 + v1 + v2 + v3 (Phase A, B & C) complete.** Data lanes are all **free**: EDGAR
  fundamentals + Yahoo prices + FRED/ALFRED macro (v1/v2), and for v3 survivorship-free backtests,
  **fja05680 PIT S&P membership (MIT) + SimFin free delisted prices (>=2019)**. Sharadar was
  evaluated and declined (cost); its stub adapter remains but is unused. **Phase C (Deflated Sharpe +
  backtest-compare, multi-regime stress, locked-test enforcement) is pure analytics over the existing
  engine -- no new data.**
- See `docs/phase0_data_inventory.md` (data honesty report) and `docs/v3_backtesting.md` (v3 plan +
  the free-DIY Phase B decision).

## Not built yet (roadmap)
- **v2 portfolio construction is COMPLETE** — inverse-variance / ERC / HRP weighting; position,
  **sector**, and liquidity caps (iterated to a joint fixpoint so all hold at once); Kelly wired to a
  CLI edge input (`--edge`/`--edge-file`, `--kelly-fraction`, `--max-weight`, `--max-sector`). Sector
  caps use SEC SIC (current, coarse, not point-in-time -- flagged; true PIT GICS is a Sharadar upgrade).
- **Macro:** FRED/ALFRED adapter (vintage-aware `macro` table + `macro_asof`/`macro_series_asof`) **and
  the rate/recession exposure analytics are built** (`exposures.py`, `exposures` CLI): per-factor OLS
  betas to the 10Y / 10Y-2Y / HY spread + an NBER-regime base rate. Live-validated (FRED key set). Note:
  daily rate series are pulled plain (not revised); only CPI/UNRATE/USREC pull ALFRED vintages.
- **v3 backtesting — Phase A COMPLETE (free lane) + Phase B COMPLETE via a FREE DIY lane.**
  Phase A: walk-forward engine (`backtest.py`) + cost model (`costs.py`) + net-of-cost metrics
  (`metrics.py`) + equal-weight benchmark; free Yahoo lane is INDICATIVE ONLY (survivorship-biased).
  **Phase B (best-effort survivorship-free, $0):** `--lane simfin` uses point-in-time S&P 500
  membership (`fja05680`, MIT) + delisted-inclusive prices (SimFin free, >=2019). A held name that
  delists now actually hurts the backtest. **Sharadar was NOT used** (too costly for a personal
  project; SimFin free + fja05680 cover it). Honest limits: SimFin free history ~2019+ (no
  GFC/dot-com); composite/factor strategies unsupported on this lane (no PIT fundamentals for
  delisted names).
  **Phase C COMPLETE (out-of-sample discipline + stress, free analytics):** Probabilistic + Deflated
  Sharpe Ratio (`metrics.py`) + `backtest-compare` (races N strategies, `n_trials=N`, selects
  in-sample only), the multi-regime stress table (dot-com / GFC / COVID / 2022 / normal; unreached
  regimes read "Not estimated"), and locked-test-set enforcement (`--locked-test-start`: in-sample vs
  out-of-sample tail, selection never peeks at the lock). All hand-tested. See `docs/v3_backtesting.md` §8.

## Working norms
- Tests for every new analytical piece; synthetic fixtures with hand-computed expected values.
- Windows console is cp1252 — keep CLI/print output ASCII (no em dashes).
- Commit only when Darrell asks. End commit messages with the Co-Authored-By trailer.
