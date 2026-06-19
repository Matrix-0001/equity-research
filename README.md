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
  net of costs and with multiple-testing correction.
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
**Phase 0 complete — Lane 1 (free) chosen; Sharadar is the planned upgrade. v1 working:
factor screen + valuation + accounting-quality flags + full SecurityReport (with optional
LLM bull/bear) + persistent universe.** Python 3.12 + `.venv` installed. EDGAR + Yahoo adapters
built and live-validated; **64/64 tests pass** (point-in-time, survivorship-free universe, EDGAR
flow-duration logic, factor math, flags, valuation, upsert persistence, report assembly, LLM parsing,
v2 portfolio weighting / caps / Kelly, vintage-aware FRED/ALFRED macro, and macro exposure regressions).
Stack: Python + DuckDB/Parquet.

**Data sources (free, no subscription):**
- **Fundamentals:** SEC EDGAR `companyfacts` — as-reported, keyed on filing date → point-in-time.
  Curated us-gaap/dei concepts; quarterly vs annual durations disambiguated; tag migrations
  handled (e.g. NVDA revenue). (`data/edgar.py`)
- **Prices:** Yahoo Finance chart API for current names. (`data/prices_free.py`)
  *Survivorship-biased — backtests stay "indicative only" until a delisted-price source
  (EODHD ~$20/mo or Sharadar) is added.*
- **Macro:** FRED/ALFRED (vintage / as-released) — adapter not built yet.

Lane 2 (Sharadar) ingestion lives in `data/sharadar.py`, ready to switch on once a key is added —
the storage / point-in-time / universe / factor layers don't change.

### Quickstart
```
.venv\Scripts\python -m pytest -q                                     # 64/64
.venv\Scripts\python -m equity_research.cli pull-sp500 --limit 50     # seed the universe (accumulates)
.venv\Scripts\python -m equity_research.cli universe                  # what's loaded
.venv\Scripts\python -m equity_research.cli screen                    # factor ranks, whole universe
.venv\Scripts\python -m equity_research.cli report AAPL               # full SecurityReport
.venv\Scripts\python -m equity_research.cli construct --method hrp    # risk-based weights + caps (v2)
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

### What works today (v2, first slice)
- **`construct [TICKERS...] --method {inverse_variance,risk_parity,hrp}`** turns a basket (explicit
  names, or the top-N by factor composite) into diversified weights from a point-in-time covariance
  matrix, then applies a hard **position cap** and a **liquidity cap** (position value vs. ADV).
  Names with too little history or zero return variance are **dropped and listed**, never imputed.
  Output shows weights, realized risk contributions, binding caps, and any un-deployable capital.
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

### Not built yet
- v2 sector caps (need stored point-in-time sector data -- Lane 1 has none); v3 validated backtesting;
  point-in-time index membership (needs Sharadar) for survivorship-free backtests.
