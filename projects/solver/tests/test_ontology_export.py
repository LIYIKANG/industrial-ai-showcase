from backend.core.generic_ai_solver import default_example
from backend.core.ontology_export import cypher_bytes, graphml_bytes


def test_graphml_is_valid_xml_envelope():
    payload = graphml_bytes(default_example()["preview"]).decode("utf-8")
    assert payload.startswith("<?xml")
    assert "<graphml" in payload
    assert "</graphml>" in payload


def test_graphml_contains_node_label():
    payload = graphml_bytes(default_example()["preview"]).decode("utf-8")
    assert "产品 A" in payload


def test_cypher_creates_node_and_match_pairs():
    payload = cypher_bytes(default_example()["preview"]).decode("utf-8")
    assert "CREATE (n0" in payload
    assert "MATCH (a" in payload


def test_handles_missing_graph():
    # networkx-based emitter still returns a valid (empty) GraphML envelope for
    # an unknown graph - the file opens cleanly in any GraphML viewer.
    payload = graphml_bytes(None).decode("utf-8")
    assert payload.startswith("<?xml")
    assert "<graphml" in payload
    assert "<graph" in payload
    assert "</graphml>" in payload
    # Cypher keeps its hand-rolled header fallback.
    assert cypher_bytes(None) == b"// Generated locally by EFESO Operations AI Workbench"