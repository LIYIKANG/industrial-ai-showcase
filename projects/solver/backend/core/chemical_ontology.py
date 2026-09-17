"""Chemical industry domain ontology for the EFESO Operations AI Workbench.

Builds the chemical ontology layer that sits on top of the generic LP/MILP
ontology. Materials, tanks, units, customers, orders, and suppliers are
expressed as ``entities[]`` / ``relationships[]`` so the existing
``ontology_graph`` builder can render them without any modification.

Three orthogonal dimensions carry business semantics:

* ``special_control_level`` 0/1/2 - government-regulated / process-controlled
  / company-custom. Driven by :mod:`backend.core.special_control`.
* ``supply_risk`` ``single`` / ``dual`` / ``multiple`` - single / dual /
  multi-source. Driven by :mod:`backend.core.critical_material`.
* ``lead_time_bucket`` ``short`` / ``mid`` / ``long`` - days of inbound
  pipeline to add to the ATP horizon.

The module is intentionally pure-Python (no LLM) so it is deterministic,
cheap to call, and easy to unit test.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Reference data - kept as module-level constants so the UI can mirror them
# ---------------------------------------------------------------------------

MATERIAL_CATEGORIES = ("raw_material", "intermediate", "finished", "byproduct", "co_product")
SUPPLY_RISK_LEVELS = ("single", "dual", "multiple")
LEAD_TIME_BUCKETS = {"short": 7, "mid": 30, "long": 90}
CUSTOMER_TIERS = ("premium", "comfort", "base")
SPECIAL_CONTROL_LABELS = {
    0: "剧毒/易制爆/易制毒",
    1: "重点监管工艺",
    2: "企业自定义特控",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_chemical_ontology(
    materials: list[dict[str, Any]],
    tanks: list[dict[str, Any]] | None = None,
    customers: list[dict[str, Any]] | None = None,
    orders: list[dict[str, Any]] | None = None,
    suppliers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return ``{"entities": [...], "relationships": [...]}`` for the chemical layer.

    Each entity is shaped like the generic ontology expects, so the result
    can be merged with the LP/MILP model entities and fed to
    :func:`backend.core.ontology_graph.build_graph` unchanged.
    """

    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    tanks = tanks or []
    customers = customers or []
    orders = orders or []
    suppliers = suppliers or []

    for material in materials:
        entities.append(_material_entity(material))
        if material.get("supplier_id"):
            relationships.append(
                {
                    "source": material["id"],
                    "relation": "采购自",
                    "target": material["supplier_id"],
                    "confidence": "EXTRACTED",
                }
            )
        if material.get("parent_material_id"):
            relationships.append(
                {
                    "source": material["parent_material_id"],
                    "relation": "BOM 父件",
                    "target": material["id"],
                    "confidence": "EXTRACTED",
                }
            )

    for tank in tanks:
        entities.append(_tank_entity(tank))
        if tank.get("material_id"):
            relationships.append(
                {
                    "source": tank["id"],
                    "relation": "储存",
                    "target": tank["material_id"],
                    "confidence": "EXTRACTED",
                }
            )

    for customer in customers:
        entities.append(_customer_entity(customer))

    for order in orders:
        entities.append(_order_entity(order))
        if order.get("customer_id"):
            relationships.append(
                {
                    "source": order["id"],
                    "relation": "客户",
                    "target": order["customer_id"],
                    "confidence": "EXTRACTED",
                }
            )
        if order.get("material_id"):
            relationships.append(
                {
                    "source": order["id"],
                    "relation": "需求物料",
                    "target": order["material_id"],
                    "confidence": "EXTRACTED",
                }
            )

    for supplier in suppliers:
        entities.append(_supplier_entity(supplier))

    return {"entities": entities, "relationships": relationships}


def classify_material(material: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised view of one material for downstream rules.

    Centralises the "what kind of material is this" judgement that both
    :mod:`special_control` and :mod:`critical_material` need.
    """

    return {
        "id": material.get("id") or material.get("material_id") or "",
        "name": material.get("name") or "",
        "category": material.get("category") or "raw_material",
        "special_control_level": int(material["special_control_level"]) if material.get("special_control_level") not in (None, "") else 2,
        "supply_risk": material.get("supply_risk") or "multiple",
        "lead_time_bucket": material.get("lead_time_bucket") or "mid",
        "lead_time_days": int(material["lead_time_days"]) if material.get("lead_time_days") not in (None, "") else LEAD_TIME_BUCKETS.get(material.get("lead_time_bucket") or "mid", 30),
        "price_sensitive": bool(material.get("price_sensitive", False)),
        "safety_stock": float(material.get("safety_stock") or 0),
        "unit": material.get("unit") or "kg",
    }


def atp_horizon(material: dict[str, Any], base_horizon_days: int = 30) -> int:
    """Long lead time materials need a wider ATP window."""
    return max(base_horizon_days, classify_material(material)["lead_time_days"] + 7)


def expand_horizon(
    base_date: date,
    materials: list[dict[str, Any]],
    base_horizon_days: int = 30,
) -> list[date]:
    """Daily date list from ``base_date`` covering the union of horizons."""
    max_horizon = max(atp_horizon(m, base_horizon_days) for m in materials) if materials else base_horizon_days
    return [base_date + timedelta(days=offset) for offset in range(max_horizon + 1)]


# ---------------------------------------------------------------------------
# Entity builders
# ---------------------------------------------------------------------------


def _material_entity(material: dict[str, Any]) -> dict[str, Any]:
    view = classify_material(material)
    sc = view["special_control_level"]
    risk = view["supply_risk"]
    return {
        "id": view["id"],
        "name": material.get("name") or view["id"],
        "type": "化工物料",
        "description": (
            f"分类={view['category']}; 特控={sc}({SPECIAL_CONTROL_LABELS.get(sc, '')}); "
            f"供应风险={risk}; 交期={view['lead_time_days']}天; "
            f"价格敏感={'是' if view['price_sensitive'] else '否'}"
        ),
        "source": "化工主数据",
        "confidence": "EXTRACTED",
        "properties": {
            "category": {"value": view["category"], "unit": "", "description": "物料分类"},
            "special_control_level": {"value": sc, "unit": "级", "description": "特控等级"},
            "supply_risk": {"value": risk, "unit": "", "description": "供应风险"},
            "lead_time_days": {"value": view["lead_time_days"], "unit": "天", "description": "采购交期"},
            "price_sensitive": {"value": view["price_sensitive"], "unit": "", "description": "价格敏感"},
            "safety_stock": {"value": view["safety_stock"], "unit": view["unit"], "description": "安全库存"},
        },
    }


def _tank_entity(tank: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": tank["id"],
        "name": tank.get("name") or tank["id"],
        "type": "储罐/装置",
        "description": f"容量={tank.get('capacity', 0)} {tank.get('unit', 'kg')}",
        "source": "化工主数据",
        "confidence": "EXTRACTED",
        "properties": {
            "capacity": {"value": float(tank.get("capacity") or 0), "unit": tank.get("unit") or "kg", "description": "容量"},
            "material_id": {"value": tank.get("material_id") or "", "unit": "", "description": "储存物料"},
        },
    }


def _customer_entity(customer: dict[str, Any]) -> dict[str, Any]:
    tier = customer.get("tier") or "base"
    return {
        "id": customer["id"],
        "name": customer.get("name") or customer["id"],
        "type": "客户",
        "description": f"客户等级={tier}",
        "source": "化工主数据",
        "confidence": "EXTRACTED",
        "properties": {
            "tier": {"value": tier, "unit": "", "description": "客户等级"},
        },
    }


def _order_entity(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order["id"],
        "name": order.get("name") or order["id"],
        "type": "订单",
        "description": (
            f"需求={order.get('quantity', 0)} {order.get('unit', 'kg')}; "
            f"交期={order.get('due_date')}; 客户={order.get('customer_id') or '-'}"
        ),
        "source": "订单导入",
        "confidence": "EXTRACTED",
        "properties": {
            "quantity": {"value": float(order.get("quantity") or 0), "unit": order.get("unit") or "kg", "description": "需求量"},
            "due_date": {"value": str(order.get("due_date") or ""), "unit": "", "description": "交期"},
            "customer_id": {"value": order.get("customer_id") or "", "unit": "", "description": "客户ID"},
            "material_id": {"value": order.get("material_id") or "", "unit": "", "description": "物料ID"},
        },
    }


def _supplier_entity(supplier: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": supplier["id"],
        "name": supplier.get("name") or supplier["id"],
        "type": "供应商",
        "description": f"风险等级={supplier.get('risk_level') or 'normal'}",
        "source": "化工主数据",
        "confidence": "EXTRACTED",
        "properties": {
            "risk_level": {"value": supplier.get("risk_level") or "normal", "unit": "", "description": "风险等级"},
        },
    }


def merge_with_problem(chemical: dict[str, Any], problem: dict[str, Any]) -> dict[str, Any]:
    """Return ``problem`` with chemical entities/relationships merged in."""
    problem = dict(problem)
    problem["entities"] = list(problem.get("entities") or []) + list(chemical.get("entities") or [])
    problem["relationships"] = list(problem.get("relationships") or []) + list(chemical.get("relationships") or [])
    return problem
