"""Media news headlines for a company (the PRIVATE half of the News section).

Honesty + licensing posture (decided with Darrell 2026-06-25):
  - We show only **headline + outlet + date + link** to the original article (a normal RSS-reader
    use). We NEVER reproduce article text and NEVER let the LLM summarize or invent a headline --
    every item is a real, linked article from the feed, or nothing.
  - Source: Google News RSS search (free, no key). Unlike the SEC-filings feed (a public primary
    source, freely shareable), media headlines are third-party content -> treat as **personal use /
    keep private or behind auth** when the app is shared. The UI stamps this.
  - Best-effort + graceful: a slow or failed fetch must not break the page. A short-TTL in-process
    cache avoids re-hitting the feed on every interaction; a fetch error propagates so the UI can
    show "unavailable" rather than a fabricated/empty-looking feed.

This module is import-light (stdlib XML) and has a PURE `parse_rss` that is unit-tested offline.
"""

from __future__ import annotations

import time
import urllib.parse
import xml.etree.ElementTree as ET

from equity_research.config import Config, config
from equity_research.data import http

_UA = "equity-research-local (personal research tool)"
_CACHE_TTL_S = 600.0                       # 10 min: news changes, but not every page interaction
_cache: dict[str, tuple[float, list[dict]]] = {}


def _rss_url(query: str) -> str:
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def parse_rss(xml_text: str, n: int = 8) -> list[dict]:
    """Parse an RSS feed into [{title, source, published, url}] (pure -> unit-tested).

    Google News titles are usually 'Headline - Outlet'; when a <source> element gives the outlet we
    strip that trailing ' - Outlet' from the title so it isn't duplicated. Malformed XML raises
    (the caller decides how to degrade); individual items missing a field are skipped, not faked.
    """
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        pub = (item.findtext("pubDate") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        items.append({"title": title, "source": source, "published": pub, "url": link})
        if len(items) >= n:
            break
    return items


def fetch_headlines(query: str, n: int = 8, cfg: Config = config,
                    use_cache: bool = True) -> list[dict]:
    """Live media headlines for `query` (a company name or ticker). Cached for `_CACHE_TTL_S`.

    Raises on a network/feed error (so the UI can say "unavailable"); returns [] only when the feed
    genuinely had no items. Never caches an error.
    """
    key = query.strip().lower()
    now = time.time()
    if use_cache and key in _cache and now - _cache[key][0] < _CACHE_TTL_S:
        return _cache[key][1]
    # Short timeout / single retry: news is best-effort, so fail fast and let the UI say so rather
    # than hold a spinner for the full default backoff.
    xml_text = http.get_text(_rss_url(query), _UA, timeout=8, retries=1)
    items = parse_rss(xml_text, n)
    _cache[key] = (now, items)
    return items
