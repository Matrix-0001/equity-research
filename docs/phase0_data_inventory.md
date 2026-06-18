# Phase 0 — Data Inventory & Honesty Report

**Status:** Draft v0.1 · **Date:** 2026-06-18 · **Owner:** (you)
**Rule this document serves:** *Confront the data problem before writing any model.*

> This report is a **decision input**, not a model. Its job is to state honestly what
> data is obtainable locally, what it costs, what its license permits, how point-in-time
> (PIT) correct it is, how far back it goes, and what it is biased by — so that the choice
> of data foundation is made with eyes open. No screening, valuation, or backtesting code
> should be written until a data lane below is chosen and its limits are accepted in writing.

---

## 0. How to read this

- **PIT integrity** is graded **High / Medium / Low** and explained. It is the single most
  important property, because non-negotiable constraint #2 ("point-in-time data or nothing")
  is the hardest one to satisfy cheaply and locally.
- **License (store?)** asks only what matters for a *personal local tool*: may I download
  and store the data locally for my own research? Redistribution rights are **not required**
  for this project, which widens the options considerably.
- **Verified** means I confirmed the figure against the vendor/source in June 2026.
  **Unverified** means the figure is gated (login wall) or stale-risk and **must be confirmed
  at signup** — consistent with rule #1, I will not present an invented number as fact.

---

## 1. The core data problem (state it plainly)

The spec's non-negotiable #2 demands, for any historical claim:

1. **As-reported fundamentals** (what was actually filed at the time, not later restatements),
2. **with point-in-time stamps** (so the backtest only "knows" what was knowable on that date),
3. **including delisted securities** (so the universe isn't survivorship-biased),
4. plus **corporate-action-adjusted prices** that also include delisted names.

This collides with "local" and "cheap." The combination of *as-reported + PIT + delisted* is
exactly the expensive part. Naive free sources fail it silently:

- **Survivorship bias:** most free price feeds drop companies once they delist, so any
  backtest run on them over-counts winners and is invalid for the spec's purposes.
- **Restatement / lookahead bias:** most free fundamentals show the *latest restated* numbers,
  not what was on the filing on the date you're testing. Using them lets the model "see the
  future," inflating every result.

**Consequence for honesty:** if we cannot satisfy #2 for a given test, the result must be
stamped **"indicative only — survivorship/lookahead risk present"** and may never be presented
as a valid backtest. The data lane we pick determines how often that stamp must appear.

---

## 2. Data categories required for v1

| # | Category | Needed for | Hard requirement |
|---|----------|-----------|------------------|
| A | EOD prices (split/dividend-adjusted), **incl. delisted** | momentum, low-vol, liquidity, returns, all backtests | survivorship-bias-free |
| B | As-reported fundamentals with **PIT filing dates**, restatement-aware | value, quality, profitability, Piotroski/Altman/Beneish, reverse-DCF | PIT integrity High |
| C | Corporate actions (splits, dividends, mergers, delistings) | accurate total return, delisting outcomes | complete delisting reasons ideal |
| D | Macro series (rates, inflation, spreads), **vintage/as-released** | rate & recession exposure, regime tagging | ALFRED-style vintages preferred |
| E | Sector / industry classification | sector limits, peer relative valuation | stable, mappable |
| F | Index constituents, **historical (PIT)** | universe definition, survivorship-free screening | point-in-time membership |

Categories **A + B** are the make-or-break. **D** is cheaply solved (FRED/ALFRED). **F** is the
quiet survivorship trap most retail projects miss.

---

## 3. Source inventory

### 3.1 Free sources

#### SEC EDGAR (fundamentals — category B)
- **Obtainable locally:** Yes — full bulk financial-statement datasets + per-filing XBRL,
  downloadable and storable. APIs (`data.sec.gov`) are free; fair-access rate limits apply.
- **Cost:** $0. **License (store?):** Yes — US government public data, storable for personal use.
- **PIT integrity:** **High *in principle*** — these are the actual filings, each with a real
  filing/acceptance date, and companies that later delisted still have their historical filings.
  You reconstruct PIT yourself by keying on filing date. This is the one genuinely free path to
  *as-reported* fundamentals.
- **History:** XBRL is reliable from ~2009; earlier filings exist as text but are far messier.
- **Biases / costs:** US-only; **heavy normalization burden** (XBRL tags are inconsistent across
  filers and years); restatement handling is on you; no prices. This is real engineering work,
  not a plug-in feed.

#### FRED / ALFRED (macro — category D)
- **Obtainable locally:** Yes, free API. **Cost:** $0. **License (store?):** Yes (with attribution;
  some series carry third-party terms — check per series).
- **PIT integrity:** **High** via **ALFRED**, which serves *vintage* data (the value as first
  released on a given date) — exactly what's needed to avoid macro lookahead.
- **History:** Decades for core series. **Biases:** revisions exist (handled by using vintages);
  coverage is US-centric (pair with other agencies for non-US later).

#### Free EOD price feeds (yfinance / Stooq / Alpha Vantage free — category A)
- **Obtainable locally:** Yes. **Cost:** $0.
- **License (store?):** **Problematic.** `yfinance` scrapes Yahoo; Yahoo's ToS restrict
  redistribution and the endpoint is unofficial/unstable. Personal local use is a grey area;
  do not build anything durable or shareable on it.
- **PIT integrity:** **Low.** **Survivorship-biased** (delisted names largely absent), adjustment
  methodology opaque, history revised silently.
- **Verdict:** acceptable for prototyping UI/plumbing, **not acceptable for any spec-valid
  backtest** (category A requirement fails). Anything built on it is "indicative only."

### 3.2 Paid sources (modest tier)

#### Sharadar — SF1 (fundamentals) + SEP (prices) — categories A, B, C, F
- **Obtainable locally:** Yes — bulk download via Nasdaq Data Link API (and Sharadar direct);
  storable locally. **Single vendor covering both A and B is a major friction win.**
- **Cost:** **Unverified — pricing is login-gated** at data.nasdaq.com / sharadar.com and could
  not be confirmed in June 2026. It has long been positioned as the most affordable PIT-fundamentals
  source for individuals (modest monthly tier). **Action: confirm exact SF1 / SEP / bundle prices
  and the non-professional license at signup before committing.** *(Per rule #1, no specific price
  is asserted here until verified.)*
- **License (store?):** Non-professional/personal tier permits local storage and use; confirm the
  exact terms (it is per-seat, internal-use — fine for this project).
- **PIT integrity:** **High** — explicitly point-in-time and **restatement-aware** (you can include
  *or* exclude restatements), with **delisted companies included** (near survivorship-bias-free).
  This is the cleanest cheap-ish way to satisfy constraint #2 for fundamentals.
- **History:** ~25–28 years fundamentals (SF1, ~16k companies); SEP prices ~21k+ active+delisted
  tickers back to ~1998. Also offers historical index constituents (category F) and corporate
  actions (C).
- **Biases:** US equities only; ~25yr history means **the dot-com top (2000) is only partially
  covered for fundamentals** — note this for v3 regime testing.

#### Norgate Data — US Platinum/Diamond — categories A, C, F (prices)
- **Obtainable locally:** Yes — local database + daily updater; integrates with Python
  (`norgatedata`). **Cost (verified, review-sourced — confirm on site):** US **Platinum ≈ $630/yr**
  (or ≈ $346.50 / 6 mo); **Gold ≈ $360/yr** (current names only, ~20yr, **no delisted**);
  **Diamond** extends price history to 1950. **Delisted data requires Platinum or above.**
- **License (store?):** Yes — personal subscription, local storage is the product.
- **PIT integrity (prices):** **High** — survivorship-bias-free, **25,000+ delisted securities**,
  and crucially **historical (PIT) index constituents** (category F) — excellent for defining a
  clean, survivorship-free backtest universe.
- **PIT integrity (fundamentals):** **Low/limited** — Norgate's fundamentals are essentially
  *current*, not as-reported PIT history. **Norgate is a prices/universe powerhouse, not a
  fundamentals source.** Pair it with Sharadar SF1 or EDGAR for category B.
- **History:** prices to 1990 (Platinum) / 1950 (Diamond). **Biases:** US/AU/CA only.

#### EODHD (eodhd.com) — categories A, B (breadth) — *verified pricing*
- **Cost (verified, personal-use plans):** Free ($0, ~20 calls/day); **EOD All-World $19.99/mo
  ($199/yr)**; EOD+Intraday $29.99/mo; **Fundamentals Data Feed $59.99/mo ($599.90/yr)**;
  **All-In-One $99.99/mo ($999.90/yr)**. Commercial use starts at $399/mo (not needed here).
- **License (store?):** Personal tier permits local use; redistribution is the commercial tier
  (not required for us).
- **PIT integrity:** **Prices Medium–High** (global, adjusted, delisted coverage exists).
  **Fundamentals: Medium–Low for PIT** — broad global coverage and history, but fundamentals are
  generally latest/normalized rather than strictly as-reported with filing-date stamps and
  restatement toggles. **Verify whether PIT/as-reported is available before relying on it for
  constraint #2.**
- **History / breadth:** Global (huge exchange coverage) — its differentiator. **Biases:** PIT
  fundamentals rigor is the open question; global data quality varies by exchange.

#### Tiingo — categories A, (B add-on) — *verified pricing*
- **Cost (verified):** **Starter $0**; **Power $30/mo** (much higher API limits). **Fundamentals
  are a separate add-on** (contact sales; individual & commercial tiers).
- **License (store?):** Data feeds are **"Internal Use Only"** — fine for a personal local tool,
  but explicitly no sharing/redistribution.
- **PIT integrity:** **Prices Medium–High** (corporate-action-adjusted "composite" EOD, broad
  coverage). **Fundamentals via third party; PIT rigor and delisted coverage need verification.**
- **History:** 30+ yr prices; fundamentals ~20yr / ~5,500 equities / 80+ indicators.
  **Biases:** fundamentals depth and PIT correctness are the open questions.

### 3.3 Aspirational — explicitly **NOT in v1**

Flagged here so they are never silently assumed (per spec). Each is a deliberate, costed v4
upgrade *only if* a future Phase-0 addendum proves it's licensable **and** adds out-of-sample
value beyond v1 factors:

- Earnings-call **transcripts** / NLP sentiment — licensing + cost barrier, unproven marginal value.
- **Alt-data** (satellite, card spend, web traffic) — expensive, short history, heavy survivorship
  & vendor-selection bias.
- **Institutional flows / 13F** — available (EDGAR) but lagged 45 days and noisy; PIT-tricky.
- **Patents / supply-chain graphs** — research-grade, not v1.

---

## 4. PIT integrity summary (the table that matters)

| Source | A: Prices+delisted | B: PIT as-reported fundamentals | F: PIT index members | Cost basis |
|--------|:---:|:---:|:---:|---|
| SEC EDGAR | ✗ (no prices) | **High** (DIY normalization) | ✗ | Free |
| FRED/ALFRED | n/a (macro D: **High**) | n/a | n/a | Free |
| Free price feeds | **Low** (survivorship) | ✗ | ✗ | Free |
| **Sharadar SF1+SEP** | **High** | **High** | **High** | Modest *(verify)* |
| Norgate Platinum+ | **High** | Low (current only) | **High** | ≈$630/yr |
| EODHD | Med–High | Med–Low *(verify PIT)* | partial | $20–100/mo |
| Tiingo | Med–High | add-on *(verify PIT)* | partial | $0–30/mo + add-on |

---

## 5. Recommended v1 foundations, by budget lane

### Lane 1 — Free ($0): "EDGAR + FRED + free prices"
- **B & D:** EDGAR (as-reported fundamentals, you build PIT) + ALFRED (vintage macro). Genuinely
  spec-compliant on fundamentals if the normalization is done carefully.
- **A:** the weak link — free price feeds are survivorship-biased. **Delisted price history is the
  unsolved gap.**
- **Honest verdict:** v1 *screening and valuation on current names* is achievable and honest.
  **Backtests (v3) will mostly carry the "indicative only" stamp** because the price universe is
  survivorship-biased. High engineering effort (XBRL normalization).

### Lane 2 — Modest (**recommended**): "Sharadar SF1 + SEP" (single vendor)
- Covers **A, B, C, F all at High PIT integrity from one vendor**, restatement-aware, delisted
  included. Lowest friction path that *actually satisfies* non-negotiable #2.
- **Honest verdict:** v1–v3 can be built **without the "indicative only" stamp** for US equities
  back to ~1998 (fundamentals ~2000). The dot-com peak is only partially covered — note for stress
  tests. **This is the best value-to-honesty ratio.** *(Confirm exact price + license at signup.)*
- **Alt within this lane:** Sharadar SF1 (fundamentals) **+ Norgate Platinum** (prices + PIT index
  constituents, ≈$630/yr) if you want Norgate's deeper price history (1990/1950) and constituent
  data — at the cost of two vendors.

### Lane 3 — Broader ($100+/mo): "EODHD All-In-One" or multi-vendor
- Buys **global breadth**. But for the spec's strict PIT-fundamentals requirement, breadth ≠
  integrity: **verify EODHD's as-reported/PIT fundamentals before assuming Lane 3 beats Lane 2.**
- **Honest verdict:** justified only if international coverage is a v1 goal. Otherwise Lane 2 is
  more honest per dollar. Flag the global pieces as a deliberate, costed upgrade — not a default.

---

## 6. Honesty verdict vs. the non-negotiables

| Constraint | Lane 1 (Free) | Lane 2 (Sharadar) | Lane 3 (Broad) |
|---|---|---|---|
| #2 PIT fundamentals | ✔ (DIY, effortful) | ✔ (native) | ⚠ verify |
| #2 Delisted prices | ✗ (gap) | ✔ | ✔ |
| #2 PIT index universe | ✗ | ✔ | partial |
| Effort to first valid backtest | High | Low | Medium |
| US dot-com (2000) coverage | partial | partial | partial |
| GFC / COVID / 2022 regimes | ✔ prices only | ✔ | ✔ |

**Bottom line:** Lane 2 (Sharadar SF1+SEP) is the only modest-cost option that satisfies
constraint #2 natively across prices, fundamentals, and universe. Lane 1 is viable for screening
and honest valuation today but leaves backtesting survivorship-limited until a delisted price
source is added. Lane 3 buys breadth, not integrity.

---

## 7. Open items to verify before committing (do not skip)

1. **Sharadar exact pricing + non-professional license terms** (login-gated) — confirm SF1, SEP,
   and bundle monthly cost and that personal local storage is permitted.
2. **EODHD / Tiingo fundamentals PIT rigor** — confirm whether as-reported, filing-date-stamped,
   restatement-aware history is actually available, and delisted fundamental coverage.
3. **Norgate prices** — confirm current Platinum/Diamond pricing on their calculator (figures here
   are review-sourced) and exact delisting-reason coverage.
4. **Corporate-action completeness** for the chosen price source (esp. delisting reasons:
   acquired vs. bankrupt — this materially affects delisting outcome base rates in v1).
5. **Data revision policy** — whether your chosen vendor ever silently restates history (breaks
   reproducibility); if so, snapshot raw pulls and version them.

---

## 8. Decision required from you

To unlock v1 development, confirm:

- [x] **Budget lane:** **Lane 1 (free) for now** — SEC EDGAR fundamentals + FRED macro + free EOD
  prices. **Lane 2 (Sharadar) is the planned upgrade** once backtesting (v3) justifies the spend.
  *(revised 2026-06-18)*
- [x] **Universe** (v1 default, 2026-06-18): **liquid US common stocks** above a size/liquidity
  floor — market cap ≥ $300M, 20-day avg dollar volume ≥ $1M, price ≥ $1. Config-driven, easily
  changed. Backtest universe is always full delisted-inclusive coverage.
- [x] **Acceptance of stated limits** for Lane 1 (see Decision Log). *Accepted 2026-06-18.*
- [x] **Authorization to spend:** **$0 (Lane 1).** Sharadar spend deferred to a later phase.

Until the universe is set and Lane 2 limits accepted **in writing here**, no screening, valuation,
or backtesting code will be written. That is the Phase 0 gate working as intended.

### Decision log

**2026-06-18 — Revised to Lane 1 (free) to start; Lane 2 deferred.**
Sharadar prices alone are $49/mo (SEP), ~$100/mo with SF1 — too much to commit before the project
has proven useful. Start on the free lane and switch to Sharadar later; the ingestion layer is
adapter-based, so the swap is a drop-in (storage / point-in-time / universe layers unchanged).
Lane 1 sources: **SEC EDGAR** (as-reported, point-in-time fundamentals, keyed on filing date),
**FRED/ALFRED** (vintage macro), and a **free EOD price source** (Stooq / Tiingo-free) for current names.

*Lane 1 limits accepted:*
1. **Free prices are survivorship-biased** (delisted names dropped). So v3 backtests are stamped
   "indicative only — survivorship risk present" until a delisted price source (EODHD ~$20/mo or
   Sharadar) is added. v1 screening/valuation and v2 construction on current names are honest.
2. **EDGAR needs normalization** (XBRL tags vary); v1 targets a curated set of us-gaap concepts for
   the liquid screening universe, not the whole market.
3. **US-only.**

**2026-06-18 — Lane 2 selected (Sharadar SF1 + SEP).** *(superseded same day by the entry above;
retained as the intended upgrade target.)*
Rationale: only modest-cost option that satisfies non-negotiable #2 natively — restatement-aware
as-reported fundamentals, delisted-inclusive prices, and historical (PIT) index constituents from
a single vendor.

**Limits accepted as part of this choice (to be confirmed by user):**
1. **History depth:** fundamentals ~2000 / prices ~1998. The **dot-com top (March 2000) is only
   partially covered for fundamentals** — v3 stress tests over that regime will be labeled with a
   coverage caveat. GFC (2008), COVID (2020), and 2022 are fully covered.
2. **Scope:** **US equities only** in v1. International is a deliberate, costed future upgrade, not
   a default.
3. **Cost not yet verified:** Sharadar pricing is login-gated; exact SF1/SEP/bundle price and the
   non-professional (personal, internal-use) license terms must be confirmed at signup before
   spending (Open Item §7.1).
4. **Reproducibility:** raw Sharadar pulls will be snapshotted and version-stamped locally, because
   the vendor may revise history; backtests pin to a snapshot date.

---

*Sources consulted (June 2026): EODHD pricing page; Norgate Data pricing/packages (review-sourced
figures, to be confirmed on vendor calculator); Tiingo pricing page; Sharadar / Nasdaq Data Link
SF1 & SEP product pages (pricing login-gated); SEC EDGAR & FRED/ALFRED public documentation.*

**DISCLAIMER:** This is a research-planning document, not investment advice. It makes no claim
about future returns of any security or strategy.
