# -*- coding: utf-8 -*-
"""API-level tests for the MVP-3 rank/compare endpoints and the MVP-4
approval workflow endpoints."""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.main import app
    return TestClient(app)


def test_rank_allocate_endpoint(client):
    base = date(2026, 7, 2)
    payload = {
        "materials": [
            {
                "id": "MAT-A",
                "name": "A",
                "category": "finished",
                "special_control_level": 2,
                "supply_risk": "multiple",
                "lead_time_days": 7,
                "safety_stock": 0,
                "lead_time_bucket": "short",
            }
        ],
        "inventory": [{"material_id": "MAT-A", "quantity": 60}],
        "inbound": [],
        "open_orders": [
            {"id": "O1", "customer_id": "C1", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 30, "creation_date": base.isoformat(), "due_date": (base + timedelta(days=2)).isoformat()},
            {"id": "O2", "customer_id": "C2", "customer_tier": "base", "material_id": "MAT-A", "quantity": 30, "creation_date": base.isoformat(), "due_date": (base + timedelta(days=5)).isoformat()},
        ],
        "priorities": [],
        "strategy": "lexicographic",
        "horizon_days": 10,
    }
    r = client.post("/api/atp/rank/allocate", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["summary"]["promised"] == 1
    assert body["summary"]["short"] == 1
    # Premium should be the one promised
    promised = [d for d in body["allocation"] if d["allocation"] == "promised"]
    assert promised[0]["customer_tier"] == "premium"


def test_rank_compare_endpoint(client):
    base = date(2026, 7, 2)
    payload = {
        "materials": [
            {
                "id": "MAT-A",
                "category": "finished",
                "special_control_level": 2,
                "supply_risk": "multiple",
                "lead_time_days": 7,
                "safety_stock": 0,
                "lead_time_bucket": "short",
            }
        ],
        "inventory": [{"material_id": "MAT-A", "quantity": 60}],
        "inbound": [],
        "open_orders": [
            {"id": "O1", "customer_id": "C1", "customer_tier": "premium", "material_id": "MAT-A", "quantity": 30, "due_date": (base + timedelta(days=2)).isoformat()},
            {"id": "O2", "customer_id": "C2", "customer_tier": "base", "material_id": "MAT-A", "quantity": 30, "due_date": (base + timedelta(days=5)).isoformat()},
        ],
        "priorities": [],
        "strategy": "weighted_sum",
        "horizon_days": 10,
    }
    r = client.post("/api/atp/rank/compare", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    strategies = {row["strategy"] for row in body["strategies"]}
    assert strategies == {"weighted_sum", "lexicographic", "epsilon_constraint", "pareto"}


def test_approval_submit_and_decide(client):
    import uuid as _uuid
    project_id = "test-project-" + _uuid.uuid4().hex[:8]
    payload = {
        "project_id": project_id,
        "order_id": "O-TOX-1",
        "material_id": "MAT-TOX",
        "quantity": 10,
        "materials": [
            {
                "id": "MAT-TOX",
                "category": "raw_material",
                "special_control_level": 0,
                "special_control_subtype": "drug_precursor",
                "lead_time_days": 90,
                "supply_risk": "single",
            }
        ],
        "requested_by": "alice",
    }
    r = client.post("/api/atp/approval/submit", json=payload)
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["state"] == "pending"

    # Wrong role -> 403
    bad = client.post(
        "/api/atp/approval/decide",
        json={
            "request_id": req["id"],
            "approver": "carol",
            "approver_role": "planner",
            "decision": "approved",
            "materials": payload["materials"],
        },
    )
    assert bad.status_code == 403

    # Correct role -> approved
    ok = client.post(
        "/api/atp/approval/decide",
        json={
            "request_id": req["id"],
            "approver": "bob",
            "approver_role": "chief_safety_officer",
            "decision": "approved",
            "note": "ok",
            "materials": payload["materials"],
        },
    )
    assert ok.status_code == 200, ok.text
    decided = ok.json()
    assert decided["state"] == "approved"

    # Pending list should now be empty
    pend = client.get("/api/atp/approval/pending", params={"project_id": project_id}).json()
    assert pend["items"] == []
    # History should show our decision
    hist = client.get("/api/atp/approval/history", params={"project_id": project_id}).json()
    assert any(h["id"] == req["id"] and h["state"] == "approved" for h in hist["items"])


def test_approval_submit_non_isolated_400(client):
    r = client.post(
        "/api/atp/approval/submit",
        json={
            "project_id": "test-project",
            "order_id": "O-OK",
            "material_id": "MAT-OK",
            "quantity": 10,
            "materials": [
                {
                    "id": "MAT-OK",
                    "category": "raw_material",
                    "special_control_level": 2,
                }
            ],
        },
    )
    assert r.status_code == 400