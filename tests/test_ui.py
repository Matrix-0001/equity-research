"""Smoke test for the optional Dash UI (skipped when the `[ui]` extra isn't installed)."""

from __future__ import annotations

import pytest

pytest.importorskip("dash")  # UI is an optional extra; skip if dash isn't installed

from equity_research.ui.app import build_app  # noqa: E402


def test_ui_app_builds_with_honesty_chrome():
    app = build_app()
    assert app.title == "Equity Research - local"
    assert app.layout is not None
    # The persistent disclaimer / honesty framing is baked into the layout, not optional.
    rendered = str(app.layout)
    assert "not investment advice" in rendered
    assert "hidden-gem screen" in rendered
