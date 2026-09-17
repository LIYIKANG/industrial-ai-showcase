"""GraphML + Cypher exporters for the ontology graph.

GraphML goes through ``networkx.write_graphml`` so the XML namespaces, key
declarations and edge encoding are produced by the standard library rather than
hand-rolled strings. Cypher has no equivalent off-the-shelf emitter that
preserves the exact CREATE / MATCH dialect this project ships, so the small
script-style output is still written directly.
"""

from __future__ import annotations

import io
import re
from typing import Any

import networkx as nx


def graphml_bytes(ontology: dict[str, Any] | None) -> bytes:
    graph_data = (ontology or {}).get("graph") or {}
    G = nx.DiGraph()
    for node in graph_data.get("nodes", []):
        G.add_node(
            str(node.get("id") or ""),
            label=str(node.get("label") or ""),
            category=str(node.get("category") or ""),
        )
    for edge in graph_data.get("edges", []):
        G.add_edge(
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
            relation=str(edge.get("relation") or ""),
            confidence=str(edge.get("confidence") or ""),
        )
    buffer = io.BytesIO()
    nx.write_graphml(G, buffer)
    return buffer.getvalue()


def _cypher_string(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _cypher_label(value: Any) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "OntologyNode"))
    return cleaned if cleaned and not cleaned[0].isdigit() else f"N_{cleaned}"


def cypher_bytes(ontology: dict[str, Any] | None) -> bytes:
    graph_data = (ontology or {}).get("graph") or {}
    variables: dict[str, str] = {}
    lines = ["// Generated locally by EFESO Operations AI Workbench"]
    for index, node in enumerate(graph_data.get("nodes", [])):
        variable = f"n{index}"
        variables[str(node.get("id"))] = variable
        label = _cypher_label(node.get("category"))
        lines.append(
            f"CREATE ({variable}:{label} {{id: '{_cypher_string(node.get('id'))}', "
            f"source_id: '{_cypher_string(node.get('source_id'))}', "
            f"name: '{_cypher_string(node.get('label'))}', "
            f"type: '{_cypher_string(node.get('type'))}'}});"
        )
    for edge in graph_data.get("edges", []):
        source = variables.get(str(edge.get("source")))
        target = variables.get(str(edge.get("target")))
        if not source or not target:
            continue
        relation = _cypher_label(edge.get("relation")).upper()
        lines.append(
            f"MATCH (a {{id: '{_cypher_string(edge.get('source'))}'}}), "
            f"(b {{id: '{_cypher_string(edge.get('target'))}'}}) "
            f"CREATE (a)-[:{relation} {{confidence: '{_cypher_string(edge.get('confidence'))}'}}]->(b);"
        )
    return "\n".join(lines).encode("utf-8")
