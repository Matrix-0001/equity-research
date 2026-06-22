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
- Run tests: `.venv\Scripts\python -m pytest -q`  (currently **124/124 green**).
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
```

## Architecture (src/equity_research/)
- `config.py` — all tunable assumptions in one auditable place (universe floors, SEC UA, FRED key,
  LLM model, `PortfolioConfig`: covariance/caps/Kelly, `ExposureConfig`: regression freq/lookback/factors,
  `BacktestConfig`: cost bps/impact/tax/cadence/initial capital/locked-test boundary).
- `disclaimer.py` — persistent disclaimer + `SecurityReport` dataclass (fields default to "Not
  estimated") + `BacktestResult` (net-of-cost stats, gross alongside, lane + validity stamp).
- `data/storage.py` — DuckDB + versioned raw parquet snapshots; `upsert_by_ticker` (persistent universe).
- `data/http.py` — GET with retry/backoff (env network is intermittently flaky).
- `data/edgar.py` — SEC EDGAR `companyfacts` → `fundamentals` table, as-reported, filing-date keyed.
  Handles flow-duration (10-Q quarterly vs YTD) and tag migration (multi-candidate, priority-ranked).
- `data/prices_free.py` — Yahoo chart API → `prices` table (survivorship-biased; documented).
  Fetches full **daily** history via explicit `period1/period2` epoch window (the `range=max`
  keyword silently downsamples to quarterly — never used), so backtests span real regimes.
- `data/reference.py` — current S&P 500 constituents (seed only; not point-in-time membership).
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
- `universe.py` — `backtest_universe` (full, delisted-incl.) vs `screening_universe` (liquid floor);
  market cap = shares × last price (vendor-agnostic).
- `factors.py` — value/quality/profitability/momentum/low-vol as within-universe percentile ranks.
- `valuation.py` — reverse-DCF *implied* growth (range over discount rates) + relative multiples vs peers.
- `flags.py` — Piotroski F, Altman Z, Beneish M (scores + components + missing-list).
- `report.py` — assembles the full `SecurityReport` from the above.
- `portfolio.py` — **v2** risk-based weighting (inverse-variance / equal-risk-contribution /
  HRP, all pure-numpy), position + liquidity caps, and bounded fractional-Kelly (pure math;
  requires an explicit expected-return vector -- the tool never fabricates `mu`). PIT covariance.
- `costs.py` — **v3** pure transaction-cost model: half-spread + commission + sqrt-law market
  impact in ADV participation; unknown/thin ADV charged at the participation cap (conservative,
  never skipped). Hand-testable.
- `metrics.py` — **v3** pure performance metrics over a NET return series: CAGR, ann vol, Sharpe
  (+ analytic SE so it's reported as a RANGE), Sortino, max drawdown, hit rate. (Deflated Sharpe
  = Phase C.)
- `backtest.py` — **v3 Phase A** walk-forward engine. Reuses pit/universe/portfolio/factors so
  every rebalance is point-in-time; holds positions as units of `closeadj` (returns compound
  automatically) **plus an explicit cash line** (undeployed capital from tight caps / a thin
  universe is HELD, never evaporates -- a leak here once faked a -72% drawdown); charges costs +
  tax each rebalance; tracks net-of-cost NAV, per-period returns, turnover. Delisting-aware (stale
  holdings liquidated at last price, never silently survived). Reports the strategy against an
  **equal-weight 1/N benchmark** (same universe/costs) with excess CAGR. Strategies: hrp /
  risk_parity / inverse_variance / composite / equal_weight. Free lane => every run "indicative only".
- `llm.py` — bounded bull/bear narrative (optional; grounded only in computed figures).
- `cli.py` — `pull` / `pull-sp500` / `universe` / `screen` / `report` / `construct` / `pull-macro` /
  `macro` / `exposures` / `pull-sectors` / `backtest`.

## Status & data lane
- **Phase 0 complete; v1 complete.** Data lane = **Lane 1 (free): EDGAR + Yahoo + (FRED planned)**.
  Sharadar (Lane 2: point-in-time fundamentals + delisted prices + PIT index membership) is the
  planned paid upgrade — the adapter exists; storage/PIT/universe/factor layers don't change.
- See `docs/phase0_data_inventory.md` for the data honesty report and decision log.

## Not built yet (roadmap)
- **v2 portfolio construction is COMPLETE** — inverse-variance / ERC / HRP weighting; position,
  **sector**, and liquidity caps (iterated to a joint fixpoint so all hold at once); Kelly wired to a
  CLI edge input (`--edge`/`--edge-file`, `--kelly-fraction`, `--max-weight`, `--max-sector`). Sector
  caps use SEC SIC (current, coarse, not point-in-time -- flagged; true PIT GICS is a Sharadar upgrade).
- **Macro:** FRED/ALFRED adapter (vintage-aware `macro` table + `macro_asof`/`macro_series_asof`) **and
  the rate/recession exposure analytics are built** (`exposures.py`, `exposures` CLI): per-factor OLS
  betas to the 10Y / 10Y-2Y / HY spread + an NBER-regime base rate. Live-validated (FRED key set). Note:
  daily rate series are pulled plain (not revised); only CPI/UNRATE/USREC pull ALFRED vintages.
- **v3 validated backtesting — Phase A COMPLETE (free lane, indicative-only).** Walk-forward engine
  (`backtest.py`) + cost model (`costs.py`) + net-of-cost metrics (`metrics.py`) + `backtest` CLI.
  Point-in-time and survivorship-free *at the engine level*; every run stamped "indicative only"
  because free Yahoo prices are survivorship-biased and EDGAR has no PIT index membership. Sharpe
  reported as a +/- 1 SE range. **Phase B (Sharadar, survivorship-free) and Phase C (locked test
  set + Deflated Sharpe + multi-regime stress) are NOT built** — see `docs/v3_backtesting.md` §5/§7.
  Phase B is gated on authorizing Sharadar spend (Darrell, not yet given).

## Working norms
- Tests for every new analytical piece; synthetic fixtures with hand-computed expected values.
- Windows console is cp1252 — keep CLI/print output ASCII (no em dashes).
- Commit only when Darrell asks. End commit messages with the Co-Authored-By trailer.
