"""Chemical industry ATP (Available-to-Promise) service.

Algorithm skeleton adapted from
``Viktor-Crettenand/Implementation-of-SAP-BOP`` (SAP aATP, single-file
Python, MIT).  We translate the original 30-line notebook into a clean
service that:

* event-drives the schedule (creation / due / inbound dates),
* sorts the order pool by customer tier (premium > comfort > base), due
  date, and special-control level,
* tracks running inventory per material, applying inbound receipts and
  inventory expiry where the data is available,
* applies :mod:`backend.core.special_control` and
  :mod:`backend.core.critical_material` rules,
* returns the (material, date) ATP matrix, alert list, and OTS metrics.

The function ``compute_atp`` is pure (no I/O) and is exercised by
``tests/test_atp_service.py``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from backend.core.critical_material import (
    adjusted_horizon,
    apply_critical_material_rules,
    compute_critical_material_rules,
)
from backend.core.special_control import (
    adjust_atp,
    compute_special_control_rules,
    should_isolate,
    summarise as sc_summarise,
)
from backend.core.critical_material import summarise as cm_summarise


CUSTOMER_TIER_RANK = {"premium": 0, "comfort": 1, "base": 2}


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default  # NaN guard


def _material_key(material: dict[str, Any]) -> str:
    return str(material.get("id") or material.get("material_id") or "").strip()


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------


@dataclass
class ATPSummary:
    materials: int
    horizon_days: int
    elapsed_ms: float
    orders_total: int
    orders_promised: int
    orders_short: int
    overall_ots_pct: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_atp(
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    *,
    horizon_days: int = 30,
    base_date: date | None = None,
    priorities: list[dict[str, Any]] | None = None,
    sc_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute the (material, date) ATP matrix.

    Returns a dict shaped as::

        {
            "matrix":  [{"material_id": "MAT-001", "name": "...", "category": "...",
                        "series": [{"date": "2026-07-02", "atp": 100.0, "level": "ok"}, ...],
                        "special_control": {...}, "critical": {...}}],
            "alerts":  [{"material_id": "MAT-001", "date": "...", "atp": -5.0, "severity": "high"}],
            "ots":     {"premium": 100.0, "comfort": 90.0, "base": 70.0, "overall": 88.0},
            "summary": {...},
        }
    """
    started = time.perf_counter()
    inventory = inventory or []
    inbound = inbound or []
    open_orders = open_orders or []
    priorities = priorities or []

    base = base_date or date.today()

    sc_rules = compute_special_control_rules(materials, sc_overrides)
    cm_rules = compute_critical_material_rules(materials)
    priority_weight = _priority_weight_map(priorities)

    materials_by_id = {_material_key(m): m for m in materials if _material_key(m)}

    matrix: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    all_promised: list[dict[str, Any]] = []

    for material in materials:
        mid = _material_key(material)
        if not mid:
            continue
        sc_rule = sc_rules.get(mid)
        if should_isolate(sc_rule):
            matrix.append(
                {
                    "material_id": mid,
                    "name": material.get("name") or mid,
                    "category": material.get("category") or "raw_material",
                    "unit": material.get("unit") or "kg",
                    "isolated": True,
                    "special_control": sc_summarise(mid, sc_rule),
                    "critical": cm_summarise(mid, cm_rules.get(mid)),
                    "series": [],
                }
            )
            continue

        cm_rule = cm_rules.get(mid)
        horizon = adjusted_horizon(base, cm_rule, horizon_days)
        mat_inventory = [row for row in inventory if (row.get("material_id") or "").strip() == mid]
        mat_inbound = [row for row in inbound if (row.get("material_id") or "").strip() == mid]
        mat_orders = [row for row in open_orders if (row.get("material_id") or "").strip() == mid]

        series, promised, shortfall = _simulate_material(
            material=material,
            base_date=base,
            horizon=horizon,
            inventory_rows=mat_inventory,
            inbound_rows=mat_inbound,
            order_rows=mat_orders,
            sc_rule=sc_rule,
            cm_rule=cm_rule,
            priority_weight=priority_weight,
        )

        for entry in series:
            if entry["atp"] < 0:
                alerts.append(
                    {
                        "material_id": mid,
                        "date": entry["date"],
                        "atp": round(entry["atp"], 2),
                        "severity": "high" if entry["atp"] < -_to_float(material.get("safety_stock")) else "medium",
                    }
                )
            elif material.get("safety_stock") and entry["atp"] < _to_float(material.get("safety_stock")):
                alerts.append(
                    {
                        "material_id": mid,
                        "date": entry["date"],
                        "atp": round(entry["atp"], 2),
                        "severity": "low",
                    }
                )

        for entry in shortfall:
            alerts.append(
                {
                    "material_id": mid,
                    "date": entry["date"],
                    "atp": round(entry["atp"], 2),
                    "severity": "info",
                    "order_id": entry["order_id"],
                }
            )

        all_promised.extend(promised)

        matrix.append(
            {
                "material_id": mid,
                "name": material.get("name") or mid,
                "category": material.get("category") or "raw_material",
                "unit": material.get("unit") or "kg",
                "safety_stock": _to_float(material.get("safety_stock")),
                "isolated": False,
                "special_control": sc_summarise(mid, sc_rule),
                "critical": cm_summarise(mid, cm_rule),
                "series": series,
                "orders_promised": sum(1 for row in promised if row.get("on_time")),
                "orders_short": sum(1 for row in promised if not row.get("on_time")),
            }
        )

    ots = _ots_metrics(all_promised)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    summary = ATPSummary(
        materials=len(matrix),
        horizon_days=horizon_days,
        elapsed_ms=elapsed_ms,
        orders_total=len(all_promised),
        orders_promised=sum(1 for row in all_promised if row.get("on_time")),
        orders_short=sum(1 for row in all_promised if not row.get("on_time")),
        overall_ots_pct=ots["overall"],
    ).__dict__

    return {
        "base_date": base.isoformat(),
        "horizon_days": horizon_days,
        "matrix": matrix,
        "alerts": alerts,
        "ots": ots,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Per-material simulation
# ---------------------------------------------------------------------------


def _simulate_material(
    *,
    material: dict[str, Any],
    base_date: date,
    horizon: list[date],
    inventory_rows: list[dict[str, Any]],
    inbound_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    sc_rule: dict[str, Any] | None,
    cm_rule: dict[str, Any] | None,
    priority_weight: dict[tuple[str, str], float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the SAP-BOP style event-driven loop for one material.

    Returns ``(series, promised_orders, shortfall)``.
    """
    # Initial inventory: sum of all on-hand rows.
    raw_inventory = sum(_to_float(row.get("quantity")) for row in inventory_rows)

    # Pre-bucket orders by creation date, drop rows missing a date.
    order_pool: list[dict[str, Any]] = []
    for order in order_rows:
        created = _coerce_date(order.get("creation_date"))
        due = _coerce_date(order.get("due_date"))
        if created is None and due is None:
            continue
        order_pool.append(
            {
                "id": str(order.get("id") or order.get("order_id") or ""),
                "customer_id": str(order.get("customer_id") or ""),
                "customer_tier": str(order.get("customer_tier") or "base").lower(),
                "material_id": _material_key(material),
                "quantity": _to_float(order.get("quantity")),
                "creation_date": created or base_date,
                "due_date": due or (created or base_date),
                "unit": order.get("unit") or material.get("unit") or "kg",
            }
        )

    inbound_index: dict[date, float] = {}
    for row in inbound_rows:
        d = _coerce_date(row.get("expected_date") or row.get("arrival_date") or row.get("date"))
        if d is None:
            continue
        inbound_index[d] = inbound_index.get(d, 0.0) + _to_float(row.get("quantity"))

    # Apply special-control and critical-material buffers to the starting inventory.
    available = adjust_atp(raw_inventory, sc_rule)
    available = apply_critical_material_rules(_material_key(material), available, cm_rule)

    # Build the union of event dates (matches SAP-BOP: creation | latest_process | inbound).
    event_dates = {d for d in inbound_index}
    for order in order_pool:
        event_dates.add(order["creation_date"])
        event_dates.add(order["due_date"])
    event_dates.update(horizon)
    sorted_events = sorted(event_dates)

    series: list[dict[str, Any]] = []
    promised: list[dict[str, Any]] = []
    shortfall: list[dict[str, Any]] = []
    fulfilled: set[str] = set()

    for present in sorted_events:
        # Inbound at present.
        if present in inbound_index:
            available += adjust_atp(inbound_index[present], sc_rule)
            available = apply_critical_material_rules(_material_key(material), available, cm_rule)

        # Pool of orders that exist by `present` and have not been processed yet.
        pool = [o for o in order_pool if o["id"] not in fulfilled and o["creation_date"] <= present]

        # Sort by SAP-BOP: customer_tier ascending (premium=0 first), due_date asc.
        pool.sort(
            key=lambda o: (
                CUSTOMER_TIER_RANK.get(o["customer_tier"], 3),
                o["due_date"],
                -(priority_weight.get((o["customer_tier"], material.get("category") or ""), 0.0)),
            )
        )

        running_cum = 0.0
        for order in pool:
            if order["id"] in fulfilled:
                continue
            running_cum += order["quantity"]
            if running_cum <= available and order["due_date"] >= present:
                # Fulfill.
                available -= order["quantity"]
                promised.append(
                    {
                        "material_id": _material_key(material),
                        "order_id": order["id"],
                        "customer_tier": order["customer_tier"],
                        "quantity": order["quantity"],
                        "fulfillment_date": present.isoformat(),
                        "due_date": order["due_date"].isoformat(),
                        "on_time": order["due_date"] >= present,
                    }
                )
                fulfilled.add(order["id"])
            elif order["due_date"] < present:
                # Past due and we cannot fulfil: record shortfall and mark as not promised.
                shortfall.append(
                    {
                        "material_id": _material_key(material),
                        "order_id": order["id"],
                        "date": present.isoformat(),
                        "atp": running_cum,
                    }
                )
                promised.append(
                    {
                        "material_id": _material_key(material),
                        "order_id": order["id"],
                        "customer_tier": order["customer_tier"],
                        "quantity": order["quantity"],
                        "fulfillment_date": None,
                        "due_date": order["due_date"].isoformat(),
                        "on_time": False,
                    }
                )
                fulfilled.add(order["id"])

        if present in horizon:
            level = _classify_level(available, _to_float(material.get("safety_stock")))
            series.append({"date": present.isoformat(), "atp": round(available, 2), "level": level})

    return series, promised, shortfall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _priority_weight_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for row in rows:
        tier = str(row.get("customer_tier") or "").lower()
        cat = str(row.get("material_category") or row.get("category") or "").lower()
        weight = _to_float(row.get("weight"), 0.0)
        if tier and cat:
            out[(tier, cat)] = weight
    return out


def _classify_level(atp: float, safety_stock: float) -> str:
    if atp < 0:
        return "short"
    if safety_stock and atp < safety_stock:
        return "tight"
    if safety_stock and atp < safety_stock * 2:
        return "watch"
    return "ok"


def _ots_metrics(promised: list[dict[str, Any]]) -> dict[str, float]:
    by_tier: dict[str, list[bool]] = {"premium": [], "comfort": [], "base": []}
    for row in promised:
        tier = row.get("customer_tier") or "base"
        by_tier.setdefault(tier, []).append(bool(row.get("on_time")))
    out: dict[str, float] = {}
    total_on_time = 0
    total = 0
    for tier, flags in by_tier.items():
        if not flags:
            out[tier] = 100.0
        else:
            pct = round(100.0 * sum(flags) / len(flags), 2)
            out[tier] = pct
        total_on_time += sum(flags)
        total += len(flags)
    out["overall"] = round(100.0 * total_on_time / total, 2) if total else 100.0
    return out


