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

from equity_research import backtest, exposures, factors, portfolio, report, universe
from equity_research.config import config
from equity_research.data import (
    edgar, fred, index_membership, pit, prices_free, reference, sectors, simfin, storage,
)
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


def _floor_cfg(min_cap: float | None, min_adv: float | None, min_price: float | None):
    """Build a config with relaxed universe-floor overrides (None keeps the configured default)."""
    over: dict = {}
    if min_cap is not None:
        over["min_market_cap_usd"] = min_cap
    if min_adv is not None:
        over["min_avg_dollar_volume_usd"] = min_adv
    if min_price is not None:
        over["min_price_usd"] = min_price
    if not over:
        return config
    return replace(config, universe_floor=replace(config.universe_floor, **over))


def _screen(asof: dt.date, top: int, min_cap: float | None = None,
            min_adv: float | None = None, min_price: float | None = None,
            min_complete: float | None = None) -> None:
    cfg = _floor_cfg(min_cap, min_adv, min_price)
    fl = cfg.universe_floor
    conn = storage.connect()
    try:
        prof = factors.factor_profile(conn, asof, cfg)
    finally:
        conn.close()
    print(f"Universe floor: mktcap >= ${fl.min_market_cap_usd:,.0f}, 20d ADV >= "
          f"${fl.min_avg_dollar_volume_usd:,.0f}, price >= ${fl.min_price_usd:.2f}"
          + ("  (relaxed -- reaches smaller names; data is thinner/noisier down here)"
             if cfg is not config else ""))
    if prof.empty:
        print("No data in the universe. Run `pull TICKERS...` or `pull-universe` first.")
        return

    n_total = len(prof)
    if min_complete is not None:
        prof = prof[prof["completeness"] >= min_complete]

    disp = prof.copy()
    disp["mcap_$B"] = (disp["market_cap"] / 1e9).round(1)
    disp["complete%"] = (disp["completeness"] * 100).round(0)
    rank_cols = ["value", "quality", "profitability", "momentum", "low_vol", "composite"]
    for c in rank_cols:
        disp[c] = disp[c].round(0)
    cols = ["mcap_$B", *rank_cols, "complete%", "n_metrics"]

    print(f"Factor percentile ranks within universe (0-100, higher = more attractive) "
          f"| as of {asof}")
    filt = (f" | shown: {len(prof)} with completeness >= {min_complete:.0%}"
            if min_complete is not None else "")
    print(f"Universe: {n_total} names{filt}\n")
    print(disp.head(top)[cols].to_string())
    print("\nRanks are cross-sectional within THIS universe - not predictions, not buy signals.")
    print("'composite' is shrunk toward neutral (50) by the fraction of UNMEASURED factor groups, so "
          "a name\ncan't rank top on a partial profile (complete% = factor groups measured; missing "
          "inputs stay 'Not estimated', never a fabricated 0). Filter with --min-complete.")
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


def _pull_universe(index: str, skip: bool, asof: dt.date) -> None:
    index = index.lower()
    print(f"Resolving constituents for {index} (current membership; not point-in-time)...")
    try:
        if index == "sp1500":
            comp = reference.sp1500_constituents()
            for k in ("sp500", "sp400", "sp600"):
                print(f"  {k}: {len(comp[k])} names")
            syms = comp["all"]
        elif index == "sp500":
            syms = reference.sp500_constituents()
        elif index == "sp400":
            syms = reference.sp400_constituents()
        else:
            syms = reference.sp600_constituents()
    except Exception as exc:
        print(f"Failed to fetch constituents: {exc}")
        return

    note = "" if skip else " (skip-existing OFF -- re-pulls everything)"
    print(f"\nUniverse '{index}': {len(syms)} names. Pulling EDGAR + Yahoo + sectors{note}.")
    print("Resumable: rate-limited + network-flaky; just RE-RUN this command to continue "
          "(already-loaded names are skipped).\n")

    f = edgar.ingest(syms, skip_existing=skip)
    print(f"Fundamentals (EDGAR): {f['rows']} rows | resolved={len(f['tickers_resolved'])} "
          f"missing_cik={len(f['tickers_missing_cik'])} no_facts={len(f['tickers_no_facts'])} "
          f"skipped(already had)={f.get('skipped', 0)}")
    p = prices_free.ingest(syms, skip_existing=skip)
    print(f"Prices (Yahoo):       {p['rows']} rows | resolved={len(p['tickers_resolved'])} "
          f"no_data={len(p['tickers_no_data'])} skipped(already had)={p.get('skipped', 0)}")
    try:
        s = sectors.ingest_sectors(syms, skip_existing=skip)
        print(f"Sectors (SEC SIC):    {s['rows']} rows | resolved={len(s['tickers_resolved'])} "
              f"missing_cik={len(s['tickers_missing_cik'])}\n")
    except Exception as exc:  # best-effort; never fail the pull over sectors
        print(f"Sectors (SEC SIC):    skipped ({exc})\n")
    _universe(asof)
    print("\nNote: backtests over this broad universe are SURVIVORSHIP-BIASED (free PIT membership "
          "covers only the S&P 500). Screening / valuation / flags on current names are honest.")
    print(f"\n{PERSISTENT_DISCLAIMER}")


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


def _pull_index_history(source: str | None) -> None:
    src = source or index_membership.FJA_URL
    print(f"Ingesting point-in-time S&P 500 membership from "
          f"{'fja05680/sp500 (MIT)' if not source else src} ...")
    try:
        s = index_membership.ingest(src)
    except Exception as exc:
        print(f"Failed to ingest membership: {exc}")
        return
    print(f"Loaded {s['windows']} membership windows | {s['tickers']} distinct tickers "
          f"| {s['current']} currently in index")
    conn = storage.connect()
    try:
        for y in (2002, 2008, 2012, 2016, 2020, 2024):
            n = len(universe.survivorship_universe(conn, dt.date(y, 6, 30)))
            print(f"  point-in-time universe as of {y}-06-30: {n} names")
    finally:
        conn.close()
    print("\nNote: this is the survivorship-free UNIVERSE only (membership facts, no prices). "
          "Backtesting the delisted members still needs delisted PRICES, which free Yahoo lacks.")
    print(f"\n{PERSISTENT_DISCLAIMER}")


def _pull_simfin(tickers: list[str], start: str) -> None:
    if tickers:
        names = [t.upper() for t in tickers]
    else:
        conn = storage.connect()
        try:
            if not storage.table_exists(conn, "index_membership"):
                names = []
            else:
                # Only members whose index window reaches the SimFin free window (>= `start`).
                # Names that delisted before then have no SimFin data -- skip them so we don't
                # re-fetch un-resolvable tickers every run.
                names = sorted({r[0] for r in conn.execute(
                    "SELECT DISTINCT ticker FROM index_membership "
                    "WHERE end_date IS NULL OR end_date >= ?", [start]).fetchall()})
        finally:
            conn.close()
        if not names:
            print("No tickers given and no index_membership loaded. Run pull-index-history first, "
                  "or pass tickers explicitly.")
            return
        print(f"Pulling the survivorship universe active since {start} ({len(names)} names) from "
              f"SimFin - rate-limited; re-run to resume (already-pulled names are skipped).")
    try:
        s = simfin.ingest(names, start=start)
    except RuntimeError as exc:
        print(f"Cannot pull from SimFin: {exc}")
        return
    print(f"SimFin prices (delisted-inclusive): {s['rows']} rows | "
          f"resolved={len(s['tickers_resolved'])} no_data={len(s['tickers_no_data'])} "
          f"skipped(already had)={s.get('skipped', 0)}")
    if s["tickers_no_data"]:
        sample = ", ".join(s["tickers_no_data"][:12])
        print(f"  no data (older than free ~2019 window, or uncovered): {sample}"
              f"{' ...' if len(s['tickers_no_data']) > 12 else ''}")
    if s.get("rate_limited"):
        print("\nStopped early on SimFin's free-tier rate limit (429). Progress is SAVED. "
              "Wait ~a minute, then RE-RUN this exact command to resume -- already-pulled names "
              "are skipped automatically.")
    print(f"\n{PERSISTENT_DISCLAIMER}")


def _bt_config(rebalance: str, value: float | None, tax: float | None,
               locked_test_start: dt.date | None):
    """Build a BacktestConfig from CLI overrides (None keeps the configured default)."""
    over: dict = {}
    if rebalance:
        over["rebalance"] = rebalance
    if value is not None:
        over["initial_capital_usd"] = value
    if tax is not None:
        over["tax_rate"] = tax
    if locked_test_start is not None:
        over["locked_test_start"] = locked_test_start
    return replace(config.backtest, **over) if over else config.backtest


def _backtest(strategy: str, start: dt.date, end: dt.date, rebalance: str, lane: str,
              value: float | None, tax: float | None, locked: dt.date | None) -> None:
    btcfg = _bt_config(rebalance, value, tax, locked)
    conn = storage.connect()
    try:
        res = backtest.run_backtest(conn, start, end, strategy=strategy, cfg=config,
                                    bt=btcfg, lane=lane)
    finally:
        conn.close()
    print(res.render())


def _backtest_compare(strategies: list[str], start: dt.date, end: dt.date, rebalance: str,
                      lane: str, value: float | None, tax: float | None,
                      locked: dt.date | None) -> None:
    btcfg = _bt_config(rebalance, value, tax, locked)
    conn = storage.connect()
    try:
        cmp = backtest.compare_backtests(conn, start, end, strategies, cfg=config,
                                         bt=btcfg, lane=lane)
    finally:
        conn.close()
    print(cmp.render())


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
    screen.add_argument("--min-cap", type=float, default=None,
                        help=f"market-cap floor override (default "
                             f"${config.universe_floor.min_market_cap_usd:,.0f}); lower it to reach "
                             f"smaller, less-followed names")
    screen.add_argument("--min-adv", type=float, default=None,
                        help=f"20-day ADV floor override (default "
                             f"${config.universe_floor.min_avg_dollar_volume_usd:,.0f})")
    screen.add_argument("--min-price", type=float, default=None,
                        help=f"min price override (default ${config.universe_floor.min_price_usd:.2f})")
    screen.add_argument("--min-complete", type=float, default=None, metavar="FRAC",
                        help="only show names measured on at least this fraction of the 5 factor "
                             "groups (e.g. 0.8); filters out sparse-data names like banks/REITs")

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

    pu = sub.add_parser(
        "pull-universe",
        help="Pull a whole index universe (sp500/sp400/sp600/sp1500=1500 names); RESUMABLE",
    )
    pu.add_argument("--index", default="sp1500", choices=["sp500", "sp400", "sp600", "sp1500"],
                    help="which universe to load (sp1500 = the full large+mid+small composite)")
    pu.add_argument("--no-skip", action="store_true",
                    help="re-pull names already loaded (default skips them, so re-runs resume)")
    pu.add_argument("--asof", default=dt.date.today().isoformat(),
                    help="point-in-time date (YYYY-MM-DD); default today")

    pm = sub.add_parser("pull-macro", help="Ingest vintage-aware FRED/ALFRED macro series")
    pm.add_argument("series", nargs="*", help="FRED series IDs; omit for the curated default set")
    pm.add_argument("--asof", default=dt.date.today().isoformat(),
                    help="point-in-time date (YYYY-MM-DD); default today")

    ps = sub.add_parser("pull-sectors",
                        help="Classify tickers into sectors from SEC SIC (enables the sector cap)")
    ps.add_argument("tickers", nargs="*",
                    help="tickers to classify; omit to backfill all loaded names")

    pih = sub.add_parser(
        "pull-index-history",
        help="Load survivorship-free S&P 500 membership (current + delisted) for the backtest universe",
    )
    pih.add_argument("--source", default=None,
                     help="CSV path or URL of the membership export (default: fja05680/sp500)")

    psf = sub.add_parser(
        "pull-simfin",
        help="Pull delisted-inclusive prices from SimFin (free tier; ~2019+) for survivorship-free backtests",
    )
    psf.add_argument("tickers", nargs="*",
                     help="tickers to pull; omit to pull the whole loaded index_membership universe")
    psf.add_argument("--start", default=simfin._FREE_HISTORY_START,
                     help="history start (YYYY-MM-DD); free tier serves from ~2019-06")

    mac = sub.add_parser("macro", help="Show macro values known as of a date (ALFRED vintage)")
    mac.add_argument("--asof", default=dt.date.today().isoformat(),
                     help="point-in-time date (YYYY-MM-DD); default today")

    exp = sub.add_parser("exposures", help="Rate/recession exposure of a ticker (PIT regressions)")
    exp.add_argument("ticker", help="e.g. AAPL")
    exp.add_argument("--asof", default=dt.date.today().isoformat(),
                     help="point-in-time date (YYYY-MM-DD); default today")

    bt = sub.add_parser(
        "backtest",
        help="Walk-forward, net-of-cost backtest (Phase A: free lane, INDICATIVE-ONLY)",
    )
    bt.add_argument("--strategy", default="hrp", choices=list(backtest.STRATEGIES),
                    help="fixed strategy to rebalance into at each date")
    bt.add_argument("--start", required=True, help="backtest start date (YYYY-MM-DD)")
    bt.add_argument("--end", default=dt.date.today().isoformat(),
                    help="backtest end date (YYYY-MM-DD); default today")
    bt.add_argument("--rebalance", default=config.backtest.rebalance,
                    choices=["monthly", "quarterly"], help="rebalance cadence")
    bt.add_argument("--lane", default="free", choices=["free", "simfin", "sharadar"],
                    help="'free' = Yahoo/screen (indicative); 'simfin' = PIT membership + delisted "
                         "prices (best-effort survivorship-free, needs pull-index-history + pull-simfin)")
    bt.add_argument("--value", type=float, default=None,
                    help=f"initial capital (default {config.backtest.initial_capital_usd:,.0f})")
    bt.add_argument("--tax", type=float, default=None,
                    help=f"tax rate on realized gains per rebalance "
                         f"(default {config.backtest.tax_rate}; 0 = tax-deferred)")
    bt.add_argument("--locked-test-start", default=None,
                    help="hold-out boundary (YYYY-MM-DD): report in-sample vs out-of-sample "
                         "(locked-tail) performance separately (Phase C out-of-sample discipline)")

    btc = sub.add_parser(
        "backtest-compare",
        help="Compare strategies with the Deflated Sharpe Ratio (Phase C; multiple-testing-correct)",
    )
    btc.add_argument("--strategies", nargs="+", default=list(backtest.STRATEGIES),
                     choices=list(backtest.STRATEGIES),
                     help="strategies to race (DSR uses n_trials = how many you pass)")
    btc.add_argument("--start", required=True, help="backtest start date (YYYY-MM-DD)")
    btc.add_argument("--end", default=dt.date.today().isoformat(),
                     help="backtest end date (YYYY-MM-DD); default today")
    btc.add_argument("--rebalance", default=config.backtest.rebalance,
                     choices=["monthly", "quarterly"], help="rebalance cadence")
    btc.add_argument("--lane", default="free", choices=["free", "simfin", "sharadar"],
                     help="'free' = indicative; 'simfin' = best-effort survivorship-free")
    btc.add_argument("--value", type=float, default=None,
                     help=f"initial capital (default {config.backtest.initial_capital_usd:,.0f})")
    btc.add_argument("--tax", type=float, default=None,
                     help=f"tax rate on realized gains per rebalance (default {config.backtest.tax_rate})")
    btc.add_argument("--locked-test-start", default=None,
                     help="hold-out boundary (YYYY-MM-DD): SELECTION uses only data before it; the "
                          "locked tail is scored once for the winner. Strongly recommended for compare.")

    args = ap.parse_args(argv)
    if args.cmd == "pull":
        _pull(args.tickers, dt.date.fromisoformat(args.asof))
    elif args.cmd == "screen":
        _screen(dt.date.fromisoformat(args.asof), args.top,
                args.min_cap, args.min_adv, args.min_price, args.min_complete)
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
    elif args.cmd == "pull-universe":
        _pull_universe(args.index, not args.no_skip, dt.date.fromisoformat(args.asof))
    elif args.cmd == "pull-macro":
        _pull_macro(args.series, dt.date.fromisoformat(args.asof))
    elif args.cmd == "pull-sectors":
        _pull_sectors(args.tickers)
    elif args.cmd == "pull-index-history":
        _pull_index_history(args.source)
    elif args.cmd == "pull-simfin":
        _pull_simfin(args.tickers, args.start)
    elif args.cmd == "macro":
        _macro(dt.date.fromisoformat(args.asof))
    elif args.cmd == "exposures":
        _exposures(args.ticker, dt.date.fromisoformat(args.asof))
    elif args.cmd == "backtest":
        _backtest(args.strategy, dt.date.fromisoformat(args.start),
                  dt.date.fromisoformat(args.end), args.rebalance, args.lane,
                  args.value, args.tax,
                  dt.date.fromisoformat(args.locked_test_start) if args.locked_test_start else None)
    elif args.cmd == "backtest-compare":
        _backtest_compare(args.strategies, dt.date.fromisoformat(args.start),
                          dt.date.fromisoformat(args.end), args.rebalance, args.lane,
                          args.value, args.tax,
                          dt.date.fromisoformat(args.locked_test_start)
                          if args.locked_test_start else None)


if __name__ == "__main__":
    main()
