"""Tests for the UI background-job layer (`equity_research.ui.jobs`).

Pure stdlib threading -- no Dash dependency, so these run even without the optional `[ui]` extra.
The thunks here are instant, so a short bounded poll is reliable (no real backtest is run).
"""

from __future__ import annotations

import time

from equity_research.ui.jobs import DONE, ERROR, QUEUED, JobManager


def _wait_terminal(mgr: JobManager, job_id: str, timeout: float = 5.0):
    """Poll until the job reaches a terminal state or the timeout trips (instant thunks finish fast)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = mgr.get(job_id)
        if job is not None and job.is_terminal():
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_job_runs_and_returns_result():
    mgr = JobManager(max_workers=2)
    jid = mgr.submit("single", "fixture run", lambda: {"answer": 42})
    assert isinstance(jid, str) and jid
    job = _wait_terminal(mgr, jid)
    assert job.status == DONE
    assert job.result == {"answer": 42}
    assert job.error == ""
    assert job.elapsed() >= 0.0
    assert job.finished_at is not None and job.started_at is not None


def test_job_captures_exception_instead_of_crashing():
    mgr = JobManager(max_workers=1)

    def _boom():
        raise ValueError("synthetic failure")

    jid = mgr.submit("single", "failing run", _boom)
    job = _wait_terminal(mgr, jid)
    assert job.status == ERROR
    assert "ValueError" in job.error and "synthetic failure" in job.error
    assert job.result is None  # a failed job never leaves a stale/partial result behind


def test_kind_and_label_are_preserved_for_the_poller():
    mgr = JobManager(max_workers=1)
    jid = mgr.submit("compare", "hrp/equal_weight [free]", lambda: "ok")
    job = _wait_terminal(mgr, jid)
    assert job.kind == "compare"
    assert job.label == "hrp/equal_weight [free]"


def test_get_unknown_or_empty_id_is_none():
    mgr = JobManager()
    assert mgr.get(None) is None
    assert mgr.get("") is None
    assert mgr.get("does-not-exist") is None


def test_new_job_starts_queued_and_is_not_terminal():
    # A job that blocks lets us observe the pre-terminal state deterministically.
    mgr = JobManager(max_workers=1)
    release = __import__("threading").Event()
    jid = mgr.submit("single", "blocking", lambda: release.wait(2.0))
    job = mgr.get(jid)
    assert job is not None
    assert job.status in (QUEUED, "running")  # not yet terminal
    assert not job.is_terminal()
    release.set()
    _wait_terminal(mgr, jid)
