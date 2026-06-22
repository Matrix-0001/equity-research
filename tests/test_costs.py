"""Offline tests for the v3 transaction-cost model (pure, hand-computed values)."""

from __future__ import annotations

import math

import pytest

from equity_research import costs
from equity_research.config import BacktestConfig

# spread 5 + commission 1 bps; impact 10 bps at 100% ADV participation; cap at 100%.
CFG = BacktestConfig(spread_bps=5.0, commission_bps=1.0, impact_coef=10.0,
                     impact_participation_cap=1.0)


def test_participation_basic_and_cap():
    assert costs.participation(250_000, 1_000_000, 1.0) == pytest.approx(0.25)
    # raw participation 4.0 is capped to 1.0.
    assert costs.participation(4_000_000, 1_000_000, 1.0) == pytest.approx(1.0)


def test_participation_unknown_adv_charges_at_cap():
    # Unknown / non-positive ADV -> the cap (conservative), never a free pass.
    assert costs.participation(1_000_000, float("nan"), 1.0) == 1.0
    assert costs.participation(1_000_000, 0.0, 1.0) == 1.0
    assert costs.participation(1_000_000, None, 1.0) == 1.0


def test_trade_cost_full_participation_hand_computed():
    # participation 1.0 -> impact 10*sqrt(1)=10 bps; total 5+1+10=16 bps on $1,000,000 = $1,600.
    assert costs.trade_cost(1_000_000, 1_000_000, CFG) == pytest.approx(1_600.0)


def test_trade_cost_partial_participation_hand_computed():
    # participation 0.25 -> impact 10*sqrt(0.25)=5 bps; total 5+1+5=11 bps on $250,000 = $275.
    assert costs.trade_cost(250_000, 1_000_000, CFG) == pytest.approx(275.0)


def test_trade_cost_caps_impact_but_charges_full_notional():
    # participation capped at 1.0 -> impact 10 bps; total 16 bps on the FULL $4,000,000 = $6,400.
    assert costs.trade_cost(4_000_000, 1_000_000, CFG) == pytest.approx(6_400.0)


def test_trade_cost_unknown_adv_is_conservative():
    # No ADV -> participation cap -> impact 10 bps; total 16 bps on $1,000,000 = $1,600.
    assert costs.trade_cost(1_000_000, float("nan"), CFG) == pytest.approx(1_600.0)


def test_trade_cost_zero_notional_is_free():
    assert costs.trade_cost(0.0, 1_000_000, CFG) == 0.0


def test_trade_cost_is_symmetric_in_sign():
    # Cost depends on the absolute notional (a buy and an equal sell cost the same).
    assert costs.trade_cost(-500_000, 1e9, CFG) == costs.trade_cost(500_000, 1e9, CFG)


def test_impact_bps_zero_coef_is_zero():
    cfg = BacktestConfig(impact_coef=0.0)
    assert costs.impact_bps(1.0, cfg) == 0.0
    assert math.isfinite(costs.trade_cost(1_000_000, 1_000, cfg))
