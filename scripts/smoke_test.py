#!/usr/bin/env python
"""End-to-end smoke test / demo for the Local Equity Research & Portfolio Platform.

Runs the test suite, then exercises every CLI surface against the local ./data store, so you
can see the whole application work in one go. It shells out to the real CLI
(`python -m equity_research.cli ...`) -- the same entry point a user drives.

Run with the project venv (from anywhere):

    .venv\\Scripts\\python scripts\\smoke_test.py            # demo against existing ./data
    .venv\\Scripts\\python scripts\\smoke_test.py --pull     # seed a small universe + macro first

Honest notes: the test suite is the authoritative correctness check (a failure aborts with a
non-zero exit). The `--pull` steps hit the network (EDGAR / Yahoo / FRED) and the macro steps
need FRED_API_KEY in .env. Without data loaded, the read commands print "pull ... first".
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(args: list[str], desc: str, check: bool = False) -> bool:
    print("\n" + "=" * 78)
    print(f"# {desc}")
    print("=" * 78, flush=True)
    proc = subprocess.run([PY, *args], cwd=REPO)
    ok = proc.returncode == 0
    status = "ok" if ok else f"FAILED (exit {proc.returncode})"
    print(f"[{status}] {desc}", flush=True)
    if check and not ok:
        sys.exit(proc.returncode)
    return ok


def _cli(*args: str) -> list[str]:
    return ["-m", "equity_research.cli", *args]


def main() -> None:
    ap = argparse.ArgumentParser(description="Smoke test / demo runner")
    ap.add_argument("--pull", action="store_true",
                    help="seed a small universe (pull-sp500 --limit) + macro before the demo")
    ap.add_argument("--limit", type=int, default=15, help="tickers to seed when using --pull")
    args = ap.parse_args()

    print(f"Repo:   {REPO}")
    print(f"Python: {PY}")

    # 1) Test suite -- the authoritative correctness check (aborts on failure).
    _run(["-m", "pytest", "-q"], "Unit + integration test suite", check=True)

    # 2) Optional: seed data from the network.
    if args.pull:
        _run(_cli("pull-sp500", "--limit", str(args.limit)),
             f"Seed universe (S&P 500, first {args.limit})")
        _run(_cli("pull-macro"), "Seed FRED/ALFRED macro (needs FRED_API_KEY)")

    # 3) Exercise every read surface against ./data.
    results = [
        _run(_cli("universe"), "Loaded universe"),
        _run(_cli("screen", "--top", "10"), "Factor screen (top 10 by composite)"),
        _run(_cli("report", "AAPL"), "Full SecurityReport: AAPL (+ optional LLM bull/bear)"),
        _run(_cli("construct", "--method", "hrp", "--top", "10"), "Portfolio: HRP"),
        _run(_cli("construct", "--method", "risk_parity", "--top", "10"),
             "Portfolio: ERC risk parity"),
        _run(_cli("construct", "--method", "kelly", "--edge", "AAPL=0.06", "--edge", "MSFT=0.05",
                  "--edge", "NVDA=0.09", "--max-weight", "0.30"),
             "Portfolio: fractional Kelly (user-supplied edges)"),
        _run(_cli("macro"), "Macro values known as-of today (ALFRED vintage)"),
        _run(_cli("exposures", "AAPL"), "Macro rate/recession exposures: AAPL"),
    ]

    print("\n" + "=" * 78)
    n_ok = sum(results)
    print(f"# Smoke test complete: tests passed; {n_ok}/{len(results)} CLI surfaces ran cleanly.")
    if n_ok < len(results):
        print("# (A non-ok read step usually means ./data is empty -- re-run with --pull.)")
    print("=" * 78)


if __name__ == "__main__":
    main()
