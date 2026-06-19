"""Minimal command-line interface for the data layer.

This is NOT the full research app yet — factor ranks, valuation, and quality
flags (v1) are not built, so those fields read "Not estimated". What works today:
pull point-in-time fundamentals + prices for tickers you choose, and see market cap
and whether they pass the liquidity screen.

Usage (run from the repo root so ./data is used):
    .venv\\Scripts\\python -m equity_research.cli pull AAPL MSFT NVDA
    .venv\\Scripts\\python -m equity_research.cli pull AAPL --asof 2020-06-30

Note: `pull` now ACCUMULATES — re-pulling a ticker refreshes it; other tickers stay.
Use `pull-sp500` to seed the whole index and `universe` to see what's loaded.
"""

from __future__ import annotations

import argparse
import datetime as dt

from equity_research import factors, report, universe
from equity_research.config import config
from equity_research.data import edgar, pit, prices_free, reference, storage
from equity_research.disclaimer import PERSISTENT_DISCLAIMER


def _pull(tickers: list[str], asof: dt.date) -> None:
    up = [t.upper() for t in tickers]
    print(f"Data dir: {config.data_dir}")
    print(f"Pulling {len(up)} ticker(s): {', '.join(up)}\n")

    f = edgar.ingest(up)
    print(f"Fundamentals (SEC EDGAR): {f['rows']} rows | "
          f"resolved={f['tickers_resolved']} missing_cik={f['tickers_missing_cik']} "
          f"no_facts={f['tickers_no_facts']}")
    p = prices_free.ingest(up)
    print(f"Prices (Yahoo):           {p['rows']} rows | "
          f"resolved={p['tickers_resolved']} no_data={p['tickers_no_data']}")

    conn = storage.connect()
    try:
        cols = ["ticker", "datekey", "reportperiod", "form", "revenue",
                "net_income", "total_equity", "shares_outstanding"]
        fund = pit.fundamentals_asof(conn, asof, up)
        print(f"\nLatest as-reported fundamentals known as of {asof}:")
        print("  (none)" if fund.empty
              else fund[[c for c in cols if c in fund.columns]].to_string(index=False))

        scr = universe.screening_universe(conn, asof)
        mine = scr[scr["ticker"].isin(up)] if not scr.empty else scr
        print("\nLiquidity/size screen - which of your tickers qualify:")
        print("  (none qualify)" if mine.empty else mine.to_string(index=False))
        failed = sorted(set(up) - set(mine["ticker"]) if not mine.empty else set(up))
        if failed:
            print(f"  not qualifying / insufficient data: {failed}")
    finally:
        conn.close()

    print("\nNot built yet (v1): factor ranks, reverse-DCF valuation, "
          "Piotroski/Altman/Beneish flags - these read 'Not estimated'.")
    print(f"\n{PERSISTENT_DISCLAIMER}")


def _screen(asof: dt.date, top: int) -> None:
    conn = storage.connect()
    try:
        prof = factors.factor_profile(conn, asof)
    finally:
        conn.close()
    if prof.empty:
        print("No data in the universe. Run `pull TICKERS...` first to load some names.")
        return

    disp = prof.copy()
    disp["mcap_$B"] = (disp["market_cap"] / 1e9).round(1)
    rank_cols = ["value", "quality", "profitability", "momentum", "low_vol", "composite"]
    for c in rank_cols:
        disp[c] = disp[c].round(0)
    cols = ["mcap_$B", *rank_cols, "n_metrics"]

    print(f"Factor percentile ranks within universe (0-100, higher = more attractive) "
          f"| as of {asof}")
    print(f"Universe: {len(disp)} names\n")
    print(disp.head(top)[cols].to_string())
    print("\nRanks are cross-sectional within THIS universe - not predictions, not buy "
          "signals.\nGroups with missing inputs are averaged over available data only "
          "(see n_metrics; 7 = all factors present).")
    print(f"\n{PERSISTENT_DISCLAIMER}")


def _universe(asof: dt.date) -> None:
    conn = storage.connect()
    try:
        nf = (conn.execute("SELECT count(DISTINCT ticker) FROM fundamentals").fetchone()[0]
              if storage.table_exists(conn, "fundamentals") else 0)
        npr = (conn.execute("SELECT count(DISTINCT ticker) FROM prices").fetchone()[0]
               if storage.table_exists(conn, "prices") else 0)
        scr = universe.screening_universe(conn, asof) if (nf and npr) else None
    finally:
        conn.close()
    print(f"Loaded universe (in {config.data_dir}):")
    print(f"  fundamentals: {nf} tickers")
    print(f"  prices:       {npr} tickers")
    n_pass = 0 if scr is None or scr.empty else len(scr)
    print(f"  pass liquidity/size screen as of {asof}: {n_pass}")


def _pull_sp500(asof: dt.date, limit: int) -> None:
    syms = reference.sp500_constituents()
    if limit and limit > 0:
        syms = syms[:limit]
    print(f"Seeding {len(syms)} S&P 500 ticker(s); this can take a few minutes...\n")
    f = edgar.ingest(syms)
    print(f"Fundamentals: {f['rows']} rows | resolved={len(f['tickers_resolved'])} "
          f"missing_cik={len(f['tickers_missing_cik'])} no_facts={len(f['tickers_no_facts'])}")
    p = prices_free.ingest(syms)
    print(f"Prices:       {p['rows']} rows | resolved={len(p['tickers_resolved'])} "
          f"no_data={len(p['tickers_no_data'])}\n")
    _universe(asof)


def _report(ticker: str, asof: dt.date) -> None:
    conn = storage.connect()
    try:
        rep = report.build_security_report(conn, asof, ticker, use_llm=True)
    finally:
        conn.close()
    print(rep.render())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="equity_research.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pull = sub.add_parser("pull", help="Ingest + show data for the given tickers")
    pull.add_argument("tickers", nargs="+", help="e.g. AAPL MSFT NVDA")
    pull.add_argument("--asof", default=dt.date.today().isoformat(),
                      help="point-in-time date (YYYY-MM-DD); default today")

    screen = sub.add_parser("screen", help="Factor percentile ranks for the loaded universe")
    screen.add_argument("--asof", default=dt.date.today().isoformat(),
                        help="point-in-time date (YYYY-MM-DD); default today")
    screen.add_argument("--top", type=int, default=50, help="show top N by composite")

    report = sub.add_parser("report", help="Per-ticker factor ranks + valuation + flags")
    report.add_argument("ticker", help="e.g. AAPL")
    report.add_argument("--asof", default=dt.date.today().isoformat(),
                        help="point-in-time date (YYYY-MM-DD); default today")

    uni = sub.add_parser("universe", help="Show the currently loaded universe")
    uni.add_argument("--asof", default=dt.date.today().isoformat(),
                     help="point-in-time date (YYYY-MM-DD); default today")

    sp = sub.add_parser("pull-sp500", help="Seed/refresh the universe from current S&P 500 list")
    sp.add_argument("--limit", type=int, default=0, help="only the first N tickers (0 = all 500)")
    sp.add_argument("--asof", default=dt.date.today().isoformat(),
                    help="point-in-time date (YYYY-MM-DD); default today")

    args = ap.parse_args(argv)
    if args.cmd == "pull":
        _pull(args.tickers, dt.date.fromisoformat(args.asof))
    elif args.cmd == "screen":
        _screen(dt.date.fromisoformat(args.asof), args.top)
    elif args.cmd == "report":
        _report(args.ticker, dt.date.fromisoformat(args.asof))
    elif args.cmd == "universe":
        _universe(dt.date.fromisoformat(args.asof))
    elif args.cmd == "pull-sp500":
        _pull_sp500(dt.date.fromisoformat(args.asof), args.limit)


if __name__ == "__main__":
    main()
