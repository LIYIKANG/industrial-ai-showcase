"""Unit tests for the chemical ontology and the chemical file parser."""

from __future__ import annotations

from datetime import date, timedelta

from backend.core.chem_file_parser import (
    parse_inbound,
    parse_inventory,
    parse_materials,
    parse_open_orders,
    parse_priorities,
)
from backend.core.chemical_ontology import (
    LEAD_TIME_BUCKETS,
    atp_horizon,
    build_chemical_ontology,
    classify_material,
    expand_horizon,
)


SAMPLE_MATERIALS = [
    {
        "id": "MAT-001",
        "name": "Raw A",
        "category": "raw_material",
        "unit": "kg",
        "special_control_level": 2,
        "supply_risk": "multiple",
        "lead_time_bucket": "short",
        "lead_time_days": 7,
        "price_sensitive": False,
        "safety_stock": 100,
    },
    {
        "id": "MAT-002",
        "name": "Toxic reagent",
        "category": "raw_material",
        "unit": "kg",
        "special_control_level": 0,
        "supply_risk": "single",
        "lead_time_bucket": "long",
        "lead_time_days": 120,
        "price_sensitive": True,
        "safety_stock": 30,
        "supplier_id": "SUP-D",
    },
    {
        "id": "MAT-003",
        "name": "Finished product",
        "category": "finished",
        "unit": "kg",
        "special_control_level": 1,
        "supply_risk": "dual",
        "lead_time_bucket": "mid",
        "lead_time_days": 30,
        "price_sensitive": False,
        "safety_stock": 200,
        "parent_material_id": "MAT-001",
    },
]


def test_classify_material_returns_normalised_view():
    view = classify_material(SAMPLE_MATERIALS[0])
    assert view["id"] == "MAT-001"
    assert view["category"] == "raw_material"
    assert view["special_control_level"] == 2
    assert view["lead_time_days"] == 7


def test_atp_horizon_for_long_lead_time_material_extends():
    horizon = atp_horizon(SAMPLE_MATERIALS[1], base_horizon_days=30)
    # long lead time: 120 + 7 = 127
    assert horizon == 127


def test_expand_horizon_returns_daily_dates():
    base = date(2026, 7, 2)
    days = expand_horizon(base, SAMPLE_MATERIALS, base_horizon_days=30)
    assert days[0] == base
    assert (days[-1] - base).days >= 127


def test_build_chemical_ontology_emits_entities_and_relationships():
    tanks = [{"id": "TANK-01", "name": "Tank 1", "capacity": 5000, "unit": "kg", "material_id": "MAT-001"}]
    customers = [{"id": "CUST-001", "name": "ACME", "tier": "premium"}]
    orders = [
        {
            "id": "ORD-001",
            "customer_id": "CUST-001",
            "material_id": "MAT-001",
            "quantity": 100,
            "due_date": "2026-08-01",
            "unit": "kg",
        }
    ]
    suppliers = [{"id": "SUP-D", "name": "Dangerous Supplier", "risk_level": "high"}]
    onto = build_chemical_ontology(SAMPLE_MATERIALS, tanks, customers, orders, suppliers)
    assert any(e["id"] == "MAT-001" and e["type"] == "化工物料" for e in onto["entities"])
    assert any(e["id"] == "TANK-01" and e["type"] == "储罐/装置" for e in onto["entities"])
    assert any(e["id"] == "CUST-001" and e["type"] == "客户" for e in onto["entities"])
    assert any(e["id"] == "ORD-001" and e["type"] == "订单" for e in onto["entities"])
    assert any(r["source"] == "MAT-002" and r["target"] == "SUP-D" and r["relation"] == "采购自" for r in onto["relationships"])
    assert any(r["source"] == "TANK-01" and r["target"] == "MAT-001" and r["relation"] == "储存" for r in onto["relationships"])
    assert any(r["source"] == "ORD-001" and r["target"] == "CUST-001" for r in onto["relationships"])


def test_parse_materials_handles_csv_with_missing_columns():
    csv = b"id,name,category,unit\nMAT-X,Y,raw_material,kg\n"
    rows = parse_materials("materials.csv", csv)
    assert rows[0]["id"] == "MAT-X"
    assert rows[0]["special_control_level"] == 2  # default


def test_parse_inventory_skips_rows_without_id():
    csv = b"material_id,quantity\nMAT-A,100\n,50\n"
    rows = parse_inventory("inventory.csv", csv)
    assert len(rows) == 1
    assert rows[0]["material_id"] == "MAT-A"


def test_parse_inbound_reads_dates_as_strings():
    csv = b"id,material_id,quantity,expected_date,supplier\nINB-1,MAT-A,100,2026-08-01,SUP-A\n"
    rows = parse_inbound("inbound.csv", csv)
    assert rows[0]["expected_date"] == "2026-08-01"


def test_parse_open_orders_includes_customer_tier():
    csv = b"id,customer_id,customer_tier,material_id,quantity,creation_date,due_date\nORD-1,C-1,premium,MAT-A,50,2026-07-01,2026-07-15\n"
    rows = parse_open_orders("open_orders.csv", csv)
    assert rows[0]["customer_tier"] == "premium"


def test_parse_priorities_returns_three_column_rows():
    csv = b"customer_tier,material_category,weight\npremium,finished,100\nbase,raw_material,5\n"
    rows = parse_priorities("priorities.csv", csv)
    assert len(rows) == 2
    assert rows[0]["weight"] == 100


def test_lead_time_lookup_table_is_consistent():
    assert LEAD_TIME_BUCKETS["short"] == 7
    assert LEAD_TIME_BUCKETS["mid"] == 30
    assert LEAD_TIME_BUCKETS["long"] == 90
