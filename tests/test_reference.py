"""Offline tests for the constituent-list parsing (the stdlib Wikipedia table reader)."""

from __future__ import annotations

from equity_research.data import reference as r


def test_normalize_dotted_and_junk():
    assert r._normalize("brk.b") == "BRK-B"        # dotted share class -> EDGAR/Yahoo form
    assert r._normalize("  AAPL ") == "AAPL"
    assert r._normalize("") == ""
    assert r._normalize("TOOLONG") == ""           # > 6 chars -> not a ticker
    assert r._normalize("BF.B [1]") == "BF-B"      # trailing footnote token dropped


_HTML = """
<table class="wikitable sortable">
  <tr><th>Company</th><th>Symbol</th><th>GICS Sector</th></tr>
  <tr><td>Apple</td><td><a href="/x">AAPL</a></td><td>Information Technology</td></tr>
  <tr><td>Berkshire</td><td>BRK.B</td><td>Financials</td></tr>
</table>
<table class="wikitable">
  <tr><th>Date</th><th>Added</th></tr>
  <tr><td>2024-01-01</td><td>Foo</td></tr>
</table>
"""


def test_symbols_from_html_picks_symbol_column():
    # Pulls the ticker column from the first wikitable with a symbol header; normalizes BRK.B,
    # reads the linked ticker AAPL, and ignores the second (changes) table that has no symbol col.
    assert r._symbols_from_html(_HTML, min_rows=2) == ["AAPL", "BRK-B"]


def test_symbols_from_html_none_without_symbol_header():
    html = ('<table class="wikitable"><tr><th>A</th><th>B</th></tr>'
            '<tr><td>1</td><td>2</td></tr></table>')
    assert r._symbols_from_html(html, min_rows=1) == []


def test_symbols_from_html_respects_min_rows():
    # A real constituents table has hundreds of rows; a tiny table is rejected (avoids grabbing a
    # stray 2-row infobox that happens to have a 'Symbol' header).
    assert r._symbols_from_html(_HTML, min_rows=100) == []
