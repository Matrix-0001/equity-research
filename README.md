# Local Equity Research & Portfolio Analysis Platform

A local, fully-auditable research platform that screens, values, risk-assesses, and helps
construct diversified long-horizon (5–20 year) portfolios with **point-in-time-correct,
reproducible logic**. It is a **decision-support tool — not an oracle, and not financial advice.**

> **PERSISTENT DISCLAIMER**
> This is a research tool, not investment advice. It does not make recommendations.
> The user is solely responsible for all decisions. Past patterns do not predict future
> returns. Every quantitative output is traceable to a specific computation; when a value
> cannot be reliably computed, it reads **"Not estimated."**

## Governing values
Epistemic honesty · reproducibility · capital preservation through humility.
A small, validated, well-understood system beats a large one that merely looks sophisticated.
Markets are assumed mostly efficient; durable edges are rare, narrow, and decay over time.

## Non-negotiable constraints
1. **No fabricated numbers.** Every figure traces to a computation over specific inputs. The
   LLM explains numbers; it never invents them. Missing → "Not estimated."
2. **Point-in-time data or nothing.** As-reported, restatement-aware, delisted-inclusive.
   Tests lacking PIT data are stamped *"indicative only — survivorship/lookahead risk present."*
3. **Uncertainty as ranges + confidence tiers** (Low/Medium/High), never false-precision point
   probabilities.
4. **Costs always included** — transaction costs, spread, market impact for the position size,
   and a configurable tax assumption. Pre-cost results are never shown alone.
5. **Out-of-sample discipline is structural** — locked test set, walk-forward only,
   multiple-testing correction (deflated Sharpe) whenever many strategies/params are compared.

## Build order (validate each phase before the next)
- **Phase 0 — Data honesty (current).** [Data Inventory & Honesty Report](docs/phase0_data_inventory.md).
  *No modeling code until a data lane is chosen and its limits accepted in writing.*
- **v1** — Screening + transparent valuation (reverse-DCF & relative, as ranges) + accounting-quality
  flags (Piotroski F, Altman Z, Beneish M — as flags, not predictions).
- **v2** — Portfolio construction (diversification, risk parity / HRP, fractional Kelly with a hard
  cap; position/sector/liquidity limits).
- **v3** — Validated backtesting + multi-regime stress tests (dot-com, GFC, COVID, 2022), reported
  net of costs and with multiple-testing correction. **Phases A, B, and C are complete on free data:**
  walk-forward engine + cost model (A), best-effort survivorship-free lane (B), and out-of-sample
  discipline — Deflated Sharpe + `backtest-compare`, multi-regime stress, locked test set (C).
  See [v3 build spec](docs/v3_backtesting.md).
- **v4 (gated)** — sentiment/NLP & alt-data, only if proven licensable *and* additive out-of-sample.

## What this system will NOT claim to do
Forecast recessions · time markets · reliably find "multi-baggers" · output precise probabilities
of doubling or permanent loss. It *may* estimate **exposures** and **historical base rates**
(clearly labeled "base rate, not a forecast").

## The LLM's bounded role
Only: (a) translate computed metrics into plain-language theses / bull–bear cases / risk narratives;
(b) check internal consistency and flag where numbers and narrative disagree; (c) surface caveats
and missing data. It never produces a numeric estimate of its own and never overrides a computed value.

## Current status
**Phase 0 + v1 + v2 + v3 (Phase A, B & C) complete — all on FREE data.** v1: factor screen + valuation +
accounting-quality flags + full SecurityReport (optional LLM bull/bear) + persistent universe. v2:
portfolio construction (risk parity / HRP, fractional Kelly, position + sector + liquidity caps);
FRED/ALFRED macro + rate/recession exposures. v3 Phase A: walk-forward backtest engine +
transaction-cost model + net-of-cost metrics + equal-weight benchmark (free Yahoo lane = "indicative
only"). **v3 Phase B: best-effort survivorship-free backtests for $0** — point-in-time S&P 500
membership (fja05680, MIT) + delisted-inclusive prices (SimFin free, ~2019+); *Sharadar declined on
cost.* **v3 Phase C: out-of-sample discipline** — Probabilistic + Deflated Sharpe Ratio and a
`backtest-compare` race (multiple-testing-correct, selects in-sample only), a multi-regime stress
table (dot-com / GFC / COVID / 2022 / normal; unreached regimes read "Not estimated"), and locked-test
enforcement (in-sample vs out-of-sample tail). Python 3.12 + `.venv`
installed; **171/171 tests pass** (point-in-time, survivorship-free universe, EDGAR
flow-duration logic, factor math, flags, valuation, upsert persistence, report assembly, LLM parsing,
v2 portfolio weighting / position+sector+liquidity caps / Kelly, vintage-aware FRED/ALFRED macro,
macro exposure regressions, SIC->sector mapping, v3 cost model / metrics / walk-forward engine
incl. no-lookahead, cost accounting, NAV compounding, delisting, cash conservation, benchmark, the
SimFin price parser, PIT index membership, a survivorship-lane test that a delisted crash is felt,
and Phase C's Deflated Sharpe / PSR / normal-CDF / regime tagging / locked-test split / compare race,
and survivorship-robust pairwise+PSD covariance that a delisted name can't truncate).
Stack: Python + DuckDB/Parquet.

**Data sources (free, no subscription):**
- **Fundamentals:** SEC EDGAR `companyfacts` — as-reported, keyed on filing date → point-in-time.
  Curated us-gaap/dei concepts; quarterly vs annual durations disambiguated; tag migrations
  handled (e.g. NVDA revenue). (`data/edgar.py`)
- **Prices (current names):** Yahoo Finance chart API, full **daily** history (`data/prices_free.py`).
  *Survivorship-biased* — fine for the v1 screen and the "indicative" Phase A backtests.
- **Prices (delisted-inclusive, ~2019+):** SimFin free tier (`data/simfin.py`) — stable entity ids,
  keeps delisted history; powers the survivorship-free `--lane simfin` backtests. Needs `SIMFIN_API_KEY`.
- **Index membership (point-in-time):** `fja05680/sp500` (MIT) via `data/index_membership.py` —
  survivorship-free S&P 500 universe since 1996.
- **Macro:** FRED/ALFRED (vintage / as-released) — adapter not built yet.

Lane 2 (Sharadar) ingestion lives in `data/sharadar.py`, ready to switch on once a key is added —
the storage / point-in-time / universe / factor layers don't change.

### Quickstart
```
.venv\Scripts\python -m pytest -q                                     # 171/171
.venv\Scripts\python -m equity_research.cli pull-universe --index sp1500  # load the full S&P 1500 (large+mid+small; resumable)
.venv\Scripts\python -m equity_research.cli pull-sp500 --limit 50     # seed the universe (accumulates)
.venv\Scripts\python -m equity_research.cli universe                  # what's loaded
.venv\Scripts\python -m equity_research.cli screen                    # factor ranks, whole universe
.venv\Scripts\python -m equity_research.cli report AAPL               # full SecurityReport
.venv\Scripts\python -m equity_research.cli construct --method hrp    # risk-based weights + caps (v2)
.venv\Scripts\python -m equity_research.cli backtest --strategy hrp --start 2012-01-01 --locked-test-start 2022-01-01   # walk-forward, net of costs, in-sample vs locked tail (v3 Phase A+C; INDICATIVE ONLY)
.venv\Scripts\python -m equity_research.cli backtest-compare --strategies hrp risk_parity equal_weight --start 2012-01-01 --locked-test-start 2022-01-01  # Deflated-Sharpe race, in-sample selection (v3 Phase C)
```
(Run from the repo root so data lands in `.\data`, which is git-ignored. `pull TICKERS...` and
`pull-sp500` both ACCUMULATE — re-pulling refreshes a name, others stay.)

### What works today (v1)
- **Persistent universe:** `pull` / `pull-sp500` upsert by ticker (build up the S&P 500 without
  re-pulling); `universe` shows what's loaded.
- **Multi-factor screen:** value / quality / profitability / momentum / low-vol as **percentile
  ranks within the loaded universe** (0-100, higher = more attractive), plus a composite. PIT correct.
- **Valuation (ranges, never a point estimate):** reverse-DCF *implied* FCF growth across discount
  rates, and P/E / P/B / P/S vs the universe median + IQR.
- **Accounting-quality flags:** Piotroski F (0-9), Altman Z (distress zones), Beneish M (manipulation
  screen) — with thresholds shown; flags, not predictions.
- **`report TICKER`** renders the full honest `SecurityReport` (data completeness, confidence tier,
  valuation read, factor profile, quality flags, machine-quantified risks, what-would-change) plus an
  **optional bounded LLM bull/bear narrative** (`pip install -e .[llm]` + `ANTHROPIC_API_KEY`; defaults
  to `claude-opus-4-8`, override `EQR_LLM_MODEL`). The LLM only narrates the computed figures — it
  never invents a number; without a key the narrative reads "Not estimated."
- Throughout: missing inputs read **"Not estimated"** with the reason, never a fabricated number
  (e.g. a bank's Altman Z and reverse-DCF correctly refuse rather than mislead).

### What works today (v2, complete)
- **`construct [TICKERS...] --method {inverse_variance,risk_parity,hrp}`** turns a basket (explicit
  names, or the top-N by factor composite) into diversified weights from a point-in-time covariance
  matrix, then applies hard **position, sector, and liquidity caps** -- iterated to a joint fixpoint so
  all three hold at once (`--max-weight` / `--max-sector` override). Names with too little history or
  zero return variance are **dropped and listed**, never imputed. Output shows weights, realized risk
  contributions, sector exposure, binding caps, and any un-deployable capital.
- **Sectors:** `pull-sectors` classifies names from SEC SIC codes into a coarse ~11-bucket sector map
  (`securities` table) so the sector cap can bind. Honestly stamped **current SIC, coarse, not
  point-in-time** (true PIT GICS membership is a Sharadar/Lane-2 upgrade).
- **Fractional-Kelly** is wired to an explicit, user-supplied edge input:
  `construct --method kelly --edge AAPL=0.06 --edge MSFT=0.04` (or `--edge-file`, plus
  `--kelly-fraction` / `--max-weight`). Expected EXCESS returns are **your** inputs, echoed back as
  assumptions and never invented; the result also reports the implied gross Kelly leverage.
- **Macro (FRED/ALFRED):** `pull-macro` ingests a curated set of rate / inflation / recession series
  **vintage-aware** (revised series carry ALFRED vintages; non-revised daily rates are stamped as known
  on their observation date), and `macro` shows the values **known as of a date** -- point-in-time
  correct. Needs a free `FRED_API_KEY`.
- **Macro exposures:** `exposures TICKER` regresses the name's periodic returns on changes in the 10Y
  yield, the 10Y-2Y curve, and the high-yield credit spread (univariate OLS, point-in-time) -- betas
  with standard errors / t / R^2 / n -- plus a **recession base rate** by NBER (USREC) regime. Measured
  historical sensitivities and base rates, **never forecasts**; thin/absent samples read "Not estimated".

### What works today (v3 Phase A, complete -- INDICATIVE ONLY)
- **`backtest --strategy {hrp,risk_parity,inverse_variance,composite,equal_weight} --start
  YYYY-MM-DD [--end --rebalance monthly|quarterly --value --tax]`** runs a walk-forward backtest
  that reuses the v1 screen + v2 construction. Each rebalance sees only `date <= as_of`
  (point-in-time); positions are held as units of the split/dividend-adjusted price so returns
  compound automatically (with an explicit cash line, so capital the caps can't deploy is held, not
  lost); **every trade is charged** (half-spread + commission + sqrt-law market impact in ADV) plus
  a configurable tax drag; the result reports a NET-of-cost NAV with the GROSS path alongside, a
  Sharpe **range** (+/- 1 SE), Sortino, max drawdown, hit rate, turnover, the annualized cost drag,
  and the **excess CAGR vs an equal-weight 1/N benchmark** on the same universe (an absolute return
  is meaningless without one).
- Delisting-aware at the engine level: a held name that stops trading is marked at its last price
  then liquidated, never silently survived. Free prices are now fetched as deep **daily** history
  (so a run can span GFC/COVID/2022). **But on the free lane every run is stamped "indicative
  only"** -- Yahoo prices are survivorship-biased and EDGAR has no point-in-time index membership.

### What works today (v3 Phase B -- best-effort survivorship-free, FREE)
A survivorship-free backtest needs two things free Yahoo lacks: a point-in-time universe (which
names were in the index over time, incl. delisted) and delisted prices. Both are now sourced for
**$0**:
- **`pull-index-history`** loads point-in-time S&P 500 membership from
  [`fja05680/sp500`](https://github.com/fja05680/sp500) (MIT, Wikipedia-derived) -> `index_membership`
  windows (~500 names/date since 1996). `universe.survivorship_universe(as_of)` gives exact PIT
  membership, delisted names included.
- **`pull-simfin`** pulls delisted-inclusive daily prices from **SimFin's free tier** (~2019+). SimFin
  keys data by stable entity id, so a delisted name keeps its real history (e.g. SVB runs to a $0.01
  final close) instead of Yahoo's recycled-ticker collisions. Needs a free `SIMFIN_API_KEY`.
- **`backtest --lane simfin`** then runs survivorship-free: basket = the PIT membership (priced names),
  weighted by hrp / risk_parity / inverse_variance / equal_weight. A held name that delists now
  **actually hurts the portfolio**, as it must. Stamped *"best-effort survivorship-free"* (honest
  limits: SimFin free starts ~2019, so no GFC/dot-com; composite/factor strategies need PIT
  fundamentals for delisted names and are unsupported on this lane). Use a dedicated `EQR_DATA_DIR`
  so SimFin and Yahoo prices don't mix.

*Sharadar was evaluated and declined* -- too costly for a personal project; the free fja05680 + SimFin
combination covers the survivorship requirement for recent years.

### What works today (v3 Phase C, complete -- out-of-sample discipline + stress)
Phase C is pure analytics over the existing engine (no new data), enforcing non-negotiable #5:
- **Probabilistic & Deflated Sharpe Ratio** (`metrics.py`, Bailey & Lopez de Prado). The PSR corrects
  a single-path Sharpe for sample length + return skew/kurtosis; the DSR additionally **deflates for
  multiple testing** -- it discounts a Sharpe that is merely the best of N tries. Every backtest now
  reports `Prob(SR>0)`; the math is self-contained (a normal CDF via `erf` + an Acklam inverse-CDF, no
  scipy) and unit-tested against the published formula.
- **`backtest-compare --strategies ... [--locked-test-start D]`** races N strategies and reports each
  one's DSR with `n_trials = N`, so we don't crown the luckiest of five. **Selection uses only the
  in-sample window**; the locked out-of-sample tail is scored once, for the winner, and never used to
  pick it.
- **Multi-regime stress table** on every run: net return / vol / max-drawdown bucketed by market
  regime (dot-com 2000-2002, GFC 2007-2009, COVID 2020, 2022 drawdown, else normal). A run only speaks
  to regimes its window + lane actually reach -- the rest read **"Not estimated"** (so a `simfin` run
  honestly shows dot-com/GFC as "Not estimated").
- **Locked test set** (`--locked-test-start`): every run splits its realized path into in-sample and
  the locked out-of-sample tail and reports each separately, so a single hero number can't span both
  selection and evaluation.

### Not built yet
- Smaller polish: portfolio-level macro exposures, multi-factor joint regression, true PIT GICS sectors.
