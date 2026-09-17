from backend.core.generic_ai_solver import default_example
from backend.core.ontology_graph import build_graph


def test_graph_shape_matches_example():
    ontology = default_example()["preview"]
    graph = ontology["graph"]
    assert "nodes" in graph and "edges" in graph
    assert graph["stats"]["node_count"] == len(graph["nodes"])
    assert graph["stats"]["edge_count"] == len(graph["edges"])
    assert 0 <= graph["stats"]["density"] <= 1


def test_objective_node_is_present():
    ontology = default_example()["preview"]
    objectives = [n for n in ontology["graph"]["nodes"] if n["category"] == "objective"]
    assert len(objectives) == 1
    assert objectives[0]["id"].startswith("objective:")


def test_structural_edges_to_objective():
    ontology = default_example()["preview"]
    edges = ontology["graph"]["edges"]
    to_objective = [e for e in edges if e["structural"] and e["relation"] in {"参与目标", "提供参数"}]
    # at least variables and parameters point at the objective
    assert len(to_objective) >= 2


def test_empty_problem_yields_minimal_graph():
    graph = build_graph({"objective": {}, "entities": [], "decision_variables": [], "constraints": [], "parameters": []})
    assert graph["stats"]["node_count"] == 1
    assert graph["stats"]["edge_count"] == 0
    assert graph["stats"]["density"] == 0
