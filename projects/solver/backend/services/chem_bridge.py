from __future__ import annotations
"""Bridge chemical ATP data into the standard project payload.

The generic ``EFESO Operations AI Workbench`` workflow stores everything
under one project record::

    project.payload = {
        "ontology":    {"graph": {nodes, edges, ...}, ...},
        "result":      {"status", "objective_value", "variables", ...},
        "validation":  {"valid", "errors", "warnings"},
        "scenarios":   [{...}, ...],
    }

The 4 chemical tabs (化工 ATP / 订单承诺 / 多目标排序 / 物料齐套) push
their domain data through this module so the generic
本体图谱 / 数学模型 / 求解结果 / 方案对比 tabs see the same data and
stay the canonical place for visualisation, export, and review.
"""

from typing import Any
from backend.core.chemical_ontology import build_chemical_ontology
from backend.core.ontology_graph import build_graph
from backend.core.special_control import compute_special_control_rules
from backend.core.critical_material import compute_critical_material_rules

import math
import time


def _safe_num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def build_chem_problem(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]] | None = None,
    inbound: list[dict[str, Any]] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
    priorities: list[dict[str, Any]] | None = None,
    bom: list[dict[str, Any]] | None = None,
    substitutes: list[dict[str, Any]] | None = None,
    horizon_days: int = 30,
    base_date: str | None = None,
) -> dict[str, Any]:
    """Return a standard ``problem`` dict for :func:`backend.core.ontology_graph.build_graph`.

    The shape matches what the existing 体系总览 → 本体图谱 / 数学模型 tabs
    already understand, so the chemical 4 tabs become a domain shortcut
    on top of the same data.
    """
    inventory = inventory or []
    inbound = inbound or []
    open_orders = open_orders or []
    priorities = priorities or []
    bom = bom or []
    substitutes = substitutes or []

    # 1. Build the chemical entities + relationships.
    ont = build_chemical_ontology(
        materials=materials,
        customers=[{"id": o.get("customer_id"), "tier": o.get("customer_tier", "base")}
                   for o in open_orders if o.get("customer_id")],
        orders=open_orders,
    )
    entities: list[dict[str, Any]] = list(ont.get("entities") or [])
    relationships: list[dict[str, Any]] = list(ont.get("relationships") or [])

    # 2. Aggregate inventory + inbound into one parameter per material.
    inv_by_mid: dict[str, float] = {}
    for row in inventory:
        mid = str(row.get("material_id") or "").strip()
        if not mid:
            continue
        inv_by_mid[mid] = inv_by_mid.get(mid, 0.0) + _safe_num(row.get("quantity"))
    inb_by_mid: dict[str, float] = {}
    for row in inbound:
        mid = str(row.get("material_id") or "").strip()
        if not mid:
            continue
        inb_by_mid[mid] = inb_by_mid.get(mid, 0.0) + _safe_num(row.get("quantity"))

    # 3. Add BOM as a special "BOM edge" relationship so the ontology graph
    #    shows parent→child expansion lines.
    for edge in bom:
        relationships.append({
            "source": edge.get("parent_material_id"),
            "relation": "BOM 子件",
            "target": edge.get("child_material_id"),
            "confidence": "EXTRACTED",
            "data": {
                "quantity_per_unit": _safe_num(edge.get("quantity_per_unit")),
                "yield_ratio": edge.get("yield_ratio"),
                "scrap_rate": _safe_num(edge.get("scrap_rate")),
            },
        })

    # 4. Add substitute relationships.
    for sub in substitutes:
        relationships.append({
            "source": sub.get("material_id"),
            "relation": "可替代料",
            "target": sub.get("substitute_id"),
            "confidence": "EXTRACTED",
            "data": {"priority": int(sub.get("priority") or 0)},
        })

    # 5. Build parameters from priorities + sc_rules + critical rules.
    sc_rules = compute_special_control_rules(materials)
    cm_rules = compute_critical_material_rules(materials)
    parameters: list[dict[str, Any]] = [
        {
            "id": "horizon_days",
            "name": f"ATP 视界 {horizon_days} 天",
            "value": float(horizon_days),
            "unit": "天",
        },
        {
            "id": "base_date",
            "name": "起始日期",
            "value": base_date or "",
            "unit": "",
        },
    ]
    for mid, sc in sc_rules.items():
        parameters.append({
            "id": f"sc_{mid}",
            "name": f"特控 {mid}",
            "value": float(sc.get("buffer") or 0),
            "unit": f"L{sc.get('level')}",
            "isolated": bool(sc.get("isolated")),
        })
    for mid, cm in cm_rules.items():
        risk_mult = float(cm.get("risk_multiplier") or 1.0)
        parameters.append({
            "id": f"cm_{mid}",
            "name": f"关键物料 {mid}",
            "value": round(1.0 - risk_mult, 4),
            "unit": cm.get("supply_risk") or "multiple",
        })
    for p in priorities:
        parameters.append({
            "id": f"pri_{p.get('customer_tier')}_{p.get('material_category')}",
            "name": f"优先级 {p.get('customer_tier')}/{p.get('material_category')}",
            "value": _safe_num(p.get("weight")),
            "unit": "权重",
        })

    # 6. Build decision variables (one per open order) and constraints.
    decision_variables: list[dict[str, Any]] = []
    for o in open_orders:
        oid = str(o.get("id") or "")
        if not oid:
            continue
        decision_variables.append({
            "id": f"x_{oid}",
            "name": f"订单 {oid} 承诺量",
            "type": "continuous",
            "lower_bound": 0,
            "upper_bound": _safe_num(o.get("quantity")),
            "data": {
                "order_id": oid,
                "material_id": o.get("material_id"),
                "customer_tier": o.get("customer_tier", "base"),
                "due_date": o.get("due_date"),
            },
        })

    constraints: list[dict[str, Any]] = []
    # 6a. Per-material inventory capacity constraint.
    for mid in sorted({o.get("material_id") for o in open_orders if o.get("material_id")}):
        coeffs = {
            f"x_{o['id']}": 1.0
            for o in open_orders
            if o.get("material_id") == mid and o.get("id")
        }
        if not coeffs:
            continue
        rhs = inv_by_mid.get(mid, 0.0) + inb_by_mid.get(mid, 0.0)
        sc = sc_rules.get(mid)
        if sc and sc.get("isolated"):
            rhs = 0.0
        elif sc:
            rhs = rhs * (1.0 - float(sc.get("buffer") or 0))
        cm = cm_rules.get(mid)
        if cm:
            rhs = rhs * float(cm.get("risk_multiplier") or 1.0)
        constraints.append({
            "id": f"cap_{mid}",
            "name": f"{mid} 可用容量",
            "sense": "<=",
            "rhs": round(rhs, 4),
            "coefficients": coeffs,
        })

    # 6b. Special control isolation constraint (level 0 must not consume shared pool).
    for mid, sc in sc_rules.items():
        if not sc.get("isolated"):
            continue
        coeffs = {
            f"x_{o['id']}": 1.0
            for o in open_orders
            if o.get("material_id") == mid and o.get("id")
        }
        if not coeffs:
            continue
        constraints.append({
            "id": f"iso_{mid}",
            "name": f"{mid} 隔离约束 (L0)",
            "sense": "=",
            "rhs": 0.0,
            "coefficients": coeffs,
        })

    # 7. Build the objective: weighted-sum of fulfilled orders.
    pri_index: dict[tuple[str, str], float] = {
        (str(p.get("customer_tier")), str(p.get("material_category"))): _safe_num(p.get("weight"))
        for p in priorities
    }
    cat_by_mid = {str(m.get("id")): m.get("category") for m in materials}
    obj_coeffs: dict[str, float] = {}
    for o in open_orders:
        oid = str(o.get("id") or "")
        if not oid:
            continue
        tier = str(o.get("customer_tier") or "base")
        cat = str(cat_by_mid.get(o.get("material_id")) or "finished")
        w = pri_index.get((tier, cat), 1.0)
        obj_coeffs[f"x_{oid}"] = round(w, 4)
    objective = {
        "id": "OBJECTIVE",
        "name": "最大化承诺订单加权完成量",
        "expression": "max Σ weight[tier,cat] · x_order (x_order 为承诺数量)",
        "coefficients": obj_coeffs,
        "sense": "maximize",
    }

    return {
        "objective": objective,
        "objective_sense": "maximize",
        "entities": entities,
        "relationships": relationships,
        "decision_variables": decision_variables,
        "parameters": parameters,
        "constraints": constraints,
        "objective_coefficients": obj_coeffs,
        "metadata": {
            "domain": "chemical",
            "horizon_days": horizon_days,
            "base_date": base_date,
            "counts": {
                "materials": len(materials),
                "orders": len(open_orders),
                "bom_edges": len(bom),
                "substitutes": len(substitutes),
            },
        },
    }


def build_chem_ontology(problem: dict[str, Any]) -> dict[str, Any]:
    """Wrap :func:`backend.core.ontology_graph.build_graph` for chemical problems."""
    return build_graph(problem)


def _atp_to_solver_result(
    atp_result: dict[str, Any],
    *,
    engine: str = "ATP-SAP-BOP",
    objective_sense: str = "maximize",
) -> dict[str, Any]:
    """Convert an ATP service result into the standard ``solver_result`` shape.

    ``atp_result`` is the raw output of
    :func:`backend.services.atp_service.compute_atp` which returns::

        {
            "base_date": "2026-07-02",
            "horizon_days": 30,
            "matrix": [{material_id, name, series:[{date,atp,level}], orders_promised, orders_short}, ...],
            "alerts":   [{material_id, date, atp, severity, message}, ...],
            "ots":      {premium, comfort, base, overall},
            "summary":  {materials, horizon_days, orders_total, orders_promised, orders_short, overall_ots_pct},
        }

    The bridge emits one ``variables`` entry per day × material (daily ATP
    allocation), ``constraints_check`` per material (avg ATP + alert count),
    and ``objective_value`` as the weighted OTS percentage.
    """
    matrix: list[dict[str, Any]] = list(atp_result.get("matrix") or [])
    alerts: list[dict[str, Any]] = list(atp_result.get("alerts") or [])
    summary: dict[str, Any] = dict(atp_result.get("summary") or {})
    ots: dict[str, Any] = dict(atp_result.get("ots") or {})

    # Per day × material ATP variables.
    variables: list[dict[str, Any]] = []
    for m in matrix:
        mid = str(m.get("material_id") or "")
        for s in m.get("series") or []:
            date = str(s.get("date") or "")
            atp_v = _safe_num(s.get("atp"))
            variables.append({
                "id": f"atp_{mid}_{date}",
                "name": f"{m.get('name') or mid} @ {date}",
                "value": atp_v,
                "status": s.get("level") or "ok",
                "data": {
                    "material_id": mid,
                    "date": date,
                    "atp": atp_v,
                    "level": s.get("level"),
                },
            })

    # Per material capacity check (avg ATP + alert count).
    constraints_check: list[dict[str, Any]] = []
    alerts_by_mid: dict[str, int] = {}
    for a in alerts:
        alerts_by_mid[str(a.get("material_id") or "")] = alerts_by_mid.get(str(a.get("material_id") or ""), 0) + 1
    for m in matrix:
        mid = str(m.get("material_id") or "")
        series = m.get("series") or []
        atp_vals = [_safe_num(s.get("atp")) for s in series]
        avg_atp = round(sum(atp_vals) / len(atp_vals), 4) if atp_vals else 0.0
        promised = int(m.get("orders_promised") or 0)
        short = int(m.get("orders_short") or 0)
        constraints_check.append({
            "id": f"cap_{mid}",
            "name": f"{mid} 容量",
            "slack": avg_atp,
            "rhs": round(avg_atp + short, 4),
            "status": "ok" if short == 0 else "tight",
            "data": {
                "material_id": mid,
                "orders_promised": promised,
                "orders_short": short,
                "alerts": alerts_by_mid.get(mid, 0),
                "min_atp": min(atp_vals) if atp_vals else 0.0,
                "max_atp": max(atp_vals) if atp_vals else 0.0,
            },
        })

    # Objective: weighted OTS percentage (premium-weighted).
    weights = {"premium": 1.0, "comfort": 0.6, "base": 0.3}
    objective_value = sum(_safe_num(ots.get(k)) * w for k, w in weights.items() if k in ots)
    if not objective_value:
        objective_value = _safe_num(ots.get("overall"))

    return {
        "status": "optimal" if not alerts else "feasible",
        "engine": engine,
        "solver_family": "chemical-atp",
        "strict_solution": False,
        "objective_value": round(objective_value, 4),
        "variables": variables,
        "constraints_check": constraints_check,
        "alerts": alerts,
        "metadata": {
            "horizon_days": atp_result.get("horizon_days"),
            "base_date": atp_result.get("base_date"),
            "ots": ots,
            "summary": summary,
            "materials": [
                {
                    "material_id": m.get("material_id"),
                    "name": m.get("name") or m.get("material_id"),
                    "orders_promised": int(m.get("orders_promised") or 0),
                    "orders_short": int(m.get("orders_short") or 0),
                }
                for m in matrix
            ],
        },
    }


def build_chem_scenarios(
    *,
    materials: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    inbound: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    priorities: list[dict[str, Any]] | None = None,
    horizon_days: int = 30,
    base_date: str | None = None,
) -> list[dict[str, Any]]:
    """Run a small set of canonical chemical scenarios and return a list of
    ``{name, flags, atp, result}`` dicts suitable for the generic
    方案对比 tab.

    The three scenarios vary how ``compute_atp`` applies the two
    domain rules (special control / critical material) so the user can
    see the effect of toggling them off:

    * **基线**: 全部启用 (default behaviour).
    * **关闭特控**: special control buffer + isolation disabled via
      ``sc_overrides`` (all materials get ``buffer=0, isolated=False``).
    * **关闭关键物料**: critical material risk multiplier neutralised by
      setting ``supply_risk=multiple`` on every material copy.
    """
    from backend.services.atp_service import compute_atp
    from backend.core.special_control import compute_special_control_rules

    sc_rules = compute_special_control_rules(materials)
    sc_keys = list(sc_rules.keys())

    # Build per-scenario inputs.
    sc_overrides_off: dict[str, dict[str, Any]] = {
        mid: {"buffer": 0.0, "isolated": False, "level": 2} for mid in sc_keys
    }

    def _neutralise_critical(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**r, "supply_risk": "multiple"} for r in rows]

    scenarios_specs: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = [
        ("基线 (全部启用)", {}, list(materials)),
        ("关闭特控 buffer", {"sc_overrides": sc_overrides_off}, list(materials)),
        ("关闭关键物料 risk", {}, _neutralise_critical(materials)),
    ]

    from datetime import date as _date
    base_date_obj: _date | None = None
    if base_date:
        try:
            base_date_obj = _date.fromisoformat(base_date)
        except ValueError:
            base_date_obj = None
    scenarios: list[dict[str, Any]] = []
    for name, kwargs, materials_in in scenarios_specs:
        t0 = time.perf_counter()
        atp = compute_atp(
            materials=materials_in,
            inventory=inventory,
            inbound=inbound,
            open_orders=open_orders,
            horizon_days=horizon_days,
            base_date=base_date_obj,
            priorities=priorities or [],
            **kwargs,
        )
        elapsed = round(time.perf_counter() - t0, 4)
        result = _atp_to_solver_result(atp)
        result["elapsed_seconds"] = elapsed
        scenarios.append({
            "name": name,
            "flags": kwargs,
            "atp": atp,
            "result": result,
        })
    return scenarios
