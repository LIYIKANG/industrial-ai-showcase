from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.core.exporter import excel_bytes, json_bytes
from backend.core.file_parser import parse_file_bytes
from backend.core.generic_ai_solver import build_with_ai, default_example, normalize
from backend.core.local_solver import solve_local
from backend.core.model_validator import validate_model
from backend.core.ontology_export import cypher_bytes, graphml_bytes
from backend.core.project_store import ProjectStore
from backend.services.llm_client import catalog, status as provider_status
from backend.services.task_manager import TaskManager

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

app = FastAPI(title="EFESO Operations AI Workbench", version="4.1")
app.mount("/static", StaticFiles(directory=str(ROOT / "frontend" / "static")), name="static")
from backend.services.approval import ApprovalService as _ApprovalService

projects = ProjectStore(DATA / "solver_projects.db")
approvals = _ApprovalService(projects)
tasks = TaskManager(workers=2)


@app.middleware("http")
async def refresh_demo_assets(request, call_next):
    response = await call_next(request)
    if request.url.path in ('/', '/solver') or request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


class AIConfig(BaseModel):
    provider: str = "ollama"
    model: str | None = None
    api_key: str | None = None


class AnalyzePayload(AIConfig):
    text: str = Field(..., min_length=1)
    domain: str | None = None
    objective_hint: str | None = None


class ValidatePayload(BaseModel):
    problem: dict[str, Any] = Field(default_factory=dict)


class SolvePayload(BaseModel):
    problem: dict[str, Any] = Field(default_factory=dict)
    time_limit: float = Field(default=60, ge=1, le=600)


class ExportPayload(BaseModel):
    ontology: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class ProjectPayload(BaseModel):
    id: str | None = None
    name: str = Field(default="未命名项目", max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


@app.get("/")
@app.get("/solver")
def solver_page():
    return FileResponse(
        ROOT / "frontend" / "solver.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/solver-assets/{filename}")
def solver_asset(filename: str):
    assets = {
        "solver.css": ("solver.css", "text/css"),
        "app.js": ("app.js", "application/javascript"),
    }
    item = assets.get(filename)
    if not item:
        raise HTTPException(404, "资源不存在。")
    return FileResponse(
        ROOT / "frontend" / "static" / item[0],
        media_type=item[1],
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": app.version,
        "privacy_mode": "local-first",
        "primary_solver": "SciPy HiGHS",
    }


@app.get("/api/solvers")
def solver_catalog():
    availability = {}
    for module in ("scipy", "pulp"):
        try:
            __import__(module)
            availability[module] = True
        except ImportError:
            availability[module] = False
    return {
        "privacy": "local",
        "default": "SciPy HiGHS",
        "fallback": "PuLP CBC",
        "availability": availability,
        "supports": ["LP", "MILP"],
        "routing": {
            "continuous": "HiGHS LP",
            "integer_or_binary": "HiGHS MILP",
            "fallback": "CBC",
        },
    }


@app.get("/api/generic/example")
def example():
    return default_example()


@app.get("/api/ai/providers")
def providers():
    return catalog()


@app.post("/api/ai/status")
def ai_status(payload: AIConfig):
    return provider_status(payload.provider, model=payload.model, api_key=payload.api_key)


@app.post("/api/generic/analyze")
def analyze(payload: AnalyzePayload):
    try:
        return build_with_ai(
            payload.text,
            domain=payload.domain,
            objective_hint=payload.objective_hint,
            provider=payload.provider,
            model=payload.model,
            api_key=payload.api_key,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/generic/upload-analyze")
async def upload_analyze(
    file: UploadFile = File(...),
    domain: str | None = Form(default=None),
    objective_hint: str | None = Form(default=None),
    provider: str = Form(default="ollama"),
    model: str | None = Form(default=None),
    api_key: str | None = Form(default=None),
):
    try:
        content = await file.read()
        text = parse_file_bytes(file.filename or "", content)
        ontology = build_with_ai(
            text,
            domain=domain,
            objective_hint=objective_hint,
            provider=provider,
            model=model,
            api_key=api_key,
        )
        return {"filename": file.filename, "text": text, "chars": len(text), "ontology": ontology}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/model/normalize")
def normalize_problem(payload: ValidatePayload):
    return normalize(payload.problem, "", {"provider": "manual", "model": "人工编辑", "privacy": "local"})


@app.post("/api/model/validate")
def validate(payload: ValidatePayload):
    return validate_model(payload.problem)


@app.post("/api/local-solve")
def local_solve(payload: SolvePayload):
    return solve_local(payload.problem, time_limit=payload.time_limit)


@app.post("/api/tasks/analyze")
def start_analyze(payload: AnalyzePayload):
    return tasks.submit(
        "analyze",
        lambda: build_with_ai(
            payload.text,
            domain=payload.domain,
            objective_hint=payload.objective_hint,
            provider=payload.provider,
            model=payload.model,
            api_key=payload.api_key,
        ),
    )


@app.post("/api/tasks/solve")
def start_solve(payload: SolvePayload):
    return tasks.submit("solve", lambda: solve_local(payload.problem, time_limit=payload.time_limit))


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    task = tasks.snapshot(task_id)
    if not task:
        raise HTTPException(404, "任务不存在。")
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    task = tasks.cancel(task_id)
    if not task:
        raise HTTPException(404, "任务不存在。")
    return task


@app.get("/api/projects")
def list_projects():
    return {"projects": projects.list()}


@app.post("/api/projects")
def save_project(payload: ProjectPayload):
    return projects.save(payload.name, payload.payload, payload.id)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, version: int | None = None):
    project = projects.get(project_id, version)
    if not project:
        raise HTTPException(404, "项目或版本不存在。")
    return project


@app.post("/api/export/json")
def export_json(payload: ExportPayload):
    return Response(
        content=json_bytes({"ontology": payload.ontology, "solver_result": payload.result}),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=private_optimization_project.json"},
    )


@app.post("/api/export/excel")
def export_excel(payload: ExportPayload):
    return Response(
        content=excel_bytes(payload.ontology, payload.result),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=private_optimization_project.xlsx"},
    )


@app.post("/api/export/graphml")
def export_graphml(payload: ExportPayload):
    return Response(
        content=graphml_bytes(payload.ontology),
        media_type="application/graphml+xml",
        headers={"Content-Disposition": "attachment; filename=ontology.graphml"},
    )


@app.post("/api/export/cypher")
def export_cypher(payload: ExportPayload):
    return Response(
        content=cypher_bytes(payload.ontology),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ontology.cypher"},
    )


# ---------------------------------------------------------------------------
# Chemical ATP layer (MVP-1) - 借鉴 SAP-BOP 算法的 ATP 可视化
# ---------------------------------------------------------------------------

from datetime import date as _date
from typing import Any as _Any
import json as _json
from fastapi import UploadFile as _UploadFile, File as _File, Form as _Form
from backend.core.chem_file_parser import (
    parse_materials as _parse_materials,
    parse_tanks as _parse_tanks,
    parse_inventory as _parse_inventory,
    parse_inbound as _parse_inbound,
    parse_open_orders as _parse_open_orders,
    parse_priorities as _parse_priorities,
    parse_bom as _parse_bom,
    parse_substitutes as _parse_substitutes,
)
from backend.core.chemical_ontology import build_chemical_ontology
from backend.services.chem_bridge import (
    _atp_to_solver_result,
    build_chem_ontology,
    build_chem_problem,
    build_chem_scenarios,
)
from backend.services.atp_service import compute_atp as _compute_atp
from backend.services.order_promising import promise_order as _promise_order
from backend.services.kit_check import batch_kit_check, check_kit, check_kit_with_substitutes
from backend.services.multi_objective import (
    allocate_under_gap as _allocate_under_gap,
    compare_strategies as _compare_strategies,
    rank_orders as _rank_orders,
)


class _ATPDatasetPayload(BaseModel):
    project_id: str | None = None
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    tanks: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    open_orders: list[dict[str, _Any]] = Field(default_factory=list)
    priorities: list[dict[str, _Any]] = Field(default_factory=list)
    bom: list[dict[str, _Any]] = Field(default_factory=list)
    substitutes: list[dict[str, _Any]] = Field(default_factory=list)


class _KitCheckPayload(BaseModel):
    project_id: str | None = None
    parent_material_id: str
    quantity: float = Field(gt=0)
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    bom: list[dict[str, _Any]] = Field(default_factory=list)
    substitutes: list[dict[str, _Any]] = Field(default_factory=list)
    use_substitutes: bool = True


class _KitBatchItem(BaseModel):
    parent_material_id: str
    quantity: float = Field(gt=0)


class _ATPScenariosPayload(BaseModel):
    project_id: str | None = None
    horizon_days: int = Field(default=30, ge=1, le=365)
    base_date: str | None = None
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    open_orders: list[dict[str, _Any]] = Field(default_factory=list)
    priorities: list[dict[str, _Any]] = Field(default_factory=list)


class _KitBatchPayload(BaseModel):
    project_id: str | None = None
    items: list[_KitBatchItem] = Field(default_factory=list)
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    bom: list[dict[str, _Any]] = Field(default_factory=list)
    substitutes: list[dict[str, _Any]] = Field(default_factory=list)
    use_substitutes: bool = True


class _ATPComputePayload(BaseModel):
    project_id: str | None = None
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    open_orders: list[dict[str, _Any]] = Field(default_factory=list)
    priorities: list[dict[str, _Any]] = Field(default_factory=list)
    horizon_days: int = Field(default=30, ge=1, le=180)
    base_date: str | None = None


class _ATPSnapshotPayload(BaseModel):
    name: str = Field(default="ATP 快照", max_length=120)
    project_id: str | None = None
    payload: dict[str, _Any] = Field(default_factory=dict)


class _PromisePayload(BaseModel):
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    open_orders: list[dict[str, _Any]] = Field(default_factory=list)
    request: dict[str, _Any]
    horizon_days: int = Field(default=30, ge=1, le=180)


class _RankPayload(BaseModel):
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    inventory: list[dict[str, _Any]] = Field(default_factory=list)
    inbound: list[dict[str, _Any]] = Field(default_factory=list)
    open_orders: list[dict[str, _Any]] = Field(default_factory=list)
    priorities: list[dict[str, _Any]] = Field(default_factory=list)
    strategy: str = Field(default="weighted_sum")
    horizon_days: int = Field(default=30, ge=1, le=180)


class _RankOptionsPayload(_RankPayload):
    weights: dict[str, float] | None = None
    epsilon: dict[str, float] | None = None


@app.post("/api/atp/import")
async def atp_import(payload: _ATPDatasetPayload):
    project_id = payload.project_id or "default"
    counts = projects.save_chem_dataset(
        project_id,
        materials=payload.materials,
        inventory=payload.inventory,
        inbound=payload.inbound,
        open_orders=payload.open_orders,
        priorities=payload.priorities,
        bom=payload.bom,
        substitutes=payload.substitutes,
    )
    # Build the standard project payload (ontology graph + math model shape)
    # so the generic 本体图谱 / 数学模型 / 求解结果 / 方案对比 tabs see the
    # chemical data through the same data path.
    problem = build_chem_problem(
        materials=payload.materials,
        inventory=payload.inventory,
        inbound=payload.inbound,
        open_orders=payload.open_orders,
        priorities=payload.priorities,
        bom=payload.bom,
        substitutes=payload.substitutes,
    )
    graph = build_chem_ontology(problem)
    # Merge BOM + substitute relationships already pushed into problem["relationships"]
    # (build_chemical_ontology handles base, bridge adds BOM/sub edges).
    project_payload = {
        "domain": "chemical",
        "problem_text": (
            f"化工 ATP 供需项目：{counts['materials']} 物料、{counts['open_orders']} 在单、"
            f"{counts['bom']} BOM 行、{counts['substitutes']} 替代料"
        ),
        "domain_label": "化工 / 供应链 ATP",
        "objective_hint": problem["objective"]["expression"],
        "ontology": {**normalize(problem, "", {"provider": "rule"}), "graph": graph, "is_preview": False},
        "math_model": {
            "objective": problem["objective"],
            "decision_variables": problem["decision_variables"],
            "constraints": problem["constraints"],
            "parameters": problem["parameters"],
        },
        "chem_meta": {
            "counts": counts,
            "horizon_days": 30,
            "base_date": None,
            "metadata": problem.get("metadata", {}),
        },
        "scenarios": [],
        "result": None,
        "validation": {"valid": True, "errors": [], "warnings": []},
    }
    projects.save(
        name=f"化工 ATP - {project_id}",
        payload=project_payload,
        project_id=project_id,
    )
    # Also return the bare ontology (for backward compatibility with the
    # existing ATP front-end which already consumes entities/relationships).
    ontology = {
        "entities": problem["entities"],
        "relationships": problem["relationships"],
    }
    return {
        "counts": counts,
        "ontology": ontology,
        "graph": {"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", []))},
        "project_id": project_id,
    }


@app.post("/api/atp/upload")
async def atp_upload(
    materials: _UploadFile = _File(default=None),
    inventory: _UploadFile = _File(default=None),
    inbound: _UploadFile = _File(default=None),
    open_orders: _UploadFile = _File(default=None),
    priorities: _UploadFile = _File(default=None),
    bom: _UploadFile = _File(default=None),
    substitutes: _UploadFile = _File(default=None),
    project_id: str = _Form(default="default"),
):
    # Upload the 7 chemical-domain tables and immediately build the
    # chemical-domain ontology + math model -- no generic LLM fallback.
    parsed: dict[str, list] = {}
    files = {
        "materials": materials,
        "inventory": inventory,
        "inbound": inbound,
        "open_orders": open_orders,
        "priorities": priorities,
        "bom": bom,
        "substitutes": substitutes,
    }
    parsers = {
        "materials": _parse_materials,
        "inventory": _parse_inventory,
        "inbound": _parse_inbound,
        "open_orders": _parse_open_orders,
        "priorities": _parse_priorities,
        "bom": _parse_bom,
        "substitutes": _parse_substitutes,
    }
    for kind, upload in files.items():
        if upload is None:
            parsed[kind] = []
            continue
        data = await upload.read()
        try:
            parsed[kind] = parsers[kind](upload.filename or "", data)
        except Exception as exc:
            raise HTTPException(400, f"{kind} 解析失败: {exc}") from exc
    counts = projects.save_chem_dataset(project_id, **parsed)

    # Build the chemical-domain ontology + math model from the uploaded
    # Excel so the user lands on a real domain model -- not a generic
    # LLM interpretation. This is the fast path that distinguishes the
    # chemical tabs from the generic solver.
    try:
        problem = build_chem_problem(
            materials=parsed["materials"],
            inventory=parsed["inventory"],
            inbound=parsed["inbound"],
            open_orders=parsed["open_orders"],
            priorities=parsed["priorities"],
            bom=parsed["bom"],
            substitutes=parsed["substitutes"],
        )
        graph = build_chem_ontology(problem)
        project_payload = {
            "domain": "chemical",
            "problem_text": (
                f"\u5de5\u5316 ATP \u4f9b\u9700\u9879\u76ee\uff1a{counts['materials']} \u7269\u6599\u3001"
                f"{counts['open_orders']} \u5728\u5355\u3001{counts['bom']} BOM \u884c\u3001"
                f"{counts['substitutes']} \u66ff\u4ee3\u6599"
            ),
            "domain_label": "\u5de5\u5316 / \u4f9b\u5e94\u94fe / ATP",
            "objective_hint": problem["objective"]["expression"],
            "ontology": {**normalize(problem, "", {"provider": "rule"}), "graph": graph, "is_preview": False},
            "math_model": {
                "objective": problem["objective"],
                "decision_variables": problem["decision_variables"],
                "constraints": problem["constraints"],
                "parameters": problem["parameters"],
            },
            "chem_meta": {
                "counts": counts,
                "horizon_days": 30,
                "base_date": None,
                "metadata": problem.get("metadata", {}),
            },
            "scenarios": [],
            "result": None,
            "validation": {"valid": True, "errors": [], "warnings": []},
        }
        projects.save(
            name=f"\u5de5\u5316 ATP - {project_id}",
            payload=project_payload,
            project_id=project_id,
        )
    except Exception as exc:
        return {
            "project_id": project_id,
            "counts": counts,
            "data": parsed,
            "rows": {k: len(v) for k, v in parsed.items()},
            "ontology_built": False,
            "ontology_error": str(exc),
        }
    return {
        "project_id": project_id,
        "counts": counts,
        "data": parsed,
        "rows": {k: len(v) for k, v in parsed.items()},
        "ontology_built": True,
        "graph": {
            "nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("edges", [])),
        },
    }


@app.post("/api/atp/compute")
def atp_compute(payload: _ATPComputePayload):
    if payload.project_id and not payload.materials:
        dataset = projects.load_chem_dataset(payload.project_id)
        materials = dataset["materials"]
        inventory = dataset["inventory"]
        inbound = dataset["inbound"]
        open_orders = dataset["open_orders"]
        priorities = dataset["priorities"]
    else:
        materials = payload.materials
        inventory = payload.inventory
        inbound = payload.inbound
        open_orders = payload.open_orders
        priorities = payload.priorities
    base_date = None
    if payload.base_date:
        try:
            base_date = _date.fromisoformat(payload.base_date)
        except ValueError as exc:
            raise HTTPException(400, f"base_date 格式错误: {exc}") from exc
    result = _compute_atp(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=open_orders,
        priorities=priorities,
        horizon_days=payload.horizon_days,
        base_date=base_date,
    )
    # Push the result into the project store so the generic 求解结果 tab
    # sees the chemical ATP allocation.
    project_id = payload.project_id or "default"
    if project_id:
        solver_result = _atp_to_solver_result(result, engine="ATP-SAP-BOP")
        try:
            projects.update_chem_payload(
                project_id,
                result=solver_result,
                validation={"valid": True, "errors": [], "warnings": []},
                chem_meta={
                    "horizon_days": payload.horizon_days,
                    "base_date": payload.base_date,
                    "ran_at": result.get("dates", [None])[0],
                },
            )
        except Exception as exc:  # noqa: BLE001
            # Bridge is best-effort; the ATP result is still returned.
            result.setdefault("bridge_warnings", []).append(
                f"项目仓库回写失败: {exc}"
            )
    return result


@app.get("/api/atp/material/{material_id}")
def atp_material(material_id: str, project_id: str | None = None, horizon_days: int = 30):
    dataset = projects.load_chem_dataset(project_id or "default")
    if not dataset["materials"]:
        raise HTTPException(404, "未找到该项目的化工主数据，请先调用 /api/atp/import")
    result = _compute_atp(
        materials=dataset["materials"],
        inventory=dataset["inventory"],
        inbound=dataset["inbound"],
        open_orders=dataset["open_orders"],
        priorities=dataset["priorities"],
        horizon_days=horizon_days,
    )
    for row in result["matrix"]:
        if row["material_id"] == material_id:
            return row
    raise HTTPException(404, f"物料 {material_id} 不在主数据中")


@app.get("/api/atp/alerts")
def atp_alerts(project_id: str | None = None, horizon_days: int = 30):
    dataset = projects.load_chem_dataset(project_id or "default")
    if not dataset["materials"]:
        return {"alerts": [], "summary": {"materials": 0, "alerts": 0}}
    result = _compute_atp(
        materials=dataset["materials"],
        inventory=dataset["inventory"],
        inbound=dataset["inbound"],
        open_orders=dataset["open_orders"],
        priorities=dataset["priorities"],
        horizon_days=horizon_days,
    )
    return {
        "alerts": result["alerts"],
        "summary": {
            "materials": result["summary"]["materials"],
            "alerts": len(result["alerts"]),
            "high_severity": sum(1 for a in result["alerts"] if a.get("severity") == "high"),
            "ots_overall": result["ots"]["overall"],
        },
    }


@app.post("/api/atp/snapshot")
def atp_save_snapshot(payload: _ATPSnapshotPayload):
    return projects.save_atp_snapshot(payload.name, payload.payload, project_id=payload.project_id)


@app.get("/api/atp/snapshots")
def atp_list_snapshots():
    return {"snapshots": projects.list_atp_snapshots()}


@app.get("/api/atp/snapshots/{snapshot_id}")
def atp_get_snapshot(snapshot_id: str):
    record = projects.get_atp_snapshot(snapshot_id)
    if not record:
        raise HTTPException(404, "快照不存在")
    return record


@app.post("/api/atp/kit-check")
def atp_kit_check(payload: _KitCheckPayload):
    materials = payload.materials
    inventory = payload.inventory
    inbound = payload.inbound
    bom = payload.bom
    substitutes = payload.substitutes
    if not materials or not bom:
        dataset = projects.load_chem_dataset(payload.project_id or "default")
        if not materials:
            materials = dataset["materials"]
        if not inventory:
            inventory = dataset["inventory"]
        if not inbound:
            inbound = dataset["inbound"]
        if not bom:
            bom = dataset.get("bom") or []
        if not substitutes:
            substitutes = dataset.get("substitutes") or []
    if payload.use_substitutes and substitutes:
        return check_kit_with_substitutes(
            parent_material_id=payload.parent_material_id,
            quantity=payload.quantity,
            bom=bom,
            inventory=inventory,
            inbound=inbound,
            materials=materials,
            substitutes=substitutes,
        )
    return check_kit(
        parent_material_id=payload.parent_material_id,
        quantity=payload.quantity,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
    )


@app.post("/api/atp/kit-check/batch")
def atp_kit_check_batch(payload: _KitBatchPayload):
    materials = payload.materials
    inventory = payload.inventory
    inbound = payload.inbound
    bom = payload.bom
    substitutes = payload.substitutes
    if not materials or not bom:
        dataset = projects.load_chem_dataset(payload.project_id or "default")
        if not materials:
            materials = dataset["materials"]
        if not inventory:
            inventory = dataset["inventory"]
        if not inbound:
            inbound = dataset["inbound"]
        if not bom:
            bom = dataset.get("bom") or []
        if not substitutes:
            substitutes = dataset.get("substitutes") or []
    items = [it.model_dump() for it in payload.items]
    return batch_kit_check(
        items=items,
        bom=bom,
        inventory=inventory,
        inbound=inbound,
        materials=materials,
        substitutes=substitutes,
        use_substitutes=payload.use_substitutes,
    )


@app.post("/api/atp/scenarios")
def atp_scenarios(payload: _ATPScenariosPayload):
    """Run a small set of canonical chemical scenarios and save them as
    ``project.payload.scenarios`` for the generic 方案对比 tab."""
    materials = payload.materials
    inventory = payload.inventory
    inbound = payload.inbound
    open_orders = payload.open_orders
    priorities = payload.priorities
    if not materials and payload.project_id:
        dataset = projects.load_chem_dataset(payload.project_id)
        materials = dataset["materials"]
        inventory = dataset["inventory"]
        inbound = dataset["inbound"]
        open_orders = dataset["open_orders"]
        priorities = dataset["priorities"]
    scenarios = build_chem_scenarios(
        materials=materials,
        inventory=inventory,
        inbound=inbound,
        open_orders=open_orders,
        priorities=priorities,
        horizon_days=payload.horizon_days,
        base_date=payload.base_date,
    )
    project_id = payload.project_id or "default"
    projects.update_chem_payload(
        project_id,
        scenarios=[
            {
                "name": s["name"],
                "flags": s["flags"],
                "result": s["result"],
                "elapsed_seconds": s["result"].get("elapsed_seconds"),
            }
            for s in scenarios
        ],
    )
    return {
        "project_id": project_id,
        "count": len(scenarios),
        "scenarios": [
            {
                "name": s["name"],
                "flags": s["flags"],
                "objective_value": s["result"].get("objective_value"),
                "status": s["result"].get("status"),
                "alerts": len(s["result"].get("alerts") or []),
                "elapsed_seconds": s["result"].get("elapsed_seconds"),
            }
            for s in scenarios
        ],
    }


@app.post("/api/atp/promise")
def atp_promise(payload: _PromisePayload):
    try:
        return _promise_order(
            materials=payload.materials,
            inventory=payload.inventory,
            inbound=payload.inbound,
            open_orders=payload.open_orders,
            request=payload.request,
            horizon_days=payload.horizon_days,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/atp/rank")
def atp_rank(payload: _RankOptionsPayload):
    return _rank_orders(
        materials=payload.materials,
        inventory=payload.inventory,
        inbound=payload.inbound,
        open_orders=payload.open_orders,
        priorities=payload.priorities,
        strategy=payload.strategy,
        weights=payload.weights,
        epsilon=payload.epsilon,
        horizon_days=payload.horizon_days,
    )


@app.post("/api/atp/rank/allocate")
def atp_rank_allocate(payload: _RankPayload):
    """Run a strategy and simulate order-by-order acceptance
    against a per-day ATP series. Returns the per-order delivery
    decision and a summary (promised / short / blocked counts)."""
    return _allocate_under_gap(
        materials=payload.materials,
        inventory=payload.inventory,
        inbound=payload.inbound,
        open_orders=payload.open_orders,
        priorities=payload.priorities,
        strategy=payload.strategy,
        horizon_days=payload.horizon_days,
    )


@app.post("/api/atp/rank/compare")
def atp_rank_compare(payload: _RankPayload):
    """Run all 4 strategies on the same input and return side-by-side KPIs.
    Each strategy is simulated through the supply-gap allocator so the
    comparison reflects what would actually be promised."""
    return _compare_strategies(
        materials=payload.materials,
        inventory=payload.inventory,
        inbound=payload.inbound,
        open_orders=payload.open_orders,
        priorities=payload.priorities,
        horizon_days=payload.horizon_days,
    )


class _ApprovalSubmitPayload(BaseModel):
    project_id: str = Field(default="default")
    order_id: str
    material_id: str
    quantity: float
    materials: list[dict[str, _Any]] = Field(default_factory=list)
    requested_by: str = Field(default="planner")


class _ApprovalDecisionPayload(BaseModel):
    request_id: str
    approver: str
    approver_role: str
    decision: str
    note: str = Field(default="")
    materials: list[dict[str, _Any]] = Field(default_factory=list)


@app.post("/api/atp/approval/submit")
def atp_approval_submit(payload: _ApprovalSubmitPayload):
    try:
        return approvals.submit(
        project_id=payload.project_id,
        order_id=payload.order_id,
        material_id=payload.material_id,
        quantity=payload.quantity,
        materials=payload.materials,
        requested_by=payload.requested_by,
    )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/atp/approval/decide")
def atp_approval_decide(payload: _ApprovalDecisionPayload):
    try:
        return approvals.decide(
            request_id=payload.request_id,
            approver=payload.approver,
            approver_role=payload.approver_role,
            decision=payload.decision,
            note=payload.note,
            materials=payload.materials,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except KeyError:
        raise HTTPException(404, f"approval request not found: {payload.request_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/atp/approval/pending")
def atp_approval_pending(project_id: str | None = None):
    return {"items": approvals.list_pending(project_id)}


@app.get("/api/atp/approval/history")
def atp_approval_history(project_id: str | None = None, limit: int = 200):
    return {"items": approvals.list_history(project_id, limit=limit)}



@app.get("/api/atp/demo")
def atp_demo():
    """Return the bundled demo dataset for one-click onboarding."""
    demo_dir = DATA / "atp_demo"
    if not demo_dir.exists():
        raise HTTPException(404, "示例数据未找到")
    parsed: dict[str, list] = {}
    files = {
        "materials": (demo_dir / "materials.csv", _parse_materials),
        "inventory": (demo_dir / "inventory.csv", _parse_inventory),
        "inbound": (demo_dir / "inbound.csv", _parse_inbound),
        "open_orders": (demo_dir / "open_orders.csv", _parse_open_orders),
        "priorities": (demo_dir / "priorities.csv", _parse_priorities),
        "bom": (demo_dir / "bom.csv", _parse_bom),
        "substitutes": (demo_dir / "substitutes.csv", _parse_substitutes),
    }
    for kind, (path, parser) in files.items():
        if path.exists():
            parsed[kind] = parser(str(path), path.read_bytes())
        else:
            parsed[kind] = []
    return {"project_id": "demo", "rows": {k: len(v) for k, v in parsed.items()}, "data": parsed}
