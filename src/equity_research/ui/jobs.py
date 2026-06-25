"""Tiny background-job layer for the UI (stdlib only -- no Dash, so it is testable on its own).

Why this exists: a single backtest is ~25s and a compare ~35s. A Dash callback runs on the web
request thread, so computing a backtest synchronously would block the server (and time out the
browser) for the whole run. Instead the callback SUBMITS a job here and returns immediately; a
`dcc.Interval` then polls the job's status until it is done and renders the result.

Design notes:
  - A module-level singleton `MANAGER` survives across stateless Dash callbacks (the job dict lives
    here, not in the browser). The browser only holds the opaque job id (in a `dcc.Store`).
  - Jobs run on a small `ThreadPoolExecutor`. The work is DuckDB + numpy (which release the GIL for
    their heavy sections), so a couple of workers let two viewers run without one fully blocking the
    other; this is a personal/few-friends local tool, not a compute cluster.
  - Each job's thunk must open its OWN DuckDB connection inside the worker thread (a DuckDB
    connection is not safe to share across threads). The thunk is supplied by the caller (app.py),
    which keeps this module free of any equity-research / Dash imports.
  - All mutation of a `Job` goes through the manager lock, so the polling reader never sees a torn
    state.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

# Job lifecycle states.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"


@dataclass
class Job:
    """One unit of background work and its current state (read by the UI poller)."""

    id: str
    kind: str                       # caller-defined tag, e.g. "single" | "compare"
    label: str                      # human-readable summary of the parameters (shown while running)
    status: str = QUEUED
    result: Any = None              # the thunk's return value once DONE (e.g. a BacktestResult)
    error: str = ""                 # "ExcType: message" if the thunk raised
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def elapsed(self) -> float:
        """Wall-clock seconds the job has been (or was) running; 0 before it starts."""
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def is_terminal(self) -> bool:
        return self.status in (DONE, ERROR)


class JobManager:
    """Thread-safe registry + executor. One instance is shared process-wide (see `MANAGER`)."""

    def __init__(self, max_workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="eqr-bt"
        )

    def submit(self, kind: str, label: str, thunk: Callable[[], Any]) -> str:
        """Register a job and run `thunk()` off-thread. Returns the job id immediately."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job, thunk)
        return job.id

    def _run(self, job: Job, thunk: Callable[[], Any]) -> None:
        with self._lock:
            job.status = RUNNING
            job.started_at = time.time()
        try:
            result = thunk()
            with self._lock:
                job.result = result
                job.status = DONE
        except Exception as exc:  # noqa: BLE001 -- surface ANY failure to the UI, never swallow it
            with self._lock:
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = ERROR
        finally:
            with self._lock:
                job.finished_at = time.time()

    def get(self, job_id: str | None) -> Job | None:
        if not job_id:
            return None
        with self._lock:
            return self._jobs.get(job_id)


# Process-wide singleton used by the Dash app (and importable for tests).
MANAGER = JobManager()
