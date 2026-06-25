"""Tiny HTTP helper with retry/backoff.

Free data endpoints (SEC, Yahoo) occasionally time out or rate-limit; a few
retries with linear backoff make ingestion robust without masking real failures
(after the last attempt the original exception is re-raised).
"""

from __future__ import annotations

import time


def get_json(
    url: str,
    user_agent: str,
    timeout: int = 30,
    retries: int = 4,
    backoff_s: float = 1.5,
) -> dict:
    import requests

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff_s * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def get_text(
    url: str,
    user_agent: str,
    timeout: int = 12,
    retries: int = 2,
    backoff_s: float = 1.0,
) -> str:
    """GET returning the raw response text (e.g. an RSS/XML feed). Same retry/backoff posture as
    `get_json`, but fewer/shorter retries -- used for the best-effort live news feed where a quick
    failure should degrade gracefully rather than stall a page render."""
    import requests

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff_s * (attempt + 1))
    assert last_exc is not None
    raise last_exc
