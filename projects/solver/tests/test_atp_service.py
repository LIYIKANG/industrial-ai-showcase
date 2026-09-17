"""Unit tests for the chemical ATP service and its rule engines."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from backend.core.critical_material import (
    RISK_MULTIPLIERS,
    apply_critical_material_rules,
    compute_critical_material_rules,
)
from backend.core.special_control import (
    adjust_atp,
    compute_special_control_rules,
    should_isolate,
)
from backend.services.atp_service import compute_atp
from backend.services.multi_objective import rank_orders
from backend.services.order_promising import promise_order


# ---------------------------------------------------------------------------
# Special control rules
# ---------------------------------------------------------------------------


def test_special_control_level_zero_is_isolated():
    rules = compute_special_control_rules(
        [{"id": "MAT-X", "special_control_level": 0}]
    )
    assert rules["MAT-X"]["isolated"] is True
    assert should_isolate(rules["MAT-X"]) is True
    assert adjust_atp(1000.0, rules["MAT-X"]) == 0.0


def test_special_control_level_one_deducts_twenty_percent():
    rules = compute_special_control_rules(
        [{"id": "MAT-Y", "special_control_level": 1}]
    )
    adjusted = adjust_atp(1000.0, rules["MAT-Y"])
    assert adjusted == pytest.approx(800.0, rel=1e-6)


def test_special_control_level_two_deducts_ten_percent():
    rules = compute_special_control_rules(
        [{"id": "MAT-Z", "special_control_level": 2}]
    )
    adjusted = adjust_atp(1000.0, rules["MAT-Z"])
    assert adjusted == pytest.approx(900.0, rel=1e-6)


def test_special_control_overrides_take_precedence():
    rules = compute_special_control_rules(
        [{"id": "MAT-W", "special_control_level": 1}],
        overrides={"MAT-W": {"buffer": 0.5}},
    )
    adjusted = adjust_atp(1000.0, rules["MAT-W"])
    assert adjusted == pytest.approx(500.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Critical material rules
# ---------------------------------------------------------------------------


def test_single_source_material_gets_eighty_percent_atp():
    rules = compute_critical_material_rules(
        [{"id": "MAT-S", "supply_risk": "single", "lead_time_bucket": "mid"}]
    )
    adjusted = apply_critical_material_rules("MAT-S", 1000.0, rules["MAT-S"])
    assert adjusted == pytest.approx(800.0, rel=1e-6)


def test_dual_source_material_gets_ninety_five_percent_atp():
    rules = compute_critical_material_rules(
        [{"id": "MAT-D", "supply_risk": "dual"}]
    )
    adjusted = apply_critical_material_rules("MAT-D", 1000.0, rules["MAT-D"])
    assert adjusted == pytest.approx(950.0, rel=1e-6)


def test_multi_source_material_is_unchanged():
    rules = compute_critical_material_rules(
        [{"id": "MAT-M", "supply_risk": "multiple"}]
    )
    adjusted = apply_critical_material_rules("MAT-M", 1000.0, rules["MAT-M"])
    assert adjusted == 1000.0


# ---------------------------------------------------------------------------
# ATP core algorithm
# ---------------------------------------------------------------------------


def _materials():
    return [
        {
            "id": "MAT-A",
            "name": "Product A",
            "category": "finished",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 100,
        },
        {
            "id": "MAT-B",
            "name": "Toxic intermediate",
            "category": "intermediate",
            "unit": "kg",
            "special_control_level": 0,
            "supply_risk": "single",
            "lead_time_bucket": "long",
            "lead_time_days": 90,
            "safety_stock": 30,
        },
        {
            "id": "MAT-C",
            "name": "Regulated material",
            "category": "raw_material",
            "unit": "kg",
            "special_control_level": 1,
            "supply_risk": "dual",
            "lead_time_bucket": "mid",
            "lead_time_days": 30,
            "safety_stock": 50,
        },
    ]


def test_isolated_material_returns_zero_atp():
    result = compute_atp(_materials(), open_orders=[])
    mat_b = next(row for row in result["matrix"] if row["material_id"] == "MAT-B")
    assert mat_b["isolated"] is True
    assert mat_b["series"] == []


def test_regulated_material_atp_deducts_twenty_percent():
    # MAT-C has sc_level=1 (20% buffer) AND supply_risk=dual (5% buffer), so total = 1000 * 0.8 * 0.95 = 760
    result = compute_atp(_materials(), inventory=[{"material_id": "MAT-C", "quantity": 1000}])
    mat_c = next(row for row in result["matrix"] if row["material_id"] == "MAT-C")
    first_atp = mat_c["series"][0]["atp"]
    assert first_atp == pytest.approx(760.0, rel=1e-6)


def test_orders_sorted_by_customer_tier_premium_first():
    base = date(2026, 7, 2)
    orders = [
        {
            "id": "ORD-base",
            "customer_id": "C-B",
            "customer_tier": "base",
            "material_id": "MAT-A",
            "quantity": 100,
            "creation_date": base,
            "due_date": base + timedelta(days=2),
        },
        {
            "id": "ORD-prem",
            "customer_id": "C-P",
            "customer_tier": "premium",
            "material_id": "MAT-A",
            "quantity": 100,
            "creation_date": base,
            "due_date": base + timedelta(days=10),
        },
        {
            "id": "ORD-comf",
            "customer_id": "C-C",
            "customer_tier": "comfort",
            "material_id": "MAT-A",
            "quantity": 100,
            "creation_date": base,
            "due_date": base + timedelta(days=5),
        },
    ]
    result = compute_atp(
        [_m for _m in _materials() if _m["id"] == "MAT-A"],
        inventory=[{"material_id": "MAT-A", "quantity": 250}],
        open_orders=orders,
        base_date=base,
        horizon_days=5,
    )
    series = sorted(
        [r for r in result["matrix"][0]["series"]],
        key=lambda e: e["date"],
    )
    # Day 0: 250 raw - 10% sc buffer = 225 available; premium (100) fulfilled -> 125
    assert series[0]["atp"] == pytest.approx(125.0, rel=1e-6)
    # OTS should reflect that premium gets first dibs
    assert result["ots"]["premium"] == pytest.approx(100.0, rel=1e-6)


def test_alerts_include_shortage():
    base = date(2026, 7, 2)
    orders = [
        {
            "id": "ORD-big",
            "customer_id": "C1",
            "customer_tier": "premium",
            "material_id": "MAT-A",
            "quantity": 5000,
            "creation_date": base,
            "due_date": base + timedelta(days=1),
        }
    ]
    result = compute_atp(
        [_m for _m in _materials() if _m["id"] == "MAT-A"],
        inventory=[{"material_id": "MAT-A", "quantity": 100}],
        open_orders=orders,
        base_date=base,
        horizon_days=5,
    )
    # Shortage is recorded as a per-order shortfall alert (severity=info) and/or
    # as a low ATP value (< safety_stock).
    assert any(a.get("severity") == "info" for a in result["alerts"])
    assert any(s["atp"] < 100 for s in result["matrix"][0]["series"])  # safety_stock=100, will go below


def test_long_lead_time_material_horizon_extends():
    base = date(2026, 7, 2)
    result = compute_atp(
        [_m for _m in _materials() if _m["id"] == "MAT-C"],
        inventory=[{"material_id": "MAT-C", "quantity": 200}],
        open_orders=[],
        base_date=base,
        horizon_days=10,
    )
    # MAT-C is lead_time_bucket=mid, so horizon should be at least 30+7
    assert len(result["matrix"][0]["series"]) >= 30


def test_ots_metrics_are_returned():
    base = date(2026, 7, 2)
    orders = [
        {
            "id": "ORD-1",
            "customer_id": "C1",
            "customer_tier": "premium",
            "material_id": "MAT-A",
            "quantity": 50,
            "creation_date": base,
            "due_date": base + timedelta(days=2),
        }
    ]
    result = compute_atp(
        [_m for _m in _materials() if _m["id"] == "MAT-A"],
        inventory=[{"material_id": "MAT-A", "quantity": 200}],
        open_orders=orders,
        base_date=base,
    )
    assert "premium" in result["ots"]
    assert "overall" in result["ots"]


def test_compute_atp_under_one_second_for_hundred_materials():
    base = date(2026, 7, 2)
    materials = [
        {
            "id": f"MAT-{i:03d}",
            "name": f"M{i}",
            "category": "finished",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 50,
        }
        for i in range(100)
    ]
    orders = [
        {
            "id": f"ORD-{i:03d}",
            "customer_id": f"C-{i}",
            "customer_tier": ["premium", "comfort", "base"][i % 3],
            "material_id": f"MAT-{i % 30:03d}",
            "quantity": 50,
            "creation_date": base,
            "due_date": base + timedelta(days=5),
        }
        for i in range(100)
    ]
    inventory = [{"material_id": f"MAT-{i:03d}", "quantity": 500} for i in range(30)]
    started = time.perf_counter()
    compute_atp(materials, inventory=inventory, open_orders=orders, base_date=base, horizon_days=30)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Order promising placeholder
# ---------------------------------------------------------------------------


def test_promise_order_returns_earliest_fulfilment_date():
    base = date.today()  # The public service plans from today.
    materials = [
        {
            "id": "MAT-A",
            "category": "finished",
            "unit": "kg",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_bucket": "short",
            "lead_time_days": 7,
            "safety_stock": 0,
        }
    ]
    inventory = [{"material_id": "MAT-A", "quantity": 100}]
    inbound = [{"material_id": "MAT-A", "quantity": 200, "expected_date": base + timedelta(days=2)}]
    result = promise_order(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=[],
        request={"material_id": "MAT-A", "quantity": 150, "desired_date": (base + timedelta(days=2)).isoformat()},
        horizon_days=5,
    )
    assert result["can_promise"] is True
    assert result["promised_date"] is not None
    assert result["status"] == "ok"
    # Inventory 100, inbound 200 day 2, want 150 by day 2 -> earliest is day 2 (inbound lands then).
    assert result["promised_date"] == (base + timedelta(days=2)).isoformat()
    assert result["on_time"] is True
    assert result["alternates"] == []


def test_promise_order_rejects_bad_input():
    with pytest.raises(ValueError):
        promise_order(
            materials=[],
            inventory=[],
            inbound=[],
            open_orders=[],
            request={"material_id": "", "quantity": 0},
        )


# ---------------------------------------------------------------------------
# Multi-objective weighted sum ranking
# ---------------------------------------------------------------------------


def test_rank_orders_premium_first():
    base = date(2026, 7, 2)
    materials = [
        {"id": "MAT-A", "category": "finished", "special_control_level": 2, "supply_risk": "multiple", "lead_time_bucket": "short", "lead_time_days": 7, "safety_stock": 0, "unit": "kg"}
    ]
    open_orders = [
        {"id": "ORD-base", "customer_id": "C1", "customer_tier": "base", "material_id": "MAT-A", "quantity": 50, "creation_date": base, "due_date": base + timedelta(days=5)},
        {"id": "ORD-prem", "customer_id": "C2", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 50, "creation_date": base, "due_date": base + timedelta(days=5)},
    ]
    result = rank_orders(
        materials=materials,
        inventory=[{"material_id": "MAT-A", "quantity": 100}],
        inbound=[],
        open_orders=open_orders,
        priorities=[{"customer_tier": "premium", "material_category": "finished", "weight": 100}],
        horizon_days=10,
    )
    assert result["status"] == "ok"
    assert result["ranking"][0]["order_id"] == "ORD-prem"
    assert result["ranking"][0]["score"] >= result["ranking"][1]["score"]


def test_rank_orders_unknown_strategy_returns_not_implemented():
    base = date(2026, 7, 2)
    result = rank_orders(
        materials=[],
        inventory=[],
        inbound=[],
        open_orders=[],
        priorities=[],
        strategy="lexicographic",
    )
    # MVP-2 implements lexicographic, so empty data still returns ok.
    assert result["status"] == "ok"
    assert result["strategy"] == "lexicographic"
    assert result["ranking"] == []
    result2 = rank_orders(
        materials=[],
        inventory=[],
        inbound=[],
        open_orders=[],
        priorities=[],
        strategy="nonsense",
    )
    assert result2["status"] == "not_implemented"

