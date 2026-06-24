"""SEC EDGAR fundamentals adapter (Lane 1 — free).

Pulls as-reported fundamentals from the SEC's XBRL `companyfacts` API and loads
them into the vendor-neutral `fundamentals` table, keyed by FILING DATE so the
point-in-time layer works unchanged.

Why this is point-in-time honest:
  - Each XBRL fact carries `filed` (the date the filing became public). We use it
    as `datekey`, so a screen "as of date D" only sees filings filed on/before D.
  - Values are exactly as reported in each filing (companyfacts returns the figure
    each filing stated). A later amendment is a *new* row with a later `filed`
    date, which is correct: it only becomes visible from its own filing date on.

v1 honest limitations (documented, not hidden):
  - US issuers filing XBRL only (no foreign-private-issuer gaps filled, no pre-XBRL
    history — reliable from ~2009).
  - A curated set of us-gaap/dei concepts (below), not every line item.
  - Concept tags vary across filers; we try a small fallback list per concept and
    leave the field NULL ("Not estimated") when no candidate is present, rather
    than guessing.

SEC fair-access: requires a real User-Agent contact (config.sec_user_agent) and
asks callers to stay under ~10 requests/second.
"""

from __future__ import annotations

import datetime as dt
import time

import pandas as pd

from equity_research.config import Config, config
from equity_research.data import http, storage

# logical field -> (XBRL unit, [(taxonomy, tag) fallback candidates])
CONCEPTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    # Income statement
    "revenue": ("USD", [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
    ]),
    "gross_profit": ("USD", [("us-gaap", "GrossProfit")]),
    "operating_income": ("USD", [("us-gaap", "OperatingIncomeLoss")]),
    "net_income": ("USD", [("us-gaap", "NetIncomeLoss")]),
    "interest_expense": ("USD", [
        ("us-gaap", "InterestExpense"),
        ("us-gaap", "InterestExpenseDebt"),
    ]),
    "eps_diluted": ("USD/shares", [("us-gaap", "EarningsPerShareDiluted")]),
    # Balance sheet
    "total_assets": ("USD", [("us-gaap", "Assets")]),
    "current_assets": ("USD", [("us-gaap", "AssetsCurrent")]),
    "total_liabilities": ("USD", [("us-gaap", "Liabilities")]),
    "current_liabilities": ("USD", [("us-gaap", "LiabilitiesCurrent")]),
    "total_equity": ("USD", [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ]),
    "cash": ("USD", [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    ]),
    "long_term_debt": ("USD", [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebt"),
    ]),
    # Cash flow
    "operating_cash_flow": ("USD", [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    ]),
    "capex": ("USD", [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment")]),
    # Extra fields for accounting-quality flags (Altman Z, Beneish M)
    "retained_earnings": ("USD", [("us-gaap", "RetainedEarningsAccumulatedDeficit")]),
    "receivables": ("USD", [
        ("us-gaap", "AccountsReceivableNetCurrent"),
        ("us-gaap", "ReceivablesNetCurrent"),
    ]),
    "ppe_net": ("USD", [("us-gaap", "PropertyPlantAndEquipmentNet")]),
    "depreciation": ("USD", [
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "DepreciationAmortizationAndAccretionNet"),
        ("us-gaap", "Depreciation"),
    ]),
    "sga": ("USD", [
        ("us-gaap", "SellingGeneralAndAdministrativeExpense"),
        ("us-gaap", "GeneralAndAdministrativeExpense"),
    ]),
    # Shares (cover-page count; dei first, then us-gaap fallbacks)
    "shares_outstanding": ("shares", [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
    ]),
}

# Periodic-report forms we keep (annual + quarterly + amendments + foreign).
KEEP_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_REQUEST_DELAY_S = 0.15  # stay well under SEC's ~10 req/s


def _get_json(url: str, cfg: Config) -> dict:
    return http.get_json(url, cfg.sec_user_agent)


def get_cik_map(cfg: Config = config) -> dict[str, int]:
    """Ticker (upper) -> CIK integer, from the SEC's official mapping file."""
    data = _get_json(_TICKER_MAP_URL, cfg)
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


def fetch_company_facts(cik: int, cfg: Config = config) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
    return _get_json(url, cfg)


def _pick_value(
    entries: list[tuple[int, str | None, str, float | None]],
    period_end: str,
    target_days: int,
) -> float | None:
    """Choose the right fact among (priority, start, end, val) tuples for one concept.

    Prefers facts ending on `period_end`; for flow items (which carry a `start`),
    picks the one whose duration is closest to `target_days` (quarter vs year), so a
    10-Q's 6-month YTD figure isn't mistaken for the quarterly number. Ties break to
    the higher-priority candidate tag (lower index), which matters when a filer uses
    several revenue tags or switches tags between eras.
    """
    pool = [t for t in entries if t[2] == period_end]
    if not pool:
        usable = [t for t in entries if t[2] <= period_end]
        if not usable:
            return None
        latest = max(t[2] for t in usable)
        pool = [t for t in usable if t[2] == latest]
    nonnull = [t for t in pool if t[3] is not None]
    if not nonnull:
        return None
    flows = [t for t in nonnull if t[1]]
    if flows:
        def _dur(t: tuple[int, str | None, str, float | None]) -> int:
            return (dt.date.fromisoformat(t[2]) - dt.date.fromisoformat(t[1])).days
        best = min(flows, key=lambda t: (abs(_dur(t) - target_days), t[0]))
        # Refuse to MISLABEL a wrong-duration flow as the period figure: a 10-Q's cash-flow
        # statement, for instance, is reported year-to-date only (no clean 3-month value), so the
        # closest available duration can be a 6-/9-month YTD. If even the nearest flow is off by
        # more than ~40% of the target, return None ("Not estimated") rather than overstate it.
        if abs(_dur(best) - target_days) > 0.4 * target_days:
            return None
        return best[3]
    return min(nonnull, key=lambda t: t[0])[3]  # instant: highest-priority tag


def extract_fundamentals(ticker: str, facts_json: dict) -> pd.DataFrame:
    """Turn one company's companyfacts JSON into wide rows, one per filing (accn)."""
    facts_root = facts_json.get("facts", {})

    # accn -> {filed, form, concepts: {logical: [(end, val), ...]}}
    by_accn: dict[str, dict] = {}
    for logical, (unit, candidates) in CONCEPTS.items():
        # Collect entries from ALL candidate tags (a filer can switch tags over time,
        # e.g. NVDA moved revenue from RevenueFromContract... to Revenues). Each entry
        # keeps its candidate priority so the preferred tag wins per period.
        for prio, (taxonomy, tag) in enumerate(candidates):
            node = facts_root.get(taxonomy, {}).get(tag)
            if not node:
                continue
            entries = node.get("units", {}).get(unit)
            if not entries:
                continue
            for e in entries:
                accn, filed, end = e.get("accn"), e.get("filed"), e.get("end")
                if accn is None or filed is None or end is None:
                    continue
                rec = by_accn.setdefault(
                    accn, {"filed": filed, "form": e.get("form"), "concepts": {}}
                )
                rec["concepts"].setdefault(logical, []).append(
                    (prio, e.get("start"), end, e.get("val"))
                )

    rows: list[dict] = []
    for rec in by_accn.values():
        if rec["form"] not in KEEP_FORMS:
            continue
        concepts = rec["concepts"]
        # Reporting period end = latest end among financial concepts (exclude the
        # cover-page share count, whose "as of" date can sit after period end).
        fin_ends = [
            end for name, lst in concepts.items() if name != "shares_outstanding"
            for _p, _s, end, _v in lst
        ]
        if not fin_ends:
            continue
        period_end = max(fin_ends)
        # Flow items (income statement, cash flow) report multiple durations ending
        # on the same date (e.g. a 10-Q carries both the 3-month and the 6-month YTD
        # figure). Pick the duration matching the filing: ~quarter vs ~year.
        target_days = 90 if rec["form"].startswith("10-Q") else 365

        row: dict = {
            "ticker": ticker,
            "datekey": rec["filed"],
            "reportperiod": period_end,
            "form": rec["form"],
        }
        for name, lst in concepts.items():
            if name == "shares_outstanding":
                nn = [t for t in lst if t[3] is not None]
                row[name] = max(nn, key=lambda x: x[2])[3] if nn else None  # latest end
            else:
                row[name] = _pick_value(lst, period_end, target_days)
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df["datekey"] = pd.to_datetime(df["datekey"])
        df["reportperiod"] = pd.to_datetime(df["reportperiod"])
        df = df.sort_values("datekey").reset_index(drop=True)
    return df


def ingest(
    tickers: list[str],
    cfg: Config = config,
    snapshot_date: dt.date | None = None,
    skip_existing: bool = False,
    batch_size: int = 50,
) -> dict:
    """Fetch + load fundamentals for `tickers` into the `fundamentals` table.

    RESUMABLE for large universes (e.g. the S&P 1500): with `skip_existing`, tickers already in the
    `fundamentals` table are skipped, and progress is persisted every `batch_size` names (per-batch
    parquet snapshot + upsert) so a pull cut short by the flaky network can simply be RE-RUN to
    continue. A single failing fetch is recorded (no_facts) rather than aborting the whole pull.
    Returns a summary dict (rows, resolved, missing_cik, no_facts, skipped) -- surfaced honestly.
    """
    snapshot_date = snapshot_date or dt.date.today()
    cik_map = get_cik_map(cfg)
    conn = storage.connect(cfg)
    try:
        already: set[str] = set()
        if skip_existing and storage.table_exists(conn, "fundamentals"):
            already = {r[0] for r in
                       conn.execute("SELECT DISTINCT ticker FROM fundamentals").fetchall()}
        todo = [t for t in tickers if t.upper() not in already]
        skipped = len(tickers) - len(todo)

        resolved: list[str] = []
        missing_cik: list[str] = []
        no_facts: list[str] = []
        frames: list[pd.DataFrame] = []
        rows = 0
        batch_no = 0

        def _flush() -> None:
            nonlocal frames, rows, batch_no
            if not frames:
                return
            batch = pd.concat(frames, ignore_index=True)
            storage.write_snapshot(
                conn, batch, "fundamentals", source="SEC EDGAR companyfacts", vendor="edgar",
                snapshot_date=snapshot_date, cfg=cfg, suffix=f"_b{batch_no:03d}",
            )
            storage.upsert_by_ticker(conn, batch, "fundamentals")
            rows += len(batch)
            batch_no += 1
            frames = []

        for i, t in enumerate(todo, start=1):
            cik = cik_map.get(t.upper())
            if cik is None:
                missing_cik.append(t.upper())
                continue
            try:
                df = extract_fundamentals(t.upper(), fetch_company_facts(cik, cfg))
            except Exception:
                no_facts.append(t.upper())          # one bad name shouldn't abort a 1500-name pull
                time.sleep(_REQUEST_DELAY_S)
                continue
            if df.empty:
                no_facts.append(t.upper())
            else:
                frames.append(df)
                resolved.append(t.upper())
            time.sleep(_REQUEST_DELAY_S)
            if i % batch_size == 0:
                _flush()
                print(f"  ... edgar {i}/{len(todo)} ({len(resolved)} resolved) - progress saved",
                      flush=True)
        _flush()
        return {"rows": rows, "tickers_resolved": resolved, "tickers_missing_cik": missing_cik,
                "tickers_no_facts": no_facts, "skipped": skipped}
    finally:
        conn.close()
