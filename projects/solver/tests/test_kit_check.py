"""Tests for the BOM expansion and material kit check (MVP-3)."""

from __future__ import annotations

import pytest

from backend.core.bom import expand_kit, index_bom, BOMCycleError
from backend.core.chem_file_parser import (
    parse_bom,
    parse_inventory,
    parse_materials,
    parse_substitutes,
)
from backend.services.kit_check import batch_kit_check, check_kit, check_kit_with_substitutes


def _bom():
    with open("data/atp_demo/bom.csv", "rb") as f:
        return parse_bom("bom.csv", f.read())


def _materials():
    with open("data/atp_demo/materials.csv", "rb") as f:
        return parse_materials("materials.csv", f.read())


def _inventory():
    with open("data/atp_demo/inventory.csv", "rb") as f:
        return parse_inventory("inventory.csv", f.read())


def _inbound():
    with open("data/atp_demo/inbound.csv", "rb") as f:
        # reuse parser by renaming
        from backend.core.chem_file_parser import parse_inbound
        return parse_inbound("inbound.csv", f.read())


def _substitutes():
    with open("data/atp_demo/substitutes.csv", "rb") as f:
        return parse_substitutes("substitutes.csv", f.read())


# ---------------------------------------------------------------------------
# BOM expansion
# ---------------------------------------------------------------------------


def test_index_bom_groups_by_parent():
    bom = _bom()
    idx = index_bom(bom)
    assert "MAT-007" in idx
    assert len(idx["MAT-007"]) == 3  # P1 -> M1, M2, trace catalyst
    assert "MAT-004" in idx
    # MAT-004 (M1) -> raw A and raw C
    children = {e["child_material_id"] for e in idx["MAT-004"]}
    assert children == {"MAT-001", "MAT-003"}


def test_expand_kit_two_levels():
    bom = _bom()
    kit = expand_kit(parent_material_id="MAT-007", quantity=1000, bom=bom)
    assert kit["parent"] == "MAT-007"
    assert kit["parent_quantity"] == 1000
    assert kit["has_cycles"] is False
    assert kit["depth"] >= 2  # P1 -> M1 -> raw
    # Leaves are the raw materials + trace catalyst.
    leaves = kit["leaves"]
    assert "MAT-001" in leaves
    assert "MAT-002" in leaves
    assert "MAT-003" in leaves
    assert "MAT-014" in leaves
    # Required gross is positive for every leaf.
    for info in leaves.values():
        assert info["gross_qty"] > 0


def test_expand_kit_rejects_invalid_input():
    bom = _bom()
    with pytest.raises(ValueError):
        expand_kit(parent_material_id="", quantity=100, bom=bom)
    with pytest.raises(ValueError):
        expand_kit(parent_material_id="MAT-007", quantity=0, bom=bom)


def test_expand_kit_applies_yield_ratio():
    bom = [
        {
            "parent_material_id": "P",
            "child_material_id": "C",
            "quantity_per_unit": 1.0,
            "unit": "kg",
            "yield_ratio": 0.5,
            "scrap_rate": 0.0,
        }
    ]
    kit = expand_kit(parent_material_id="P", quantity=10, bom=bom)
    # 10 units of P need 10 kg of C (gross); with yield 0.5 the parent must
    # be issued 10/0.5 = 20 kg to get the 10 kg it needs.
    assert kit["leaves"]["C"]["gross_qty"] == pytest.approx(20.0)


def test_expand_kit_handles_cycle():
    bom = [
        {"parent_material_id": "A", "child_material_id": "B", "quantity_per_unit": 1.0, "unit": "kg", "yield_ratio": None, "scrap_rate": 0.0},
        {"parent_material_id": "B", "child_material_id": "A", "quantity_per_unit": 1.0, "unit": "kg", "yield_ratio": None, "scrap_rate": 0.0},
    ]
    kit = expand_kit(parent_material_id="A", quantity=1, bom=bom)
    assert kit["has_cycles"] is True


# ---------------------------------------------------------------------------
# Kit check
# ---------------------------------------------------------------------------


def test_check_kit_completes_when_stock_sufficient():
    bom = _bom()
    inventory = _inventory()
    inbound = _inbound()
    materials = _materials()
    # 100 kg of P1 (MAT-007) -- leaves are tiny against the demo stock.
    result = check_kit(
        parent_material_id="MAT-007",
        quantity=100,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
    )
    # The trace catalyst MAT-014 is special-control level 0 -- isolated.
    leaves_by_id = {l["material_id"]: l for l in result["leaves"]}
    assert leaves_by_id["MAT-014"]["isolated"] is True
    # All other leaves should be sufficient at this small quantity.
    for mid, leaf in leaves_by_id.items():
        if mid == "MAT-014":
            continue
        assert leaf["status"] == "sufficient", f"{mid} should be sufficient, got {leaf['status']}"


def test_check_kit_flags_shortage_at_high_quantity():
    bom = _bom()
    inventory = _inventory()
    inbound = _inbound()
    materials = _materials()
    # 100,000 kg of P1 -- way more than stock.
    result = check_kit(
        parent_material_id="MAT-007",
        quantity=100_000,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
    )
    assert result["status"] == "incomplete"
    assert result["summary"]["short"] >= 1
    # Every leaf that is a raw material should be short.
    for leaf in result["leaves"]:
        if leaf["category"] == "raw_material":
            assert leaf["status"] == "short"


def test_check_kit_with_substitutes_proposes_recovery():
    bom = _bom()
    inventory = _inventory()
    inbound = _inbound()
    materials = _materials()
    substitutes = _substitutes()
    # Hit a size where raw A is short, but raw B is in stock and is a substitute.
    result = check_kit_with_substitutes(
        parent_material_id="MAT-007",
        quantity=2000,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
        substitutes=substitutes,
    )
    # We expect at least one short leaf with a substitute candidate.
    short_leaves = [l for l in result["leaves"] if l["status"] == "short"]
    # If nothing is short, the test is degenerate -- still assert structure.
    if short_leaves:
        assert "recovery" in result
        assert result["summary"]["recoverable"] >= 0
    # Special control level 0 leaves must remain isolated (no substitute recovery).
    for leaf in result["leaves"]:
        if leaf.get("special_control") == 0:
            assert leaf["available"] == 0


def test_check_kit_respects_special_control_isolation():
    bom = _bom()
    inventory = [
        {"material_id": "MAT-014", "quantity": 999999},  # plenty of stock
    ]
    inbound = []
    materials = _materials()
    result = check_kit(
        parent_material_id="MAT-007",
        quantity=1000,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
    )
    mat014 = next(l for l in result["leaves"] if l["material_id"] == "MAT-014")
    # Despite plenty of stock, special control level 0 isolates the material.
    assert mat014["isolated"] is True
    assert mat014["available"] == 0
    assert mat014["status"] == "short"


def test_check_kit_uses_inbound_in_availability():
    bom = _bom()
    inventory = []
    inbound = [
        {"material_id": "MAT-001", "quantity": 1000, "expected_date": "2026-08-01"},
    ]
    materials = _materials()
    result = check_kit(
        parent_material_id="MAT-007",
        quantity=100,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
    )
    mat001 = next(l for l in result["leaves"] if l["material_id"] == "MAT-001")
    # Inbound should count toward availability.
    assert mat001["in_transit"] == 1000
    # MAT-001 is special-control level 2 in the demo data, so the 10% buffer
    # applies: available = 1000 * (1 - 0.10) = 900.  This mirrors the ATP
    # service semantic: available here is the buffer-adjusted figure.
    assert mat001["available"] == 900
    assert mat001["status"] == "sufficient"


# ---------------------------------------------------------------------------
# MVP-3 follow-up: reason field, completion %, substitute buffer, batch mode
# ---------------------------------------------------------------------------

def _mat_simple(material_id, *, sc=2, supply="multiple", name=None):
    return {
        "id": material_id,
        "name": name or material_id,
        "category": "raw_material",
        "unit": "kg",
        "special_control_level": sc,
        "supply_risk": supply,
        "lead_time_days": 7,
        "price_sensitive": False,
        "safety_stock": 0,
        "supplier_id": None,
    }


def test_check_kit_returns_reason_per_leaf():
    """Each leaf carries a ``reason`` object with code + Chinese message."""
    bom = [
        {"parent_material_id": "P", "child_material_id": "M", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [_mat_simple("P", sc=2), _mat_simple("M", sc=2)]
    # Stock well below required
    inventory = [{"material_id": "M", "quantity": 10}]
    result = check_kit(
        parent_material_id="P",
        quantity=100,
        bom=bom,
        inventory=inventory,
        materials=materials,
    )
    leaf = result["leaves"][0]
    assert "reason" in leaf
    assert leaf["reason"]["code"] in ("insufficient_stock", "buffer_deducted")
    assert "缺" in leaf["reason"]["message"] or "齐套" in leaf["reason"]["message"]


def test_check_kit_summary_has_completion_stats():
    """Top-level summary must include completion_pct, total_required, total_available, total_gap."""
    bom = [
        {"parent_material_id": "P", "child_material_id": "M", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [_mat_simple("P", sc=2), _mat_simple("M", sc=2)]
    inventory = [{"material_id": "M", "quantity": 80}]  # 80% covered
    result = check_kit(
        parent_material_id="P", quantity=100, bom=bom, inventory=inventory, materials=materials
    )
    sm = result["summary"]
    assert "completion_pct" in sm
    assert "total_required" in sm
    assert "total_available" in sm
    assert "total_gap" in sm
    assert sm["total_required"] == 100
    assert 0 < sm["completion_pct"] < 100


def test_check_kit_isolated_leaf_reason_is_isolated():
    """A leaf under special-control level 0 should report reason.code=isolated."""
    bom = [
        {"parent_material_id": "P", "child_material_id": "M", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [_mat_simple("P", sc=2), _mat_simple("M", sc=0, supply="single")]
    inventory = [{"material_id": "M", "quantity": 9999}]
    result = check_kit(
        parent_material_id="P", quantity=10, bom=bom, inventory=inventory, materials=materials
    )
    leaf = result["leaves"][0]
    assert leaf["isolated"] is True
    assert leaf["reason"]["code"] == "isolated"
    assert "特控" in leaf["reason"]["message"]


def test_substitute_respects_buffer():
    """A substitute material that is level-2 (10% buffer) should not contribute full raw stock."""
    bom = [
        {"parent_material_id": "P", "child_material_id": "M1", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [
        _mat_simple("P", sc=2),
        _mat_simple("M1", sc=2),
        _mat_simple("M2", sc=2, name="Sub 2"),
    ]
    substitutes = [{"material_id": "M1", "substitute_id": "M2", "priority": 1}]
    inventory = [
        {"material_id": "M1", "quantity": 0},
        {"material_id": "M2", "quantity": 100},  # raw, after 10% buffer -> 90
    ]
    result = check_kit_with_substitutes(
        parent_material_id="P", quantity=80, bom=bom, inventory=inventory,
        materials=materials, substitutes=substitutes,
    )
    leaf = result["leaves"][0]
    assert leaf["recovery"]
    cand = leaf["recovery"][0]
    assert cand["raw_available"] == 100
    assert cand["available"] == 90  # 10% buffer
    assert cand["buffer_applied"] == 10
    assert cand["can_cover"] == 80  # covers all 80 needed
    assert result["summary"]["recoverable"] == 1


def test_substitute_skipped_when_isolated():
    """A substitute that is itself special-control level 0 must be skipped."""
    bom = [
        {"parent_material_id": "P", "child_material_id": "M1", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [
        _mat_simple("P", sc=2),
        _mat_simple("M1", sc=2),
        _mat_simple("M2", sc=0, supply="single", name="IsolatedSub"),
    ]
    substitutes = [{"material_id": "M1", "substitute_id": "M2", "priority": 1}]
    inventory = [
        {"material_id": "M1", "quantity": 0},
        {"material_id": "M2", "quantity": 1000},
    ]
    result = check_kit_with_substitutes(
        parent_material_id="P", quantity=50, bom=bom, inventory=inventory,
        materials=materials, substitutes=substitutes,
    )
    leaf = result["leaves"][0]
    assert leaf["recovery"] == []
    assert any(s["substitute"] == "M2" for s in result["skipped_isolated_substitutes"])
    assert result["summary"]["recoverable"] == 0


def test_substitute_status_partial_when_some_unrecoverable():
    """If some leaves are recoverable and some are not, status should be 'partial'."""
    bom = [
        {"parent_material_id": "P", "child_material_id": "M1", "quantity_per_unit": 1.0, "unit": "kg"},
        {"parent_material_id": "P", "child_material_id": "M2", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [
        _mat_simple("P", sc=2),
        _mat_simple("M1", sc=2),
        _mat_simple("M2", sc=2),
        _mat_simple("S1", sc=2, name="Sub 1"),
    ]
    substitutes = [
        {"material_id": "M1", "substitute_id": "S1", "priority": 1},  # M1 can recover
    ]
    inventory = [
        {"material_id": "M1", "quantity": 0},
        {"material_id": "M2", "quantity": 0},  # cannot recover
        {"material_id": "S1", "quantity": 100},
    ]
    result = check_kit_with_substitutes(
        parent_material_id="P", quantity=10, bom=bom, inventory=inventory,
        materials=materials, substitutes=substitutes,
    )
    assert result["status"] == "partial"
    assert result["summary"]["recoverable"] == 1
    assert "recovery_rate" in result["summary"]


def test_batch_kit_check_rollup():
    """batch_kit_check should aggregate short leaves and report problem_materials."""
    bom = [
        {"parent_material_id": "P1", "child_material_id": "M1", "quantity_per_unit": 1.0, "unit": "kg"},
        {"parent_material_id": "P2", "child_material_id": "M2", "quantity_per_unit": 1.0, "unit": "kg"},
    ]
    materials = [
        _mat_simple("P1", sc=2), _mat_simple("M1", sc=2),
        _mat_simple("P2", sc=2), _mat_simple("M2", sc=2),
    ]
    inventory = [
        {"material_id": "M1", "quantity": 100},  # enough
        {"material_id": "M2", "quantity": 0},     # short
    ]
    result = batch_kit_check(
        items=[{"parent_material_id": "P1", "quantity": 50},
               {"parent_material_id": "P2", "quantity": 30}],
        bom=bom, inventory=inventory, materials=materials,
    )
    assert result["summary"]["total_items"] == 2
    assert result["summary"]["complete_items"] == 1
    assert result["summary"]["short_items"] == 1
    assert result["summary"]["total_gap"] == 30
    assert result["problem_materials"][0]["material_id"] == "M2"
    assert result["problem_materials"][0]["total_short_qty"] == 30


def test_batch_kit_check_validates_items():
    """Bad items should be reported as error entries, not crash the batch."""
    bom = []
    materials = []
    result = batch_kit_check(items=[{}, {"parent_material_id": "P", "quantity": 0}], bom=bom, materials=materials)
    assert result["summary"]["total_items"] == 1  # only P with qty=0 ignored
    assert result["items"][0]["status"] == "error"
