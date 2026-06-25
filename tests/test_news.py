"""Tests for the media-headlines RSS parser (`news.parse_rss`). Pure, offline -- no network."""

from __future__ import annotations

from equity_research.news import parse_rss

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Apple unveils a new chip - Reuters</title>
    <link>https://news.google.com/a1</link>
    <pubDate>Fri, 20 Jun 2026 10:00:00 GMT</pubDate>
    <source url="http://reuters.com">Reuters</source>
  </item>
  <item>
    <title>Analysts weigh in on AAPL - Bloomberg</title>
    <link>https://news.google.com/a2</link>
    <pubDate>Thu, 19 Jun 2026 09:00:00 GMT</pubDate>
    <source url="http://bloomberg.com">Bloomberg</source>
  </item>
  <item>
    <title>Item with no link should be skipped</title>
  </item>
</channel></rss>"""


def test_parse_rss_extracts_and_strips_outlet_suffix():
    items = parse_rss(_RSS, n=8)
    assert len(items) == 2                                 # the link-less item is skipped, not faked
    assert items[0]["title"] == "Apple unveils a new chip"  # trailing ' - Reuters' stripped
    assert items[0]["source"] == "Reuters"
    assert items[0]["url"] == "https://news.google.com/a1"
    assert "20 Jun 2026" in items[0]["published"]


def test_parse_rss_respects_limit():
    assert len(parse_rss(_RSS, n=1)) == 1


def test_parse_rss_empty_feed():
    empty = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
    assert parse_rss(empty, n=8) == []
