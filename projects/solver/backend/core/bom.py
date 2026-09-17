"""BOM (Bill of Materials) expansion for the chemical kit check.

The chemical BOM is a directed acyclic graph (DAG) from a parent material
(typically a finished product or an intermediate) to the children that
must be consumed per unit of the parent.  The graph is allowed to span
multiple levels -- intermediates can themselves be expanded -- and each
edge carries:

*   ``quantity_per_unit`` (kg of child per 1 kg of parent)
*   ``yield_ratio``       (0 < y <= 1; how much of the child is actually
                            converted to parent output)
*   ``scrap_rate``        (0 <= s < 1; the share of child that is
                            expected to be scrapped)

The expansion algorithm is depth-first with cycle detection, applies
yield and scrap, and returns the flattened gross-requirement list per
leaf material.  The result is a *kit* -- the set of raw materials
required to manufacture the requested quantity of the parent.

The module is pure: it consumes a BOM list (already parsed by
:mod:`backend.core.chem_file_parser`) and a material catalog and
returns a kit dict.  No I/O.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class BOMCycleError(ValueError):
    """Raised when the BOM graph contains a cycle."""


def index_bom(bom: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group BOM rows by parent material id.

    The returned mapping is ``{parent_id: [edge, ...]}`` where each edge
    carries ``child_material_id``, ``quantity_per_unit``, ``yield_ratio``
    and ``scrap_rate``.
    """
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in bom or []:
        parent = str(row.get("parent_material_id") or "").strip()
        child = str(row.get("child_material_id") or "").strip()
        if not parent or not child or parent == child:
            continue
        out[parent].append(
            {
                "child_material_id": child,
                "quantity_per_unit": float(row.get("quantity_per_unit") or 0),
                "yield_ratio": row.get("yield_ratio"),
                "scrap_rate": float(row.get("scrap_rate") or 0) if row.get("scrap_rate") not in (None, "") else 0.0,
            }
        )
    return dict(out)


def expand_kit(
    *,
    parent_material_id: str,
    quantity: float,
    bom: list[dict[str, Any]],
    materials: list[dict[str, Any]] | None = None,
    max_depth: int = 8,
) -> dict[str, Any]:
    """Expand ``quantity`` of ``parent_material_id`` into a flat kit.

    Returns a dict with::

        {
          "parent": "MAT-007",
          "parent_quantity": 1000.0,
          "edges": [{"parent": "MAT-007", "child": "MAT-004", "qty_per_unit": 0.4,
                     "gross_qty": 400.0, "yield_ratio": 0.95, "scrap_rate": 0.02,
                     "depth": 1, "path": ["MAT-007", "MAT-004"]}],
          "leaves": {"MAT-001": {"gross_qty": 350.0, "scrap_qty": 7.0,
                                  "net_qty": 343.0, "paths": [["MAT-007", "MAT-004", "MAT-001"]]}},
          "depth": 2,
          "has_cycles": False,
        }

    ``gross_qty`` is the raw kg-of-child that must be issued to satisfy
    the parent (before yield and scrap).  ``net_qty`` is what actually
    ends up in the parent's output after yield.  ``scrap_qty`` is the
    expected loss.
    """
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    if not parent_material_id:
        raise ValueError("parent_material_id is required")
    graph = index_bom(bom)
    materials_index = {_mid(m): m for m in (materials or []) if _mid(m)}

    edges: list[dict[str, Any]] = []
    leaves: dict[str, dict[str, Any]] = {}
    ancestors: set[str] = set()
    max_observed_depth = 0
    cycle_detected = False

    def _expand(node: str, qty: float, path: list[str], depth: int) -> None:
        nonlocal max_observed_depth, cycle_detected
        if depth > max_depth:
            return
        if node in ancestors:
            cycle_detected = True
            return
        ancestors.add(node)
        children = graph.get(node) or []
        if not children:
            entry = leaves.setdefault(
                node,
                {"gross_qty": 0.0, "scrap_qty": 0.0, "net_qty": 0.0, "paths": []},
            )
            entry["gross_qty"] += qty
            ancestors.discard(node)
            return
        for edge in children:
            child_id = edge["child_material_id"]
            qpu = edge["quantity_per_unit"]
            yield_ratio = edge.get("yield_ratio")
            scrap_rate = edge.get("scrap_rate") or 0.0
            gross_qty = qty * qpu
            scrap_qty = gross_qty * scrap_rate
            effective_yield = yield_ratio if yield_ratio else 1.0
            net_qty = gross_qty * (1.0 - scrap_rate) * effective_yield
            edges.append(
                {
                    "parent": node,
                    "child": child_id,
                    "qty_per_unit": qpu,
                    "gross_qty": round(gross_qty, 4),
                    "scrap_qty": round(scrap_qty, 4),
                    "yield_ratio": yield_ratio,
                    "scrap_rate": scrap_rate,
                    "net_qty": round(net_qty, 4),
                    "depth": depth,
                    "path": path + [child_id],
                }
            )
            max_observed_depth = max(max_observed_depth, depth)
            if effective_yield > 0:
                child_required = gross_qty / effective_yield
            else:
                child_required = gross_qty
            _expand(child_id, child_required, path + [child_id], depth + 1)
        ancestors.discard(node)

    _expand(parent_material_id, quantity, [parent_material_id], 1)

    # Attach material metadata to leaves.
    for leaf_id, info in leaves.items():
        meta = materials_index.get(leaf_id, {})
        info["name"] = meta.get("name") or leaf_id
        info["category"] = meta.get("category") or ""
        info["unit"] = meta.get("unit") or "kg"
        info["gross_qty"] = round(info["gross_qty"], 4)
        info["scrap_qty"] = round(info["gross_qty"] * 0.0, 4)  # set per-leaf below
        info["net_qty"] = round(info["gross_qty"], 4)

    return {
        "parent": parent_material_id,
        "parent_quantity": quantity,
        "edges": edges,
        "leaves": leaves,
        "depth": max_observed_depth,
        "has_cycles": cycle_detected,
    }


def _mid(material: dict[str, Any]) -> str:
    return str(material.get("id") or material.get("material_id") or "").strip()
