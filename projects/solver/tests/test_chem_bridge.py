from __future__ import annotations
"""Tests for the chem -> generic project bridge (方案一).

These cover:
* ``build_chem_problem`` returns the standard shape (entities, relationships,
  decision_variables, constraints, parameters, objective) consumable by
  ``backend.core.ontology_graph.build_graph``.
* ``_atp_to_solver_result`` correctly maps the raw ATP ``matrix/series``
  output into the ``solver_result`` shape the generic 求解结果 tab reads.
* ``build_chem_scenarios`` runs three canonical scenarios (baseline,
  no special control, no critical material) and persists them in
  ``project.payload.scenarios``.
* The bridge pushes chem data into the standard ``ProjectStore`` so the
  generic tabs see the chemical dataset.
"""

from backend.services.chem_bridge import (
    _atp_to_solver_result,
    build_chem_ontology,
    build_chem_problem,
    build_chem_scenarios,
)


def _demo_materials():
    return [
        {"id": "M1", "name": "原料 A", "category": "raw_material", "unit": "kg",
         "special_control_level": 0, "supply_risk": "single",
         "lead_time_days": 7, "safety_stock": 100, "supplier_id": "SUP1",
         "price_sensitive": False},
        {"id": "M2", "name": "中间体 X", "category": "intermediate", "unit": "kg",
         "special_control_level": 2, "supply_risk": "multiple",
         "lead_time_days": 7, "safety_stock": 50, "supplier_id": None,
         "price_sensitive": False},
    ]


def _demo_orders():
    return [
        {"id": "O1", "customer_id": "C1", "customer_tier": "premium",
         "material_id": "M1", "quantity": 50, "due_date": "2026-07-15"},
        {"id": "O2", "customer_id": "C2", "customer_tier": "base",
         "material_id": "M2", "quantity": 200, "due_date": "2026-07-15"},
    ]


def _demo_priorities():
    return [
        {"customer_tier": "premium", "material_category": "raw_material", "weight": 60},
        {"customer_tier": "base", "material_category": "intermediate", "weight": 8},
    ]


def test_build_chem_problem_shape_is_standard():
    problem = build_chem_problem(
        materials=_demo_materials(),
        inventory=[{"material_id": "M1", "quantity": 1000}],
        inbound=[],
        open_orders=_demo_orders(),
        priorities=_demo_priorities(),
        bom=[],
        substitutes=[],
    )
    # Standard shape consumed by ontology_graph.build_graph
    for key in ("objective", "entities", "relationships", "decision_variables",
                "parameters", "constraints"):
        assert key in problem, f"missing {key}"
    assert problem["objective_sense"] == "maximize"
    assert len(problem["decision_variables"]) == 2
    # Constraints include a capacity + isolation constraint (M1 is L0 isolated)
    cap_ids = {c["id"] for c in problem["constraints"]}
    assert any(c.startswith("cap_") for c in cap_ids)
    assert "iso_M1" in cap_ids  # M1 is L0 -> must be isolated


def test_build_chem_ontology_returns_graph():
    problem = build_chem_problem(
        materials=_demo_materials(),
        inventory=[{"material_id": "M1", "quantity": 1000}],
        inbound=[],
        open_orders=_demo_orders(),
        priorities=_demo_priorities(),
    )
    graph = build_chem_ontology(problem)
    assert "nodes" in graph
    assert "edges" in graph
    assert len(graph["nodes"]) > 0
    # The chemical graph should at least have an objective node + 2 entities
    cat = {n["category"] for n in graph["nodes"]}
    assert "objective" in cat
    assert "entity" in cat


def test_bom_and_substitutes_become_relationships():
    problem = build_chem_problem(
        materials=_demo_materials(),
        inventory=[],
        inbound=[],
        open_orders=[],
        bom=[{"parent_material_id": "M2", "child_material_id": "M1",
              "quantity_per_unit": 0.4, "unit": "kg", "yield_ratio": 0.95, "scrap_rate": 0.02}],
        substitutes=[{"material_id": "M1", "substitute_id": "M2", "priority": 1}],
    )
    rels = problem["relationships"]
    assert any(r.get("relation") == "BOM 子件" and r["source"] == "M2" and r["target"] == "M1" for r in rels)
    assert any(r.get("relation") == "可替代料" and r["source"] == "M1" and r["target"] == "M2" for r in rels)


def test_atp_to_solver_result_maps_matrix_and_series():
    raw = {
        "base_date": "2026-07-02",
        "horizon_days": 3,
        "matrix": [
            {"material_id": "M1", "name": "原料 A", "series": [
                {"date": "2026-07-02", "atp": 100, "level": "ok"},
                {"date": "2026-07-03", "atp": 80, "level": "watch"},
                {"date": "2026-07-04", "atp": 50, "level": "tight"},
            ], "orders_promised": 2, "orders_short": 1},
        ],
        "alerts": [
            {"material_id": "M1", "date": "2026-07-04", "atp": 0, "severity": "high",
             "message": "缺 50"},
        ],
        "ots": {"premium": 100, "comfort": 80, "base": 60, "overall": 80},
        "summary": {"materials": 1, "horizon_days": 3, "orders_total": 2,
                    "orders_promised": 2, "orders_short": 1, "overall_ots_pct": 80},
    }
    out = _atp_to_solver_result(raw)
    # One variable per day per material: 3 entries
    assert len(out["variables"]) == 3
    # One constraint check per material
    assert len(out["constraints_check"]) == 1
    cap = out["constraints_check"][0]
    assert cap["data"]["orders_short"] == 1
    assert cap["data"]["alerts"] == 1
    # Objective = weighted OTS: 100*1.0 + 80*0.6 + 60*0.3 = 100 + 48 + 18 = 166
    assert out["objective_value"] == 166.0
    assert out["metadata"]["summary"]["overall_ots_pct"] == 80


def test_build_chem_scenarios_returns_three_variants():
    scenarios = build_chem_scenarios(
        materials=_demo_materials(),
        inventory=[{"material_id": "M1", "quantity": 1000}, {"material_id": "M2", "quantity": 200}],
        inbound=[],
        open_orders=_demo_orders(),
        priorities=_demo_priorities(),
        horizon_days=5,
        base_date="2026-07-02",
    )
    assert len(scenarios) == 3
    names = {s["name"] for s in scenarios}
    assert "基线 (全部启用)" in names
    assert "关闭特控 buffer" in names
    assert "关闭关键物料 risk" in names
    for s in scenarios:
        # Each scenario should have a valid solver_result shape
        r = s["result"]
        assert "status" in r
        assert "objective_value" in r
        assert "variables" in r
        assert "constraints_check" in r


def test_chem_bridge_persists_into_project_store(tmp_path):
    """The bridge should push chem data into a project so the standard
    tabs see it."""
    from backend.core.project_store import ProjectStore
    from fastapi.testclient import TestClient
    from backend.main import app

    store = ProjectStore(tmp_path / "store.db")
    # Swap the global project_store in the app with the tmp one.
    import backend.main as _m
    _m.projects = store
    with TestClient(app) as client:
        problem = build_chem_problem(
            materials=_demo_materials(),
            inventory=[{"material_id": "M1", "quantity": 1000}],
            inbound=[],
            open_orders=_demo_orders(),
            priorities=_demo_priorities(),
        )
        graph = build_chem_ontology(problem)
        # Persist via the same path atp_import uses.
        store.save(
            name="bridge-test",
            payload={
                "domain": "chemical",
                "ontology": {"graph": graph, "is_preview": False},
                "math_model": {
                    "objective": problem["objective"],
                    "decision_variables": problem["decision_variables"],
                    "constraints": problem["constraints"],
                    "parameters": problem["parameters"],
                },
                "result": None,
                "scenarios": [],
            },
            project_id="bridge-test",
        )
        # Reload via the API to confirm shape.
        resp = client.get("/api/projects/bridge-test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["payload"]["domain"] == "chemical"
        assert len(body["payload"]["ontology"]["graph"]["nodes"]) > 0
        assert body["payload"]["math_model"]["decision_variables"][0]["id"].startswith("x_")



# ---------------------------------------------------------------------------
# 方案对比 tab 多场景渲染 — 端到端
# ---------------------------------------------------------------------------


def test_scenarios_persist_to_project_payload(tmp_path):
    """POST /api/atp/scenarios writes 3 scenarios to project.payload.scenarios
    with the shape that renderMultiScenarioCompare consumes: each scenario has
    {name, flags, result, elapsed_seconds} and result carries
    metadata.{summary, ots, materials}.
    """
    import backend.main as _m
    from backend.core.project_store import ProjectStore
    from fastapi.testclient import TestClient

    store = ProjectStore(tmp_path / "store.db")
    _m.projects = store

    with TestClient(_m.app) as client:
        problem = build_chem_problem(
            materials=_demo_materials(),
            inventory=[{"material_id": "M1", "quantity": 1000}],
            inbound=[],
            open_orders=_demo_orders(),
            priorities=_demo_priorities(),
        )
        graph = build_chem_ontology(problem)
        store.save(
            name="bridge-test",
            payload={
                "domain": "chemical",
                "ontology": {"graph": graph, "is_preview": False},
                "math_model": {
                    "objective": problem["objective"],
                    "decision_variables": problem["decision_variables"],
                    "constraints": problem["constraints"],
                    "parameters": problem["parameters"],
                },
                "result": None,
                "scenarios": [],
            },
            project_id="bridge-test",
        )

        resp = client.post(
            "/api/atp/scenarios",
            json={"project_id": "bridge-test", "base_date": "2026-07-02"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 3
        assert len(body["scenarios"]) == 3

        p = client.get("/api/projects/bridge-test").json()
        scen = p["payload"]["scenarios"]
        assert len(scen) == 3, f"expected 3 scenarios, got {len(scen)}"
        names = [s["name"] for s in scen]
        assert "基线 (全部启用)" in names
        assert "关闭特控 buffer" in names
        assert "关闭关键物料 risk" in names

        for s in scen:
            assert "result" in s
            r = s["result"]
            assert r.get("status") in ("optimal", "feasible")
            assert isinstance(r.get("objective_value"), (int, float))
            assert isinstance(r.get("variables"), list)
            assert isinstance(r.get("constraints_check"), list)
            md = r["metadata"]
            assert "summary" in md
            assert "ots" in md
            assert "materials" in md
            assert "orders_promised" in md["summary"]
            assert "orders_short" in md["summary"]
            assert "overall" in md["ots"]


def test_scenario_variants_change_alerts(tmp_path):
    """The 关闭特控 buffer scenario must show a different alert count from
    the baseline when the demo dataset (which contains SC L1 items) is used.
    Proves the scenario actually toggles a domain rule.
    """
    import backend.main as _m
    from backend.core.project_store import ProjectStore
    from backend.core.chem_file_parser import (
        parse_inbound, parse_inventory, parse_materials, parse_open_orders,
        parse_priorities, parse_bom, parse_substitutes,
    )
    from fastapi.testclient import TestClient

    store = ProjectStore(tmp_path / "store.db")
    _m.projects = store

    # Use the real demo dataset (it has 30 materials, 50 orders, L1 SC items).
    with open("data/atp_demo/materials.csv", "rb") as f:
        materials = parse_materials("materials.csv", f.read())
    with open("data/atp_demo/inventory.csv", "rb") as f:
        inventory = parse_inventory("inventory.csv", f.read())
    with open("data/atp_demo/inbound.csv", "rb") as f:
        inbound = parse_inbound("inbound.csv", f.read())
    with open("data/atp_demo/open_orders.csv", "rb") as f:
        orders = parse_open_orders("open_orders.csv", f.read())
    with open("data/atp_demo/priorities.csv", "rb") as f:
        priorities = parse_priorities("priorities.csv", f.read())
    with open("data/atp_demo/bom.csv", "rb") as f:
        bom = parse_bom("bom.csv", f.read())
    with open("data/atp_demo/substitutes.csv", "rb") as f:
        subs = parse_substitutes("substitutes.csv", f.read())

    with TestClient(_m.app) as client:
        problem = build_chem_problem(
            materials=materials,
            inventory=inventory,
            inbound=inbound,
            open_orders=orders,
            priorities=priorities,
            bom=bom,
            substitutes=subs,
        )
        # Push through /api/atp/import so the chem_* tables are populated
        # (the scenarios endpoint reads from those tables).
        imp = client.post("/api/atp/import", json={
            "project_id": "bridge-demo",
            "materials": materials,
            "inventory": inventory,
            "inbound": inbound,
            "open_orders": orders,
            "priorities": priorities,
            "bom": bom,
            "substitutes": subs,
        })
        assert imp.status_code == 200, f"import failed: {imp.text}"
        s = client.post("/api/atp/scenarios", json={"project_id": "bridge-demo", "base_date": "2026-07-02"})
        assert s.status_code == 200, f"scenarios failed: {s.text}"
        p = client.get("/api/projects/bridge-demo").json()
        scen_by_name = {s["name"]: s for s in p["payload"]["scenarios"]}
        base = scen_by_name["基线 (全部启用)"]["result"]
        no_sc = scen_by_name["关闭特控 buffer"]["result"]
        base_alerts = len(base["alerts"])
        no_sc_alerts = len(no_sc["alerts"])
        # Demo has L1 SC items; disabling the SC buffer should lower alerts.
        assert no_sc_alerts < base_alerts, (
            f"disabling the SC buffer should not increase alerts: "
            f"base={base_alerts} off={no_sc_alerts}"
        )


# ---------------------------------------------------------------------------
# 化工领域快路径：上传 Excel → 立即建化工领域本体
# ---------------------------------------------------------------------------


def test_atp_upload_builds_chem_ontology_immediately(tmp_path):
    """POST /api/atp/upload with the 7 chemical-domain CSVs must build the
    chemical-domain ontology + math model in the same request — no need
    for a separate /api/atp/compute call before the 4 generic tabs see it.
    This is the fast path that distinguishes the chemical tabs from the
    generic LLM-driven solver.
    """
    import backend.main as _m
    from backend.core.project_store import ProjectStore
    from fastapi.testclient import TestClient

    store = ProjectStore(tmp_path / "store.db")
    _m.projects = store

    with TestClient(_m.app) as client:
        files = {
            "materials": ("materials.csv", open("data/atp_demo/materials.csv", "rb").read(), "text/csv"),
            "inventory": ("inventory.csv", open("data/atp_demo/inventory.csv", "rb").read(), "text/csv"),
            "inbound": ("inbound.csv", open("data/atp_demo/inbound.csv", "rb").read(), "text/csv"),
            "open_orders": ("open_orders.csv", open("data/atp_demo/open_orders.csv", "rb").read(), "text/csv"),
            "priorities": ("priorities.csv", open("data/atp_demo/priorities.csv", "rb").read(), "text/csv"),
            "bom": ("bom.csv", open("data/atp_demo/bom.csv", "rb").read(), "text/csv"),
            "substitutes": ("substitutes.csv", open("data/atp_demo/substitutes.csv", "rb").read(), "text/csv"),
        }
        resp = client.post(
            "/api/atp/upload",
            data={"project_id": "chemfast"},
            files=files,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ontology_built"] is True
        assert body["graph"]["nodes"] > 0
        assert body["graph"]["edges"] > 0
        # Counts must reflect the real demo.
        assert body["counts"]["materials"] == 30
        assert body["counts"]["open_orders"] == 50
        assert body["counts"]["bom"] == 28
        assert body["counts"]["substitutes"] == 8

        # The project must already carry the chemical-domain payload
        # (ontology + math_model), no follow-up compute required.
        p = client.get("/api/projects/chemfast").json()
        pl = p["payload"]
        assert pl["domain"] == "chemical"
        # graph.nodes is a list of node dicts; graph.edges likewise
        assert isinstance(pl["ontology"]["graph"]["nodes"], list)
        assert len(pl["ontology"]["graph"]["nodes"]) > 0
        assert isinstance(pl["ontology"]["graph"]["edges"], list)
        assert len(pl["ontology"]["graph"]["edges"]) > 0
        assert len(pl["math_model"]["decision_variables"]) > 0
        assert len(pl["math_model"]["constraints"]) > 0
        assert pl["chem_meta"]["counts"]["materials"] == 30
