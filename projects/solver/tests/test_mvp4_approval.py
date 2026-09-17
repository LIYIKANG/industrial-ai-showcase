# -*- coding: utf-8 -*-
"""MVP-4: tests for the special control approval workflow."""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.core.project_store import ProjectStore
from backend.services.approval import (
    STATE_APPROVED,
    STATE_PENDING,
    STATE_REJECTED,
    ApprovalService,
)
from backend.services.multi_objective import allocate_under_gap


@pytest.fixture()
def store_path():
    tmp = Path(tempfile.mkdtemp(prefix="mvp4-store-"))
    yield tmp / "store.sqlite"
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def store(store_path):
    return ProjectStore(store_path)


@pytest.fixture()
def materials():
    return [
        {
            "id": "MAT-TOX",
            "name": "易制毒原料",
            "category": "raw_material",
            "special_control_level": 0,
            "special_control_subtype": "drug_precursor",
            "supply_risk": "single",
            "lead_time_days": 90,
            "safety_stock": 0,
            "lead_time_bucket": "long",
        },
        {
            "id": "MAT-EXP",
            "name": "易制爆",
            "category": "raw_material",
            "special_control_level": 0,
            "special_control_subtype": "explosive_precursor",
            "supply_risk": "single",
            "lead_time_days": 90,
            "safety_stock": 0,
            "lead_time_bucket": "long",
        },
        {
            "id": "MAT-FIN",
            "name": "成品",
            "category": "finished",
            "special_control_level": 2,
            "supply_risk": "multiple",
            "lead_time_days": 7,
            "safety_stock": 0,
            "lead_time_bucket": "short",
        },
    ]


def _seed_chem_materials(store: ProjectStore, project_id: str, materials: list[dict]) -> None:
    store.save_chem_dataset(project_id=project_id, materials=materials)


# ---------------------------------------------------------------------------
# ApprovalService unit tests
# ---------------------------------------------------------------------------


def test_submit_creates_pending_request(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    req = svc.submit(
        project_id="demo",
        order_id="O-TOX-1",
        material_id="MAT-TOX",
        quantity=10,
        materials=materials,
        requested_by="alice",
    )
    assert req["state"] == STATE_PENDING
    assert req["subtype"] == "drug_precursor"
    assert req["approver_role"] == "chief_safety_officer"


def test_decide_approved_by_correct_role(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    req = svc.submit(
        project_id="demo",
        order_id="O-TOX-2",
        material_id="MAT-TOX",
        quantity=5,
        materials=materials,
    )
    out = svc.decide(
        request_id=req["id"],
        approver="bob",
        approver_role="chief_safety_officer",
        decision=STATE_APPROVED,
        note="safety review ok",
    )
    assert out["state"] == STATE_APPROVED
    assert out["decided_by"] == "bob"
    assert out["decision_note"] == "safety review ok"
    assert out["decided_at"]


def test_decide_wrong_role_rejected(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    req = svc.submit(
        project_id="demo",
        order_id="O-TOX-3",
        material_id="MAT-TOX",
        quantity=5,
        materials=materials,
    )
    with pytest.raises(PermissionError):
        svc.decide(
            request_id=req["id"],
            approver="carol",
            approver_role="planner",
            decision=STATE_APPROVED,
        )
    # Still pending after the failed attempt
    again = svc.get(req["id"])
    assert again["state"] == STATE_PENDING


def test_decide_rejected_path(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    req = svc.submit(
        project_id="demo",
        order_id="O-EXP-1",
        material_id="MAT-EXP",
        quantity=8,
        materials=materials,
    )
    out = svc.decide(
        request_id=req["id"],
        approver="dave",
        approver_role="plant_manager",
        decision=STATE_REJECTED,
        note="quota exceeded",
    )
    assert out["state"] == STATE_REJECTED
    assert out["decision_note"] == "quota exceeded"


def test_decide_terminal_state_cannot_be_redecided(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    req = svc.submit(
        project_id="demo",
        order_id="O-EXP-2",
        material_id="MAT-EXP",
        quantity=8,
        materials=materials,
    )
    svc.decide(
        request_id=req["id"],
        approver="dave",
        approver_role="plant_manager",
        decision=STATE_REJECTED,
    )
    with pytest.raises(ValueError):
        svc.decide(
            request_id=req["id"],
            approver="dave",
            approver_role="plant_manager",
            decision=STATE_APPROVED,
        )


def test_list_pending_and_history(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    a = svc.submit(
        project_id="demo", order_id="A", material_id="MAT-TOX",
        quantity=1, materials=materials,
    )
    b = svc.submit(
        project_id="demo", order_id="B", material_id="MAT-EXP",
        quantity=1, materials=materials,
    )
    svc.decide(
        request_id=b["id"], approver="d", approver_role="plant_manager",
        decision=STATE_APPROVED,
    )
    pending = svc.list_pending("demo")
    assert {p["order_id"] for p in pending} == {"A"}
    history = svc.list_history("demo")
    assert {h["order_id"] for h in history} == {"A", "B"}


def test_submit_non_isolated_material_raises(store, materials):
    _seed_chem_materials(store, "demo", materials)
    svc = ApprovalService(store)
    with pytest.raises(ValueError):
        svc.submit(
            project_id="demo",
            order_id="O-OK",
            material_id="MAT-FIN",
            quantity=10,
            materials=materials,
        )


# ---------------------------------------------------------------------------
# Allocator integration: approval releases the order
# ---------------------------------------------------------------------------


def test_allocator_blocks_unapproved_toxic_order(store, materials):
    _seed_chem_materials(store, "demo", materials)
    base = date(2026, 7, 2)
    inventory = [{"material_id": "MAT-TOX", "quantity": 100}]
    open_orders = [
        {
            "id": "O-TOX-100",
            "customer_id": "C1",
            "customer_tier": "premium",
            "material_id": "MAT-TOX",
            "quantity": 10,
            "creation_date": base,
            "due_date": base + timedelta(days=2),
        }
    ]
    # No approval yet -> blocked
    r = allocate_under_gap(
        materials=materials, inventory=inventory, inbound=[],
        open_orders=open_orders, strategy="lexicographic", horizon_days=10,
    )
    assert r["summary"]["promised"] == 0
    assert r["summary"]["special_control_blocked"] == 1
    # Approve and re-allocate
    svc = ApprovalService(store)
    svc.submit(
        project_id="demo", order_id="O-TOX-100", material_id="MAT-TOX",
        quantity=10, materials=materials,
    )
    # Need to seed the open order in store too so the approve() path can read
    # project context. (We re-submit after seeding open_orders.)
    # The approval service does not depend on open_orders table; the allocator
    # helper approved_order_ids() looks them up. Submit again so we have a
    # pending request, then decide.
    pending = svc.list_pending("demo")
    assert pending and pending[0]["order_id"] == "O-TOX-100"
    svc.decide(
        request_id=pending[0]["id"], approver="bob",
        approver_role="chief_safety_officer", decision=STATE_APPROVED,
    )
    # We need the allocator to know which orders are approved. The API
    # endpoint at /api/atp/rank/allocate takes an ``approved_order_ids``
    # field for that. Here we simulate the same call path with a
    # pre-marked order.
    open_orders[0]["approved"] = True
    r2 = allocate_under_gap(
        materials=materials, inventory=inventory, inbound=[],
        open_orders=open_orders, strategy="lexicographic", horizon_days=10,
    )
    assert r2["summary"]["promised"] == 1
    assert r2["summary"]["special_control_blocked"] == 0