"""Reference data — free universe seeds (no key required).

Constituent lists for the S&P 500 (large), S&P 400 (mid), and S&P 600 (small) -> the S&P Composite
1500. These are CURRENT membership only, so a universe built from them for *backtesting* is
survivorship-biased (it omits names removed in the past) — fine for seeding a live screen / a hidden-
gem search across the size spectrum, NOT for a survivorship-free historical test. Point-in-time
membership is only free for the S&P 500 (fja05680, `data/index_membership.py`); below that, backtests
stay "indicative only" until a PIT-membership source is added.

S&P 500 comes from a clean public CSV; the 400/600 lists come from Wikipedia, parsed with a tiny
stdlib HTML table reader (no lxml/bs4 dependency, consistent with the project's lean-deps posture).
"""

from __future__ import annotations

import csv
import io
from html.parser import HTMLParser

from equity_research.config import Config, config

_SP500_CSV = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
)
_SP400_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"
_SP600_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"

_SYMBOL_HEADERS = {"symbol", "ticker", "ticker symbol"}


def _normalize(sym: str) -> str:
    """Upper-case + EDGAR/Yahoo dotted-class normalization (BRK.B -> BRK-B); '' if not ticker-like."""
    s = (sym or "").strip().upper().replace(".", "-")
    s = s.split()[0] if s else s                          # drop footnote markers / trailing notes
    if s and s.isascii() and 1 <= len(s) <= 6 and s.replace("-", "").isalnum():
        return s
    return ""


class _WikiTableParser(HTMLParser):
    """Collect every <table class=wikitable> as a list of rows, each a list of cell-text strings."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in = False
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table" and "wikitable" in dict(attrs).get("class", ""):
            self._in, self._table = True, []
            self.tables.append(self._table)
        elif self._in and tag == "tr" and self._table is not None:
            self._row = []
            self._table.append(self._row)
        elif self._in and tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._row.append(self._cell)  # type: ignore[arg-type]

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "table" and self._in:
            self._in, self._table = False, None


def _symbols_from_html(html: str, min_rows: int) -> list[str]:
    """Pull the ticker column from the first wikitable that has a symbol header + enough rows."""
    p = _WikiTableParser()
    p.feed(html)
    for table in p.tables:
        if len(table) < 2:
            continue
        header = ["".join(c).strip().lower() for c in table[0]]
        col = next((i for i, h in enumerate(header) if h in _SYMBOL_HEADERS), None)
        if col is None:
            continue
        syms = [_normalize("".join(row[col])) for row in table[1:] if col < len(row)]
        syms = [s for s in syms if s]
        if len(syms) >= min_rows:
            return syms
    return []


def _wiki_constituents(url: str, cfg: Config, min_rows: int) -> list[str]:
    import requests

    resp = requests.get(url, headers={"User-Agent": cfg.sec_user_agent}, timeout=30)
    resp.raise_for_status()
    return _symbols_from_html(resp.text, min_rows=min_rows)


def sp500_constituents(cfg: Config = config) -> list[str]:
    """Current S&P 500 tickers, normalized for EDGAR/Yahoo (e.g. BRK.B -> BRK-B)."""
    import requests

    resp = requests.get(_SP500_CSV, headers={"User-Agent": cfg.sec_user_agent}, timeout=30)
    resp.raise_for_status()
    rows = csv.DictReader(io.StringIO(resp.text))
    out: list[str] = []
    for row in rows:
        sym = _normalize(row.get("Symbol") or "")
        if sym:
            out.append(sym)
    return out


def sp400_constituents(cfg: Config = config) -> list[str]:
    """Current S&P 400 (mid-cap) tickers, from Wikipedia (~400 names)."""
    return _wiki_constituents(_SP400_WIKI, cfg, min_rows=300)


def sp600_constituents(cfg: Config = config) -> list[str]:
    """Current S&P 600 (small-cap) tickers, from Wikipedia (~600 names)."""
    return _wiki_constituents(_SP600_WIKI, cfg, min_rows=400)


def sp1500_constituents(cfg: Config = config) -> dict[str, list[str]]:
    """S&P Composite 1500 = 500 + 400 + 600, deduped. Returns {'sp500','sp400','sp600','all'}.

    Per-source lists are returned (not just the union) so the caller can report coverage honestly;
    a source that fails to fetch is surfaced by the caller, never silently treated as empty.
    """
    sp500 = sp500_constituents(cfg)
    sp400 = sp400_constituents(cfg)
    sp600 = sp600_constituents(cfg)
    seen: set[str] = set()
    all_: list[str] = []
    for lst in (sp500, sp400, sp600):
        for s in lst:
            if s not in seen:
                seen.add(s)
                all_.append(s)
    return {"sp500": sp500, "sp400": sp400, "sp600": sp600, "all": all_}
