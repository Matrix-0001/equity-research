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
**Phase 0 complete — Lane 1 (free) chosen to start; Sharadar is the planned upgrade.**
Python 3.12 + `.venv` installed. EDGAR + Yahoo adapters **built and live-validated** against
AAPL/MSFT; **8/8 tests pass** (point-in-time, survivorship-free universe, and the EDGAR
flow-duration logic). Stack: Python + DuckDB/Parquet.

**Data sources (free, no subscription):**
- **Fundamentals:** SEC EDGAR `companyfacts` — as-reported, keyed on filing date → point-in-time.
  Curated us-gaap/dei concepts; quarterly vs annual durations disambiguated. (`data/edgar.py`)
- **Prices:** Yahoo Finance chart API for current names. (`data/prices_free.py`)
  *Survivorship-biased — backtests stay "indicative only" until a delisted-price source
  (EODHD ~$20/mo or Sharadar) is added.*
- **Macro:** FRED/ALFRED (vintage / as-released) — adapter not built yet.

Lane 2 (Sharadar) ingestion lives in `data/sharadar.py`, ready to switch on once a key is added —
the storage / point-in-time / universe layers don't change.

### Quickstart
```
.venv\Scripts\python -m pytest -q                              # 8/8
set EQR_DATA_DIR=%CD%\data
.venv\Scripts\python scripts\validate_lane1.py                 # live EDGAR + Yahoo pull
```
