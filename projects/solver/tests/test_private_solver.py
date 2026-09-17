from pathlib import Path

from backend.core.generic_ai_solver import default_example
from backend.core.local_solver import solve_local
from backend.core.model_validator import validate_model
from backend.core.ontology_export import cypher_bytes, graphml_bytes
from backend.core.project_store import ProjectStore


def test_example_is_strictly_solvable():
    problem = default_example()["preview"]["problem"]
    validation = validate_model(problem)
    result = solve_local(problem)

    assert validation["valid"] is True
    assert result["status"] == "optimal"
    assert result["strict_solution"] is True
    assert result["engine"] in {"SciPy HiGHS", "PuLP CBC"}
    assert result["objective_value"] == 2360
    assert all(item["status"] == "OK" for item in result["constraints_check"])


def test_invalid_model_is_not_sent_to_solver():
    problem = {
        "objective_sense": "maximize",
        "objective": {"coefficients": {"missing": 1}},
        "decision_variables": [{"id": "x", "type": "integer", "lower_bound": 5, "upper_bound": 1}],
        "constraints": [],
    }
    result = solve_local(problem)

    assert result["status"] == "invalid_model"
    assert result["strict_solution"] is False
    assert result["validation"]["errors"]


def test_project_versions_close_sqlite_connections(tmp_path: Path):
    store = ProjectStore(tmp_path / "projects.db")
    first = store.save("案例", {"version": 1})
    second = store.save("案例", {"version": 2}, first["id"])
    loaded = store.get(first["id"])

    assert second["version"] == 2
    assert loaded is not None
    assert loaded["payload"]["version"] == 2
    assert len(loaded["versions"]) == 2


def test_ontology_tables_keep_model_details():
    ontology = default_example()["preview"]

    assert any(row["属性分类"] == "决策变量" and row["下界"] == 10 for row in ontology["attributes"])
    assert any(row["属性分类"] == "目标系数" and row["属性名称"] == "x_A" for row in ontology["attributes"])
    assert any(row["属性分类"] == "约束系数" and row["属性名称"] == "x_B" for row in ontology["attributes"])
    assert any(row["是否推断"] == "是" for row in ontology["relationships"])
    assert ontology["ontology_audit"]["属性明细数"] == len(ontology["attributes"])


def test_graph_exports_are_generated():
    ontology = default_example()["preview"]
    graphml = graphml_bytes(ontology).decode("utf-8")
    cypher = cypher_bytes(ontology).decode("utf-8")

    assert "<graphml" in graphml
    assert "产品 A" in graphml
    assert "CREATE (n0" in cypher
    assert "MATCH (a" in cypher
