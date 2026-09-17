# -*- coding: utf-8 -*-
"""Special control (特控) approval workflow (MVP-4).

When an order touches a level-0 material (剧毒 / 易制爆 / 易制毒), the
allocator must NOT release stock until a qualified approver signs off.
This module:

* Persists approval requests in the SQLite store
  (table ``special_control_approvals``)
* Enforces a small state machine: pending -> approved | rejected
* Validates that the approver's role matches the rule's
  ``approval_role`` (e.g. drug_precursor needs chief_safety_officer)
* Surfaces the decision back to :mod:`backend.services.multi_objective`
  so the allocator can flip an order's ``approved`` flag and re-allocate
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.core.special_control import compute_special_control_rules


STATE_PENDING = "pending"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
TERMINAL_STATES = {STATE_APPROVED, STATE_REJECTED}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_decision(rule: dict[str, Any], approver_role: str) -> tuple[bool, str]:
    expected = rule.get("approval_role", "planner")
    if approver_role == expected:
        return True, ""
    if expected == "chief_safety_officer" and approver_role in {"plant_manager", "safety_manager"}:
        # Allow safety_manager as a delegated approver for drug_precursor.
        return True, ""
    return False, f"role {approver_role!r} cannot approve {expected!r}"


class ApprovalService:
    """Thin wrapper over the SQLite project store."""

    def __init__(self, store) -> None:
        self._store = store
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._store._session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS special_control_approvals (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    material_id TEXT NOT NULL,
                    subtype TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    requested_by TEXT NOT NULL,
                    approver_role TEXT NOT NULL,
                    state TEXT NOT NULL,
                    decided_by TEXT,
                    decision_note TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                """
            )

    # -- public API --------------------------------------------------------

    def submit(
        self,
        *,
        project_id: str,
        order_id: str,
        material_id: str,
        quantity: float,
        materials: list[dict[str, Any]],
        requested_by: str = "planner",
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        rules = compute_special_control_rules(materials)
        rule = rules.get(material_id)
        if not rule or not rule.get("isolated"):
            raise ValueError(f"material {material_id} is not level-0 controlled")
        subtype = rule.get("subtype", "toxic")
        req_id = f"appr-{uuid.uuid4().hex[:8]}"
        created_at = _now_iso()
        with self._store._session() as conn:
            conn.execute(
                """INSERT INTO special_control_approvals
                   (id, project_id, order_id, material_id, subtype, quantity,
                    requested_by, approver_role, state, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    req_id,
                    project_id,
                    order_id,
                    material_id,
                    subtype,
                    float(quantity),
                    requested_by,
                    rule.get("approval_role", "planner"),
                    STATE_PENDING,
                    created_at,
                ),
            )
        return {
            "id": req_id,
            "project_id": project_id,
            "order_id": order_id,
            "material_id": material_id,
            "subtype": subtype,
            "subtype_label": rule.get("subtype_label", subtype),
            "quantity": float(quantity),
            "requested_by": requested_by,
            "approver_role": rule.get("approval_role", "planner"),
            "state": STATE_PENDING,
            "created_at": created_at,
        }

    def decide(
        self,
        *,
        request_id: str,
        approver: str,
        approver_role: str,
        decision: str,
        note: str = "",
        materials: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if decision not in (STATE_APPROVED, STATE_REJECTED):
            raise ValueError(f"decision must be {STATE_APPROVED} or {STATE_REJECTED}")
        with self._store._session() as conn:
            row = conn.execute(
                "SELECT * FROM special_control_approvals WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(request_id)
            if row["state"] in TERMINAL_STATES:
                raise ValueError(f"request {request_id} already {row['state']}")
            if materials is None:
                materials = _materials_for(conn, row["project_id"])
        from backend.core.special_control import compute_special_control_rules

        rules = compute_special_control_rules(materials)
        rule = rules.get(row["material_id"]) or {}
        ok, err = _validate_decision(rule, approver_role)
        if not ok and decision == STATE_APPROVED:
            raise PermissionError(err)
        decided_at = _now_iso()
        with self._store._session() as conn:
            conn.execute(
                """UPDATE special_control_approvals
                   SET state = ?, decided_by = ?, decision_note = ?, decided_at = ?
                   WHERE id = ?""",
                (decision, approver, note, decided_at, request_id),
            )
        return self.get(request_id)

    def get(self, request_id: str) -> dict[str, Any]:
        with self._store._session() as conn:
            row = conn.execute(
                "SELECT * FROM special_control_approvals WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        return _row_to_dict(row)

    def list_pending(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self._store._session() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM special_control_approvals WHERE state = ? AND project_id = ? ORDER BY created_at",
                    (STATE_PENDING, project_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM special_control_approvals WHERE state = ? ORDER BY created_at",
                    (STATE_PENDING,),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_history(self, project_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._store._session() as conn:
            if project_id:
                rows = conn.execute(
                    "SELECT * FROM special_control_approvals WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM special_control_approvals ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def approved_order_ids(self, project_id: str) -> set[str]:
        """Helper for the multi-objective allocator: which orders for this
        project have at least one approved request."""
        with self._store._session() as conn:
            rows = conn.execute(
                "SELECT DISTINCT order_id FROM special_control_approvals WHERE project_id = ? AND state = ?",
                (project_id, STATE_APPROVED),
            ).fetchall()
        return {r["order_id"] for r in rows}


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "order_id": row["order_id"],
        "material_id": row["material_id"],
        "subtype": row["subtype"],
        "quantity": row["quantity"],
        "requested_by": row["requested_by"],
        "approver_role": row["approver_role"],
        "state": row["state"],
        "decided_by": row["decided_by"],
        "decision_note": row["decision_note"] or "",
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
    }


def _materials_for(conn, project_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT payload FROM chem_materials WHERE project_id = ?", (project_id,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            out.append(json.loads(r["payload"]))
        except (TypeError, ValueError):
            continue
    return out