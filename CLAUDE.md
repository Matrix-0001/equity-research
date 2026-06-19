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
- Run tests: `.venv\Scripts\python -m pytest -q`  (currently **31/31 green**).
- Optional LLM: `pip install -e .[llm]` + `ANTHROPIC_API_KEY` (default model `claude-opus-4-8`,
  override `EQR_LLM_MODEL`). Without it, narratives read "Not estimated".

## CLI (run from repo root so data lands in ./data)
```
python -m equity_research.cli pull-sp500 --limit 50   # seed universe (ACCUMULATES)
python -m equity_research.cli pull AAPL MSFT NVDA      # add/refresh specific names
python -m equity_research.cli universe                 # what's loaded
python -m equity_research.cli screen                   # factor percentile ranks, whole universe
python -m equity_research.cli report AAPL              # full SecurityReport (+ optional LLM bull/bear)
```

## Architecture (src/equity_research/)
- `config.py` — all tunable assumptions in one auditable place (universe floors, SEC UA, LLM model).
- `disclaimer.py` — persistent disclaimer + `SecurityReport` dataclass (fields default to "Not estimated").
- `data/storage.py` — DuckDB + versioned raw parquet snapshots; `upsert_by_ticker` (persistent universe).
- `data/http.py` — GET with retry/backoff (env network is intermittently flaky).
- `data/edgar.py` — SEC EDGAR `companyfacts` → `fundamentals` table, as-reported, filing-date keyed.
  Handles flow-duration (10-Q quarterly vs YTD) and tag migration (multi-candidate, priority-ranked).
- `data/prices_free.py` — Yahoo chart API → `prices` table (survivorship-biased; documented).
- `data/reference.py` — current S&P 500 constituents (seed only; not point-in-time membership).
- `data/sharadar.py` — **deferred Lane-2 adapter**; same schema, switch on when a key is added.
- `data/pit.py` — point-in-time access: `fundamentals_asof`, `prices_asof`, `annual_history`, `last_price`.
- `universe.py` — `backtest_universe` (full, delisted-incl.) vs `screening_universe` (liquid floor);
  market cap = shares × last price (vendor-agnostic).
- `factors.py` — value/quality/profitability/momentum/low-vol as within-universe percentile ranks.
- `valuation.py` — reverse-DCF *implied* growth (range over discount rates) + relative multiples vs peers.
- `flags.py` — Piotroski F, Altman Z, Beneish M (scores + components + missing-list).
- `report.py` — assembles the full `SecurityReport` from the above.
- `llm.py` — bounded bull/bear narrative (optional; grounded only in computed figures).
- `cli.py` — `pull` / `pull-sp500` / `universe` / `screen` / `report`.

## Status & data lane
- **Phase 0 complete; v1 complete.** Data lane = **Lane 1 (free): EDGAR + Yahoo + (FRED planned)**.
  Sharadar (Lane 2: point-in-time fundamentals + delisted prices + PIT index membership) is the
  planned paid upgrade — the adapter exists; storage/PIT/universe/factor layers don't change.
- See `docs/phase0_data_inventory.md` for the data honesty report and decision log.

## Not built yet (roadmap)
- **v2 portfolio construction:** risk parity / HRP, fractional-Kelly with a hard cap, position/sector/
  liquidity limits.
- **v3 validated backtesting:** walk-forward, costs netted out, multi-regime stress, multiple-testing
  correction (deflated Sharpe). Requires point-in-time index membership (Sharadar) for survivorship-free runs.
- FRED/ALFRED macro adapter (rate/recession exposures).

## Working norms
- Tests for every new analytical piece; synthetic fixtures with hand-computed expected values.
- Windows console is cp1252 — keep CLI/print output ASCII (no em dashes).
- Commit only when Darrell asks. End commit messages with the Co-Authored-By trailer.
