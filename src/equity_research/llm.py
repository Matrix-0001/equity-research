"""Bounded LLM narrative — bull/bear cases from precomputed figures ONLY.

The LLM's role is strictly bounded (per the platform spec): translate already-computed
figures into plain-language bull/bear narratives. It must never introduce a number not
present in the figures, and never give buy/sell advice.

Defaults to claude-opus-4-8 (override with EQR_LLM_MODEL). Reads ANTHROPIC_API_KEY from
the environment. If the key or the `anthropic` package is missing, returns None so the
report reads "Not estimated" rather than failing.
"""

from __future__ import annotations

import json
import os
import re

_DEFAULT_MODEL = "claude-opus-4-8"

_SYSTEM = (
    "You are the narrative layer of an equity-research tool. You are given ONLY "
    "pre-computed figures about a single stock (factor percentile ranks, a valuation "
    "read, and accounting-quality flags). Write a concise bull case and bear case "
    "grounded STRICTLY in those figures.\n\n"
    "Hard rules:\n"
    "- Never introduce a number, ratio, statistic, date, or fact that is not present in "
    "the figures. Refer to figures qualitatively (e.g. 'cheap versus peers', 'top-quartile "
    "profitability', 'elevated leverage').\n"
    "- Do not give buy/sell/hold advice and do not use the word 'recommend'. This is "
    "research, not advice.\n"
    "- 2-4 sentences per case. If the figures are sparse or several read 'Not estimated', "
    "say so plainly instead of speculating.\n"
    "- Respond with ONLY a JSON object, no preamble and no code fences:\n"
    '  {"bull_case": "...", "bear_case": "..."}'
)


def _model() -> str:
    return os.getenv("EQR_LLM_MODEL", _DEFAULT_MODEL)


def _figures_to_text(figures: dict[str, str]) -> str:
    lines = [f"{k}: {v}" for k, v in figures.items()]
    return "Figures for the security:\n" + "\n".join(lines)


def _parse_bull_bear(text: str) -> dict | None:
    """Extract {bull_case, bear_case} from the model's text, tolerant of stray wrapping."""
    data = None
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                return None
    if not isinstance(data, dict):
        return None
    bull, bear = data.get("bull_case"), data.get("bear_case")
    if not bull or not bear:
        return None
    return {"bull_case": str(bull).strip(), "bear_case": str(bear).strip()}


def is_configured() -> bool:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def generate_bull_bear(figures: dict[str, str], model: str | None = None) -> dict | None:
    """Bull/bear narrative grounded only in `figures`. None if LLM not configured/errored."""
    if not is_configured():
        return None
    import anthropic

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model or _model(),
            max_tokens=700,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _figures_to_text(figures)}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text"
        )
    except Exception:
        return None
    return _parse_bull_bear(text)
