"""Tests for the bounded LLM module (no network — parsing + the not-configured path)."""

from __future__ import annotations

from equity_research import llm


def test_parse_plain_json():
    out = llm._parse_bull_bear('{"bull_case": "cheap and profitable", "bear_case": "volatile"}')
    assert out == {"bull_case": "cheap and profitable", "bear_case": "volatile"}


def test_parse_tolerates_wrapping_text():
    raw = "Here you go:\n```json\n{\"bull_case\": \"A\", \"bear_case\": \"B\"}\n```\nThanks!"
    out = llm._parse_bull_bear(raw)
    assert out == {"bull_case": "A", "bear_case": "B"}


def test_parse_rejects_incomplete():
    assert llm._parse_bull_bear('{"bull_case": "only one"}') is None
    assert llm._parse_bull_bear("not json at all") is None


def test_not_configured_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.is_configured() is False
    assert llm.generate_bull_bear({"ticker": "AAA"}) is None
