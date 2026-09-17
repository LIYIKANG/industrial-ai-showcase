"""Ontology graph builder.

The output shape is stable (consumed by ``ontology_tables`` and the JS front-end)
so the public dict layout must not change. Internally we use ``networkx`` for
degree / density / community math, which keeps the structural inference logic
in plain Python where the domain rules live.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import networkx as nx

COLORS = {
    "objective": "#1976d2",
    "entity": "#7c3aed",
    "variable": "#00897b",
    "parameter": "#ca8a04",
    "constraint": "#e65100",
}

COMMUNITIES = {
    "objective": (0, "目标函数"),
    "entity": (1, "业务实体"),
    "variable": (2, "决策变量"),
    "parameter": (3, "模型参数"),
    "constraint": (4, "约束条件"),
}

_CONFIDENCE_SCORES = {"EXTRACTED": 1.0, "INFERRED": 0.7, "AMBIGUOUS": 0.35}


def _node_id(category: str, source_id: str) -> str:
    return f"{category}:{source_id}"


def _normalize_confidence(value: Any) -> str:
    label = str(value or "INFERRED").upper()
    return label if label in _CONFIDENCE_SCORES else "INFERRED"


def _build_node(item: dict[str, Any], category: str, source_id: str) -> dict[str, Any]:
    community_id, community_name = COMMUNITIES[category]
    return {
        "id": _node_id(category, source_id),
        "source_id": source_id,
        "label": str(item.get("name") or source_id),
        "category": category,
        "type": str(item.get("type") or item.get("hardness") or category),
        "description": str(item.get("description") or item.get("expression") or ""),
        "community": community_id,
        "community_name": community_name,
        "color": COLORS[category],
        "degree": 0,
        "data": item,
    }


def build_graph(problem: dict[str, Any]) -> dict[str, Any]:
    graph: nx.DiGraph = nx.DiGraph()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    lookup: dict[str, str] = {}
    grouped: dict[str, list[str]] = defaultdict(list)  # category -> [node_id]

    def register(node: dict[str, Any]) -> str:
        node_id = node["id"]
        graph.add_node(node_id)
        nodes.append(node)
        grouped[node["category"]].append(node_id)
        lookup[node["source_id"]] = node_id
        lookup[node["label"]] = node_id
        return node_id

    # ---- Nodes ---------------------------------------------------------
    objective = problem.get("objective") or {}
    if not isinstance(objective, dict):
        objective = {"name": "目标函数", "expression": str(objective)}
    objective_node = _build_node(
        {
            **objective,
            "id": "OBJECTIVE",
            "name": objective.get("name") or "目标函数",
            "type": problem.get("objective_sense") or "objective",
        },
        "objective",
        "OBJECTIVE",
    )
    objective_id = register(objective_node)
    lookup["OBJECTIVE"] = objective_id

    for category, items in (
        ("entity", problem.get("entities") or []),
        ("variable", problem.get("decision_variables") or problem.get("variables") or []),
        ("parameter", problem.get("parameters") or []),
        ("constraint", problem.get("constraints") or []),
    ):
        for index, item in enumerate(items):
            payload = item if isinstance(item, dict) else {"name": str(item)}
            source_id = str(payload.get("id") or payload.get("name") or f"{category}_{index + 1}")
            register(_build_node(payload, category, source_id))

    # ---- Edges ---------------------------------------------------------
    seen: set[tuple[str, str, str]] = set()

    def add_edge(
        source: str | None,
        target: str | None,
        relation: str,
        *,
        confidence: str = "INFERRED",
        description: str = "",
        structural: bool = False,
    ) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, relation)
        if key in seen:
            return
        seen.add(key)
        label = _normalize_confidence(confidence)
        edge = {
            "id": f"edge:{len(edges) + 1}",
            "source": source,
            "target": target,
            "relation": relation,
            "description": description,
            "confidence": label,
            "confidence_score": _CONFIDENCE_SCORES[label],
            "structural": structural,
        }
        edges.append(edge)
        graph.add_edge(source, target)

    for relation in problem.get("relationships") or []:
        if isinstance(relation, dict):
            add_edge(
                lookup.get(str(relation.get("source") or "")),
                lookup.get(str(relation.get("target") or "")),
                str(relation.get("relation") or "关联"),
                confidence=str(relation.get("confidence") or "EXTRACTED"),
                description=str(relation.get("description") or ""),
            )

    for variable_id in grouped["variable"]:
        add_edge(variable_id, objective_id, "参与目标", structural=True)

    for constraint_id in grouped["constraint"]:
        constraint_node = next(item for item in nodes if item["id"] == constraint_id)
        coeffs = constraint_node["data"].get("coefficients") or {}
        matches = [vid for vid in grouped["variable"] if vid.split(":", 1)[1] in coeffs]
        for variable_id in matches:
            add_edge(constraint_id, variable_id, "约束", structural=True)
        if not matches:
            add_edge(constraint_id, objective_id, "限定可行域", structural=True)

    for parameter_id in grouped["parameter"]:
        add_edge(parameter_id, objective_id, "提供参数", structural=True)

    # ---- Degrees -------------------------------------------------------
    for node in nodes:
        node["degree"] = graph.degree(node["id"])

    # ---- Communities (fixed category grouping) ------------------------
    communities = [
        {
            "id": community_id,
            "category": category,
            "label": label,
            "color": COLORS[category],
            "node_count": len(grouped[category]),
        }
        for category, (community_id, label) in COMMUNITIES.items()
        if grouped[category]
    ]

    # ---- Hubs (top 5 by degree) ---------------------------------------
    hubs = sorted(
        (
            {
                "id": node["id"],
                "label": node["label"],
                "category": node["category"],
                "degree": node["degree"],
            }
            for node in nodes
        ),
        key=lambda item: (-item["degree"], item["label"]),
    )[:5]

    return {
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "hubs": hubs,
        "stats": {
            "node_count": graph.number_of_nodes(),
            "edge_count": graph.number_of_edges(),
            "community_count": len(communities),
            "isolated_count": sum(1 for node in nodes if node["degree"] == 0),
            "density": round(nx.density(graph), 4),
        },
    }
