"""Run-scoped memoization for the backtest hot path (perf only -- never changes results).

A backtest re-derives the SAME point-in-time views many times: at each rebalance the screening
universe, the factor profile, and the covariance of the chosen basket are each computed more than
once (the engine asks for ADV, selection, and weighting separately), and `compare_backtests` runs
several strategies over the IDENTICAL dates. None of those views depend on the strategy -- only on
`as_of` + config (+ the basket, for covariance) -- so they can be memoized within a single run/compare.

The cache is an explicit plain `dict` created at the top of `run_backtest` / `compare_backtests` and
threaded down (default `None` = no caching, so every existing caller and test is unaffected). It is
discarded when the run finishes, so it cannot go stale across a data refresh -- there is no global
state. Keys must capture every input that changes the value (date, the relevant config identity, and
the ticker set for covariance); see the call sites.
"""

from __future__ import annotations

from typing import Any, Callable

_MISS = object()


def cache_get(cache: dict | None, key: Any, compute: Callable[[], Any]) -> Any:
    """Return `cache[key]`, computing + storing it on a miss. `cache=None` just calls `compute()`.

    The cached object is returned by reference; callers in this codebase derive new objects from it
    (set_index / slicing / numpy ops) rather than mutating it in place, so sharing is safe.
    """
    if cache is None:
        return compute()
    hit = cache.get(key, _MISS)
    if hit is _MISS:
        hit = cache[key] = compute()
    return hit
