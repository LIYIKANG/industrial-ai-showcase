"""Tests that exercise the PyJobShop engine path explicitly.

These complement ``tests/test_mvp2_ranking.py`` (which only checks the
public API) by asserting the engine resolves to the PyJobShop backend
and that the model's ``select_exactly_one`` semantics pick the
earliest feasible day.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

pyjobshop = pytest.importorskip("pyjobshop")

from backend.services import order_promising
from backend.services.order_promising import promise_order


def _materials() -> list[dict]:
    return [
        {
            "id": "MAT-A",
            "name": "Mat A",
            "category": "finished",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 0,
        }
    ]


def test_engine_resolves_to_pyjobshop(monkeypatch):
    """When pyjobshop is importable, the engine factory must pick it."""
    engine = order_promising._make_engine()
    assert engine.name.startswith("PyJobShop"), engine.name


def test_pyjobshop_picks_earliest_feasible_day():
    base = date(2026, 7, 2)
    inventory = [{"material_id": "MAT-A", "quantity": 1000}]
    request = {
        "material_id": "MAT-A",
        "quantity": 100,
        "desired_date": (base + timedelta(days=2)).isoformat(),
    }
    result = promise_order(
        materials=_materials(),
        inventory=inventory,
        inbound=[],
        open_orders=[],
        request=request,
        horizon_days=10,
    )
    assert result["can_promise"] is True
    assert result["promised_date"] == base.isoformat()  # day 0
    assert result["lateness_days"] == 0
    assert result["engine"].startswith("PyJobShop")
    assert result["on_time"] is True


def test_pyjobshop_selects_only_one_day():
    """If we ask for more qty than any single day allows, must fail clean."""
    base = date(2026, 7, 2)
    inventory = [{"material_id": "MAT-A", "quantity": 50}]  # 50 kg total
    request = {
        "material_id": "MAT-A",
        "quantity": 9999,
        "desired_date": (base + timedelta(days=1)).isoformat(),
    }
    result = promise_order(
        materials=_materials(),
        inventory=inventory,
        inbound=[],
        open_orders=[],
        request=request,
        horizon_days=10,
    )
    assert result["can_promise"] is False
    assert result["status"] == "infeasible"
    # alternates list may be empty (no other finished materials) -- that is fine
