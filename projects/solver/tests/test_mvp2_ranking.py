"""Tests for the MVP-2 order promising and multi-objective ranking."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.services.multi_objective import (
    CUSTOMER_TIER_RANK,
    rank_orders,
)
from backend.services.order_promising import promise_batch, promise_order


# ---------------------------------------------------------------------------
# Order promising (CP-SAT)
# ---------------------------------------------------------------------------


def _materials() -> list[dict]:
    return [
        {
            "id": "MAT-FIN-A",
            "name": "成品A",
            "category": "finished",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 100,
        },
        {
            "id": "MAT-FIN-B",
            "name": "成品B",
            "category": "finished",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 100,
        },
        {
            "id": "MAT-TOX",
            "name": "剧毒",
            "category": "raw_material",
            "unit": "kg",
            "special_control_level": 0,
            "supply_risk": "single",
            "lead_time_bucket": "long",
            "lead_time_days": 90,
            "safety_stock": 50,
        },
        {
            "id": "MAT-RAW-OK",
            "name": "原料OK",
            "category": "raw_material",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 0,
        },
    ]


def test_promise_order_finds_alternate_when_infeasible():
    base = date(2026, 7, 2)
    inventory = [
        {"material_id": "MAT-FIN-A", "quantity": 50},
        {"material_id": "MAT-FIN-B", "quantity": 800},
        {"material_id": "MAT-TOX", "quantity": 200},
        {"material_id": "MAT-RAW-OK", "quantity": 1000},
    ]
    request = {
        "material_id": "MAT-FIN-A",
        "quantity": 500,
        "desired_date": (base + timedelta(days=3)).isoformat(),
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
    assert any(a["material_id"] == "MAT-FIN-B" for a in result["alternates"])


def test_promise_order_isolated_material_returns_no_series():
    base = date(2026, 7, 2)
    inventory = [
        {"material_id": "MAT-FIN-A", "quantity": 50},
        {"material_id": "MAT-FIN-B", "quantity": 800},
        {"material_id": "MAT-TOX", "quantity": 200},
        {"material_id": "MAT-RAW-OK", "quantity": 1000},
    ]
    request = {
        "material_id": "MAT-TOX",
        "quantity": 10,
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
    # Special control level 0 -> isolated -> can_promise False (alternates searched).
    assert result["can_promise"] is False
    assert result["alternates"]  # non-empty: raw materials other than MAT-TOX


def test_promise_order_lateness_is_reported():
    base = date(2026, 7, 2)
    inventory = [
        {"material_id": "MAT-FIN-A", "quantity": 50},
        {"material_id": "MAT-FIN-B", "quantity": 800},
        {"material_id": "MAT-TOX", "quantity": 200},
        {"material_id": "MAT-RAW-OK", "quantity": 1000},
    ]
    # Want 40 kg, but inventory is 50 (after 10% buffer = 45, so feasible).
    request = {
        "material_id": "MAT-FIN-A",
        "quantity": 40,
        "desired_date": (base - timedelta(days=2)).isoformat(),
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
    assert result["on_time"] is False
    assert result["lateness_days"] >= 2


def test_promise_batch_returns_independent_decisions():
    base = date(2026, 7, 2)
    inventory = [
        {"material_id": "MAT-FIN-A", "quantity": 50},
        {"material_id": "MAT-FIN-B", "quantity": 800},
        {"material_id": "MAT-TOX", "quantity": 200},
        {"material_id": "MAT-RAW-OK", "quantity": 1000},
    ]
    requests = [
        {"material_id": "MAT-FIN-A", "quantity": 30, "desired_date": (base + timedelta(days=1)).isoformat()},
        {"material_id": "MAT-FIN-A", "quantity": 9999, "desired_date": (base + timedelta(days=1)).isoformat()},
    ]
    result = promise_batch(
        materials=_materials(),
        inventory=inventory,
        inbound=[],
        open_orders=[],
        requests=requests,
        horizon_days=10,
    )
    assert result["total"] == 2
    assert result["promised"] == 1
    assert result["short"] == 1


# ---------------------------------------------------------------------------
# Multi-objective ranking
# ---------------------------------------------------------------------------


def test_lexicographic_puts_premium_first():
    base = date(2026, 7, 2)
    materials = [{"id": "MAT-A", "category": "finished", "special_control_level": 2, "supply_risk": "multiple", "lead_time_bucket": "short", "lead_time_days": 7, "safety_stock": 0, "unit": "kg"}]
    open_orders = [
        {"id": "ORD-1", "customer_id": "C1", "customer_tier": "base", "material_id": "MAT-A", "quantity": 10, "creation_date": base, "due_date": base + timedelta(days=5)},
        {"id": "ORD-2", "customer_id": "C2", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 10, "creation_date": base, "due_date": base + timedelta(days=5)},
    ]
    r = rank_orders(materials=materials, inventory=[{"material_id": "MAT-A", "quantity": 100}], open_orders=open_orders, strategy="lexicographic")
    assert r["status"] == "ok"
    assert r["ranking"][0]["order_id"] == "ORD-2"
    assert r["ranking"][0]["customer_tier"] == "premium"


def test_epsilon_constraint_excludes_overdue():
    base = date.today()  # The public service plans from today.
    materials = [{"id": "MAT-A", "category": "finished", "special_control_level": 2, "supply_risk": "multiple", "lead_time_bucket": "short", "lead_time_days": 7, "safety_stock": 0, "unit": "kg"}]
    open_orders = [
        {"id": "ORD-late", "customer_id": "C1", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 10, "creation_date": base, "due_date": base - timedelta(days=5)},
        {"id": "ORD-ok", "customer_id": "C2", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 10, "creation_date": base, "due_date": base + timedelta(days=5)},
    ]
    r = rank_orders(materials=materials, inventory=[{"material_id": "MAT-A", "quantity": 100}], open_orders=open_orders, strategy="epsilon_constraint", epsilon={"max_lateness_days": 0})
    assert r["status"] == "ok"
    eligible_ids = [o["order_id"] for o in r["ranking"] if o.get("eligible")]
    assert "ORD-ok" in eligible_ids
    assert "ORD-late" not in eligible_ids


def test_pareto_returns_multiple_rankings_and_front():
    base = date(2026, 7, 2)
    materials = [{"id": "MAT-A", "category": "finished", "special_control_level": 2, "supply_risk": "multiple", "lead_time_bucket": "short", "lead_time_days": 7, "safety_stock": 0, "unit": "kg"}]
    open_orders = [
        {"id": "ORD-1", "customer_id": "C1", "customer_tier": "base", "material_id": "MAT-A", "quantity": 10, "creation_date": base, "due_date": base + timedelta(days=5)},
        {"id": "ORD-2", "customer_id": "C2", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 20, "creation_date": base, "due_date": base + timedelta(days=3)},
        {"id": "ORD-3", "customer_id": "C3", "customer_tier": "comfort", "material_id": "MAT-A", "quantity": 30, "creation_date": base, "due_date": base + timedelta(days=2)},
    ]
    r = rank_orders(materials=materials, inventory=[{"material_id": "MAT-A", "quantity": 100}], open_orders=open_orders, strategy="pareto")
    assert r["status"] == "ok"
    assert len(r["rankings"]) >= 2
    assert len(r["pareto_front"]) >= 1


def test_weighted_sum_with_custom_weights_emphasises_quantity():
    base = date(2026, 7, 2)
    materials = [{"id": "MAT-A", "category": "finished", "special_control_level": 2, "supply_risk": "multiple", "lead_time_bucket": "short", "lead_time_days": 7, "safety_stock": 0, "unit": "kg"}]
    open_orders = [
        {"id": "ORD-small", "customer_id": "C1", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 5, "creation_date": base, "due_date": base + timedelta(days=1)},
        {"id": "ORD-big", "customer_id": "C2", "customer_tier": "base", "material_id": "MAT-A", "quantity": 500, "creation_date": base, "due_date": base + timedelta(days=5)},
    ]
    r_qty = rank_orders(materials=materials, inventory=[{"material_id": "MAT-A", "quantity": 1000}], open_orders=open_orders, strategy="weighted_sum", weights={"priority": 0.0, "quantity": 1.0, "lateness": 0.0})
    assert r_qty["ranking"][0]["order_id"] == "ORD-big"
