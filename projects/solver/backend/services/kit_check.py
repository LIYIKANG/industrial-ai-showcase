"""Material kit check (物料齐套性检查) service.

Given a finished product order, expand the BOM, compare gross
requirements against on-hand inventory and in-transit inbound, and
report per-leaf ``sufficient / short / surplus`` status plus a tree
view that the UI can render directly.

The check has two flavours:

*   :func:`check_kit` returns a *strict* report: every leaf must be
    fully covered for the kit to be marked complete.
*   :func:`check_kit_with_substitutes` additionally walks the
    ``substitutes`` table to flag leaves that can be partially or
    fully covered by an alternate material (a *recovery plan*).

The service is pure: it consumes the BOM, inventory, inbound and
substitutes inputs (the same shape the project store and the
``/api/atp/import`` route use) and returns a JSON-serialisable dict.
"""

from __future__ import annotations

from typing import Any

from backend.core.bom import _mid, expand_kit
from backend.core.special_control import adjust_atp, should_isolate
from backend.services.atp_service import compute_special_control_rules


def _shortage_reason(required: float, available: float, sc_rule: dict[str, Any] | None, isolated: bool) -> dict[str, str]:
    """Return a machine-readable + Chinese reason for a leaf's availability gap.

    ``code`` is one of: ``isolated`` | ``insufficient_stock`` | ``ok``
    ``message`` is a short Chinese description suitable for the UI.
    """
    if required <= 0:
        return {"code": "ok", "message": "无需补料"}
    if isolated:
        level = (sc_rule or {}).get("level", 0)
        label = (sc_rule or {}).get("label") or "特控隔离"
        return {"code": "isolated", "message": f"特控等级 {level}（{label}），库存不参与普通订单分配"}
    if available <= 0:
        return {"code": "insufficient_stock", "message": "库存 + 在途 均为 0"}
    gap = required - available
    if gap > 0:
        if sc_rule and sc_rule.get("buffer", 0) > 0:
            buf_pct = int(round(sc_rule["buffer"] * 100))
            return {
                "code": "buffer_deducted",
                "message": f"特控扣减 {buf_pct}% 后仍缺 {round(gap, 4)}",
            }
        return {"code": "insufficient_stock", "message": f"库存 + 在途 不足，缺 {round(gap, 4)}"}
    return {"code": "ok", "message": "齐套"}


def _compute_completion(total_required: float, total_gap: float) -> float:
    """Return the kit completion percentage (0-100)."""
    if total_required <= 0:
        return 100.0
    covered = max(0.0, total_required - total_gap)
    return round(min(100.0, covered / total_required * 100.0), 2)


def _inventory_lookup(inventory: list[dict[str, Any]] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in inventory or []:
        mid = str(row.get("material_id") or "").strip()
        if not mid:
            continue
        try:
            qty = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        out[mid] = out.get(mid, 0.0) + qty
    return out


def _inbound_lookup(inbound: list[dict[str, Any]] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in inbound or []:
        mid = str(row.get("material_id") or "").strip()
        if not mid:
            continue
        try:
            qty = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        out[mid] = out.get(mid, 0.0) + qty
    return out


def _substitute_index(substitutes: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in substitutes or []:
        mid = str(row.get("material_id") or "").strip()
        sid = str(row.get("substitute_id") or "").strip()
        if not mid or not sid or mid == sid:
            continue
        out.setdefault(mid, []).append(
            {
                "substitute_id": sid,
                "priority": int(row.get("priority") or 0),
            }
        )
    for v in out.values():
        v.sort(key=lambda r: r["priority"])
    return out


def check_kit(
    *,
    parent_material_id: str,
    quantity: float,
    bom: list[dict[str, Any]] | None = None,
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    apply_special_control: bool = True,
) -> dict[str, Any]:
    """Return a strict kit report for ``quantity`` of ``parent_material_id``."""
    bom = bom or []
    inv = _inventory_lookup(inventory)
    inb = _inbound_lookup(inbound)
    sc_rules = compute_special_control_rules(materials or []) if apply_special_control else {}
    materials_by_id = {_mid(m): m for m in (materials or []) if _mid(m)}

    kit = expand_kit(
        parent_material_id=parent_material_id,
        quantity=quantity,
        bom=bom,
        materials=materials,
    )
    if kit["has_cycles"]:
        return {
            "parent": parent_material_id,
            "parent_quantity": quantity,
            "status": "error",
            "error": "BOM contains a cycle; expansion aborted",
            "kit": kit,
        }

    leaves_report: list[dict[str, Any]] = []
    sufficient_count = 0
    short_count = 0
    surplus_count = 0
    for leaf_id, info in kit["leaves"].items():
        required = float(info["gross_qty"])
        on_hand = inv.get(leaf_id, 0.0)
        in_transit = inb.get(leaf_id, 0.0)
        sc_rule = sc_rules.get(leaf_id) if apply_special_control else None
        if sc_rule and should_isolate(sc_rule):
            available = 0.0
            isolated = True
        else:
            available = adjust_atp(on_hand + in_transit, sc_rule)
            isolated = False
        short = max(0.0, required - available)
        surplus = max(0.0, available - required)
        status = "sufficient" if short == 0 else "short"
        if short == 0:
            sufficient_count += 1
        else:
            short_count += 1
        if surplus > 0:
            surplus_count += 1
        meta = materials_by_id.get(leaf_id, {})
        reason = _shortage_reason(required, available, sc_rule, isolated) if apply_special_control else {"code": "ok" if short == 0 else "insufficient_stock", "message": "齐套" if short == 0 else "库存 + 在途 不足"}
        leaves_report.append(
            {
                "material_id": leaf_id,
                "name": meta.get("name") or leaf_id,
                "category": meta.get("category") or "",
                "unit": meta.get("unit") or "kg",
                "required_qty": round(required, 4),
                "on_hand": round(on_hand, 4),
                "in_transit": round(in_transit, 4),
                "available": round(available, 4),
                "short_qty": round(short, 4),
                "surplus_qty": round(surplus, 4),
                "status": status,
                "isolated": isolated,
                "paths": info.get("paths") or _derive_paths(kit["edges"], leaf_id),
                "special_control": (sc_rule or {}).get("level") if apply_special_control else None,
                "reason": reason,
            }
        )
    leaves_report.sort(key=lambda r: (r["status"] != "short", r["material_id"]))
    total_leaves = len(leaves_report)
    kit_status = "complete" if short_count == 0 and total_leaves > 0 else "incomplete"
    total_required = sum(float(r["required_qty"]) for r in leaves_report)
    total_available = sum(float(r["available"]) for r in leaves_report)
    total_gap = sum(float(r["short_qty"]) for r in leaves_report)
    completion_pct = _compute_completion(total_required, total_gap)
    parent_meta = materials_by_id.get(parent_material_id, {})
    return {
        "parent": parent_material_id,
        "parent_name": parent_meta.get("name") or parent_material_id,
        "parent_unit": parent_meta.get("unit") or "kg",
        "parent_quantity": quantity,
        "status": kit_status,
        "summary": {
            "total_leaves": total_leaves,
            "sufficient": sufficient_count,
            "short": short_count,
            "surplus": surplus_count,
            "completion_pct": completion_pct,
            "total_required": round(total_required, 4),
            "total_available": round(total_available, 4),
            "total_gap": round(total_gap, 4),
        },
        "leaves": leaves_report,
        "edges": kit["edges"],
        "depth": kit["depth"],
    }


def check_kit_with_substitutes(
    *,
    parent_material_id: str,
    quantity: float,
    bom: list[dict[str, Any]] | None = None,
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    substitutes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Strict check plus substitute-aware recovery suggestions.

    For each leaf that is short, look at the substitutes table and
    decide whether the alternate material can plug the gap.  A
    substitute is considered *effective* if the alternate's available
    stock covers the residual gap.  The recovery is reported per
    short leaf; a leaf is *recoverable* if at least one effective
    substitute exists.
    """
    report = check_kit(
        parent_material_id=parent_material_id,
        quantity=quantity,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
    )
    if report["status"] == "error":
        return report

    inv = _inventory_lookup(inventory)
    inb = _inbound_lookup(inbound)
    sub_index = _substitute_index(substitutes)
    materials_by_id = {_mid(m): m for m in (materials or []) if _mid(m)}
    sc_rules = compute_special_control_rules(materials or [])

    recovery: list[dict[str, Any]] = []
    recoverable = 0
    skipped_isolated_subs: list[dict[str, str]] = []
    for leaf in report["leaves"]:
        if leaf["status"] != "short":
            leaf["recovery"] = []
            continue
        gap = float(leaf["short_qty"])
        candidates = sub_index.get(leaf["material_id"], [])
        leaf_recovery = []
        for cand in candidates:
            alt_id = cand["substitute_id"]
            alt_raw = inv.get(alt_id, 0.0) + inb.get(alt_id, 0.0)
            if alt_raw <= 0:
                continue
            alt_sc = sc_rules.get(alt_id)
            if alt_sc and should_isolate(alt_sc):
                # Skip substitutes that are themselves under special-control isolation.
                skipped_isolated_subs.append(
                    {
                        "original": leaf["material_id"],
                        "substitute": alt_id,
                        "reason": (alt_sc or {}).get("label") or "特控隔离",
                    }
                )
                continue
            alt_available = adjust_atp(alt_raw, alt_sc)
            if alt_available <= 0:
                continue
            plugged = min(alt_available, gap)
            new_gap = max(0.0, gap - plugged)
            leaf_recovery.append(
                {
                    "substitute_id": alt_id,
                    "name": (materials_by_id.get(alt_id) or {}).get("name") or alt_id,
                    "raw_available": round(alt_raw, 4),
                    "buffer_applied": round(alt_raw - alt_available, 4),
                    "available": round(alt_available, 4),
                    "can_cover": round(plugged, 4),
                    "residual_gap": round(new_gap, 4),
                    "priority": cand["priority"],
                    "special_control_level": (alt_sc or {}).get("level"),
                }
            )
            gap = new_gap
            if gap <= 0:
                break
        leaf["recovery"] = leaf_recovery
        if leaf_recovery and gap <= 0:
            recoverable += 1
        recovery.append(
            {
                "material_id": leaf["material_id"],
                "name": leaf["name"],
                "short_qty": leaf["short_qty"],
                "candidates": leaf_recovery,
                "fully_recoverable": bool(leaf_recovery and gap <= 0),
                "residual_gap": round(gap, 4),
            }
        )

    short_count = report["summary"]["short"]
    if short_count == 0:
        new_status = "complete"
    elif recoverable == short_count:
        new_status = "recoverable"
    elif recoverable > 0:
        new_status = "partial"
    else:
        new_status = "incomplete"
    report["status"] = new_status
    report["summary"]["recoverable"] = recoverable
    report["summary"]["recovery_rate"] = round(
        (recoverable / short_count * 100.0) if short_count else 100.0, 2
    )
    report["summary"]["residual_gap_after_recovery"] = round(
        sum(float(r["residual_gap"]) for r in recovery), 4
    )
    report["recovery"] = recovery
    report["skipped_isolated_substitutes"] = skipped_isolated_subs
    return report


def _derive_paths(edges: list[dict[str, Any]], leaf_id: str) -> list[list[str]]:
    paths: list[list[str]] = []
    for e in edges:
        if e["child"] == leaf_id:
            paths.append(e["path"])
    return paths


def batch_kit_check(
    *,
    items: list[dict[str, Any]],
    bom: list[dict[str, Any]] | None = None,
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    substitutes: list[dict[str, Any]] | None = None,
    use_substitutes: bool = True,
) -> dict[str, Any]:
    """Run check_kit_with_substitutes (or check_kit) for many products at once.

    ``items`` is a list of ``{"parent_material_id": str, "quantity": float}`` dicts.
    Returns one combined report with per-item details and overall rollups.
    """
    runner = check_kit_with_substitutes if use_substitutes else check_kit
    per_item: list[dict[str, Any]] = []
    overall_required = 0.0
    overall_gap = 0.0
    overall_short_items = 0
    overall_complete_items = 0
    overall_recoverable_items = 0
    problem_materials: dict[str, float] = {}
    for entry in items or []:
        parent = str(entry.get("parent_material_id") or "").strip()
        qty = float(entry.get("quantity") or 0)
        if not parent or qty <= 0:
            per_item.append(
                {
                    "parent_material_id": parent,
                    "status": "error",
                    "error": "parent_material_id 和 quantity (>0) 必填",
                }
            )
            continue
        report = runner(
            parent_material_id=parent,
            quantity=qty,
            bom=bom,
            inventory=inventory,
            inbound=inbound,
            materials=materials,
            substitutes=substitutes,
        )
        per_item.append(
            {
                "parent_material_id": parent,
                "parent_name": report.get("parent_name") or parent,
                "status": report.get("status"),
                "summary": report.get("summary", {}),
            }
        )
        sm = report.get("summary", {})
        overall_required += float(sm.get("total_required", 0) or 0)
        overall_gap += float(sm.get("total_gap", 0) or 0)
        if sm.get("short", 0) > 0:
            overall_short_items += 1
            for leaf in report.get("leaves", []):
                if leaf.get("status") == "short":
                    problem_materials[leaf["material_id"]] = (
                        problem_materials.get(leaf["material_id"], 0.0)
                        + float(leaf.get("short_qty", 0) or 0)
                    )
        if report.get("status") == "complete":
            overall_complete_items += 1
        elif report.get("status") == "recoverable":
            overall_recoverable_items += 1
    total_items = sum(1 for it in items or [] if it.get("parent_material_id"))
    return {
        "summary": {
            "total_items": total_items,
            "complete_items": overall_complete_items,
            "recoverable_items": overall_recoverable_items,
            "short_items": overall_short_items,
            "total_required": round(overall_required, 4),
            "total_gap": round(overall_gap, 4),
            "completion_pct": _compute_completion(overall_required, overall_gap),
        },
        "problem_materials": [
            {"material_id": mid, "total_short_qty": round(q, 4)}
            for mid, q in sorted(problem_materials.items(), key=lambda x: -x[1])
        ],
        "items": per_item,
    }


