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
from dataclasses import replace

import pandas as pd

from equity_research import exposures, factors, portfolio, report, universe
from equity_research.config import config
from equity_research.data import edgar, fred, pit, prices_free, reference, sectors, storage
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
    try:
        s = sectors.ingest_sectors(up)
        print(f"Sectors (SEC SIC):        {s['rows']} rows | "
              f"resolved={len(s['tickers_resolved'])} missing_cik={len(s['tickers_missing_cik'])}")
    except Exception as exc:  # sector classification is best-effort; never fail a pull over it
        print(f"Sectors (SEC SIC):        skipped ({exc})")

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


def _pull_sectors(tickers: list[str]) -> None:
    conn = storage.connect()
    try:
        if tickers:
            names = [t.upper() for t in tickers]
        elif storage.table_exists(conn, "fundamentals"):
            names = sorted(r[0] for r in
                           conn.execute("SELECT DISTINCT ticker FROM fundamentals").fetchall())
        else:
            names = []
    finally:
        conn.close()
    if not names:
        print("No tickers to classify. Pull some names first, or pass tickers explicitly.")
        return
    print(f"Fetching SIC sector classification for {len(names)} ticker(s) from SEC...\n")
    s = sectors.ingest_sectors(names)
    print(f"Classified {s['rows']} | resolved={len(s['tickers_resolved'])} "
          f"missing_cik={len(s['tickers_missing_cik'])}\n")
    conn = storage.connect()
    try:
        smap = pit.sectors_for(conn, names)
    finally:
        conn.close()
    counts: dict[str, int] = {}
    for sec in smap.values():
        counts[sec] = counts.get(sec, 0) + 1
    print("Sector breakdown of loaded names (current SIC, coarse, not point-in-time):")
    for sec, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {sec:<24} {c}")
    print(f"\n{PERSISTENT_DISCLAIMER}")


def _exposures(ticker: str, asof: dt.date) -> None:
    conn = storage.connect()
    try:
        rep = exposures.macro_exposures(conn, asof, ticker)
    finally:
        conn.close()
    print(rep.render())


def _pull_macro(series: list[str] | None, asof: dt.date) -> None:
    print("Pulling macro series from FRED/ALFRED (vintage-aware)...\n")
    try:
        s = fred.ingest(series or None)
    except RuntimeError as exc:
        print(f"Cannot pull macro data: {exc}")
        return
    print(f"Macro: {s['rows']} rows | resolved={s['series_resolved']} "
          f"no_data={s['series_no_data']}\n")
    _macro(asof)


def _macro(asof: dt.date) -> None:
    conn = storage.connect()
    try:
        if not storage.table_exists(conn, "macro"):
            print("No macro data loaded. Run `pull-macro` first (needs a free FRED_API_KEY).")
            return
        df = pit.macro_asof(conn, asof)
    finally:
        conn.close()
    print(f"Macro values known as of {asof} (ALFRED vintage in force - no lookahead):")
    if df.empty:
        print("  (none known as of this date)")
    else:
        for _, r in df.iterrows():
            label = fred.DEFAULT_SERIES.get(r["series_id"], "")
            obs = pd.Timestamp(r["date"]).date()
            vint = pd.Timestamp(r["vintage"]).date()
            print(f"  {r['series_id']:<14} {r['value']:>12.3f}  (obs {obs}, vintage {vint})"
                  f"  {label}")
    print("\nData courtesy of FRED/ALFRED, Federal Reserve Bank of St. Louis.")
    print(f"{PERSISTENT_DISCLAIMER}")


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
          f"no_data={len(p['tickers_no_data'])}")
    try:
        s = sectors.ingest_sectors(syms)
        print(f"Sectors:      {s['rows']} rows | resolved={len(s['tickers_resolved'])} "
              f"missing_cik={len(s['tickers_missing_cik'])}\n")
    except Exception as exc:  # best-effort; never fail the seed over sectors
        print(f"Sectors:      skipped ({exc})\n")
    _universe(asof)


def _report(ticker: str, asof: dt.date) -> None:
    conn = storage.connect()
    try:
        rep = report.build_security_report(conn, asof, ticker, use_llm=True)
    finally:
        conn.close()
    print(rep.render())


def _parse_edges(pairs: list[str] | None, path: str | None) -> dict[str, float]:
    """Build a ticker -> expected-excess-return dict from --edge pairs and/or a CSV file.

    These are USER-SUPPLIED assumptions for Kelly sizing; the tool never invents them.
    """
    edges: dict[str, float] = {}
    if path:
        import csv
        with open(path, newline="") as fh:
            for row in csv.reader(fh):
                if not row or len(row) < 2:
                    continue
                if row[0].strip().lower() in ("ticker", "symbol"):
                    continue
                edges[row[0].strip().upper()] = float(row[1])
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--edge expects TICKER=VALUE (e.g. AAPL=0.06), got {pair!r}")
        t, v = pair.split("=", 1)
        edges[t.strip().upper()] = float(v)
    return edges


def _construct(tickers: list[str], asof: dt.date, method: str, top: int, value: float,
               edges: list[str] | None, edge_file: str | None,
               kelly_fraction: float | None, max_weight: float | None,
               max_sector: float | None) -> None:
    over: dict = {}
    if top and top > 0:
        over["default_top_n"] = top
    if max_weight is not None:
        over["max_position_weight"] = max_weight
    if max_sector is not None:
        over["max_sector_weight"] = max_sector
    cfg = replace(config, portfolio=replace(config.portfolio, **over)) if over else config
    mu = _parse_edges(edges, edge_file) or None
    conn = storage.connect()
    try:
        res = portfolio.construct_portfolio(
            conn, asof, tickers=tickers or None, method=method, cfg=cfg,
            portfolio_value=value, mu=mu, kelly_fraction=kelly_fraction,
        )
    finally:
        conn.close()
    print(res.render())


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

    cons = sub.add_parser(
        "construct",
        help="Build risk-based portfolio weights (inverse_variance / risk_parity / hrp) + caps",
    )
    cons.add_argument("tickers", nargs="*",
                      help="names to weight; omit to use the top-N screen by composite")
    cons.add_argument("--method", default=config.portfolio.default_method,
                      choices=[*portfolio.WEIGHT_METHODS, "kelly"],
                      help="weighting scheme; 'kelly' requires --edge / --edge-file")
    cons.add_argument("--edge", action="append", metavar="TICKER=EXCESS_RETURN",
                      help="USER-SUPPLIED expected excess return for Kelly (e.g. --edge "
                           "AAPL=0.06); repeatable. The tool never invents these.")
    cons.add_argument("--edge-file",
                      help="CSV of ticker,expected_excess_return for Kelly (header optional)")
    cons.add_argument("--kelly-fraction", type=float, default=None,
                      help=f"Kelly fraction override (default {config.portfolio.kelly_fraction})")
    cons.add_argument("--max-weight", type=float, default=None,
                      help="per-position cap override (default "
                           f"{config.portfolio.max_position_weight})")
    cons.add_argument("--max-sector", type=float, default=None,
                      help="per-sector cap override (default "
                           f"{config.portfolio.max_sector_weight}); needs pull-sectors")
    cons.add_argument("--top", type=int, default=config.portfolio.default_top_n,
                      help="how many names by factor composite when none are given")
    cons.add_argument("--value", type=float,
                      default=config.portfolio.default_portfolio_value_usd,
                      help="portfolio dollar value (drives the liquidity cap)")
    cons.add_argument("--asof", default=dt.date.today().isoformat(),
                      help="point-in-time date (YYYY-MM-DD); default today")

    uni = sub.add_parser("universe", help="Show the currently loaded universe")
    uni.add_argument("--asof", default=dt.date.today().isoformat(),
                     help="point-in-time date (YYYY-MM-DD); default today")

    sp = sub.add_parser("pull-sp500", help="Seed/refresh the universe from current S&P 500 list")
    sp.add_argument("--limit", type=int, default=0, help="only the first N tickers (0 = all 500)")
    sp.add_argument("--asof", default=dt.date.today().isoformat(),
                    help="point-in-time date (YYYY-MM-DD); default today")

    pm = sub.add_parser("pull-macro", help="Ingest vintage-aware FRED/ALFRED macro series")
    pm.add_argument("series", nargs="*", help="FRED series IDs; omit for the curated default set")
    pm.add_argument("--asof", default=dt.date.today().isoformat(),
                    help="point-in-time date (YYYY-MM-DD); default today")

    ps = sub.add_parser("pull-sectors",
                        help="Classify tickers into sectors from SEC SIC (enables the sector cap)")
    ps.add_argument("tickers", nargs="*",
                    help="tickers to classify; omit to backfill all loaded names")

    mac = sub.add_parser("macro", help="Show macro values known as of a date (ALFRED vintage)")
    mac.add_argument("--asof", default=dt.date.today().isoformat(),
                     help="point-in-time date (YYYY-MM-DD); default today")

    exp = sub.add_parser("exposures", help="Rate/recession exposure of a ticker (PIT regressions)")
    exp.add_argument("ticker", help="e.g. AAPL")
    exp.add_argument("--asof", default=dt.date.today().isoformat(),
                     help="point-in-time date (YYYY-MM-DD); default today")

    args = ap.parse_args(argv)
    if args.cmd == "pull":
        _pull(args.tickers, dt.date.fromisoformat(args.asof))
    elif args.cmd == "screen":
        _screen(dt.date.fromisoformat(args.asof), args.top)
    elif args.cmd == "report":
        _report(args.ticker, dt.date.fromisoformat(args.asof))
    elif args.cmd == "construct":
        _construct(args.tickers, dt.date.fromisoformat(args.asof),
                   args.method, args.top, args.value,
                   args.edge, args.edge_file, args.kelly_fraction, args.max_weight,
                   args.max_sector)
    elif args.cmd == "universe":
        _universe(dt.date.fromisoformat(args.asof))
    elif args.cmd == "pull-sp500":
        _pull_sp500(dt.date.fromisoformat(args.asof), args.limit)
    elif args.cmd == "pull-macro":
        _pull_macro(args.series, dt.date.fromisoformat(args.asof))
    elif args.cmd == "pull-sectors":
        _pull_sectors(args.tickers)
    elif args.cmd == "macro":
        _macro(dt.date.fromisoformat(args.asof))
    elif args.cmd == "exposures":
        _exposures(args.ticker, dt.date.fromisoformat(args.asof))


if __name__ == "__main__":
    main()
