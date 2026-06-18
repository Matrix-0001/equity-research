"""Smoke tests for the honest-output scaffolding (no vendor data / API needed)."""

from equity_research.disclaimer import (
    NOT_ESTIMATED,
    PERSISTENT_DISCLAIMER,
    SecurityReport,
)


def test_unpopulated_report_defaults_to_not_estimated():
    r = SecurityReport(ticker="ABC", company="Example Corp")
    # Every analytical field must default to the sentinel, never a fabricated value.
    assert r.valuation_read == NOT_ESTIMATED
    assert r.factor_profile == NOT_ESTIMATED
    assert r.completeness_pct() == 0.0


def test_completeness_tracks_populated_fields():
    r = SecurityReport(ticker="ABC", company="Example Corp")
    r.valuation_read = "Implied 5yr growth 6-9% vs peer median 7%"
    r.factor_profile = "Value p80 / Quality p65 / Momentum p40"
    assert 0.0 < r.completeness_pct() < 1.0


def test_render_always_includes_disclaimer():
    r = SecurityReport(ticker="ABC", company="Example Corp")
    out = r.render()
    assert PERSISTENT_DISCLAIMER in out
    assert "DISCLAIMER:" in out
