from __future__ import annotations

import json
import re
from typing import Any

from backend.core.ontology_graph import build_graph
from backend.core.ontology_tables import build_ontology_tables
from backend.services.llm_client import chat

DEFAULT_EXAMPLE_TEXT = """一家咨询公司的客户工厂计划生产 A、B 两种产品。
A 产品单位利润 40 元，需要 2 小时机器工时和 1 千克原料。
B 产品单位利润 55 元，需要 3 小时机器工时和 2 千克原料。
本周机器工时最多 120 小时，原料最多 80 千克。
市场要求 A 至少生产 10 件，B 至少生产 8 件。
目标是在满足资源和最低产量要求的前提下，使总利润最大。"""

DEFAULT_EXAMPLE_PROBLEM = {
    "title": "双产品生产计划优化",
    "domain": "生产计划 / 资源分配",
    "problem_type": "linear_programming",
    "objective_sense": "maximize",
    "objective": {
        "name": "总利润",
        "expression": "40*x_A + 55*x_B",
        "coefficients": {"x_A": 40, "x_B": 55},
        "constant": 0,
        "description": "最大化产品组合总利润",
    },
    "entities": [
        {
            "id": "A",
            "name": "产品 A",
            "type": "产品",
            "description": "客户工厂计划生产的产品",
            "source": "业务文本",
            "properties": {
                "单位利润": {"value": 40, "unit": "元/件"},
                "最低产量": {"value": 10, "unit": "件"},
            },
        },
        {
            "id": "B",
            "name": "产品 B",
            "type": "产品",
            "description": "客户工厂计划生产的产品",
            "source": "业务文本",
            "properties": {
                "单位利润": {"value": 55, "unit": "元/件"},
                "最低产量": {"value": 8, "unit": "件"},
            },
        },
        {
            "id": "machine",
            "name": "机器工时",
            "type": "资源",
            "description": "计划期内可用的机器工时",
            "properties": {"可用量": {"value": 120, "unit": "小时"}},
        },
        {
            "id": "material",
            "name": "原料",
            "type": "资源",
            "description": "计划期内可用的生产原料",
            "properties": {"可用量": {"value": 80, "unit": "千克"}},
        },
    ],
    "relationships": [
        {"source": "A", "relation": "消耗", "target": "machine", "confidence": "EXTRACTED"},
        {"source": "B", "relation": "消耗", "target": "machine", "confidence": "EXTRACTED"},
        {"source": "A", "relation": "消耗", "target": "material", "confidence": "EXTRACTED"},
        {"source": "B", "relation": "消耗", "target": "material", "confidence": "EXTRACTED"},
    ],
    "decision_variables": [
        {"id": "x_A", "name": "产品 A 产量", "type": "integer", "lower_bound": 10, "upper_bound": None, "unit": "件"},
        {"id": "x_B", "name": "产品 B 产量", "type": "integer", "lower_bound": 8, "upper_bound": None, "unit": "件"},
    ],
    "parameters": [
        {"id": "machine_limit", "name": "机器工时上限", "value": 120, "unit": "小时"},
        {"id": "material_limit", "name": "原料上限", "value": 80, "unit": "千克"},
    ],
    "constraints": [
        {
            "id": "C1",
            "name": "机器工时限制",
            "expression": "2*x_A + 3*x_B <= 120",
            "coefficients": {"x_A": 2, "x_B": 3},
            "sense": "<=",
            "rhs": 120,
            "hardness": "hard",
        },
        {
            "id": "C2",
            "name": "原料限制",
            "expression": "x_A + 2*x_B <= 80",
            "coefficients": {"x_A": 1, "x_B": 2},
            "sense": "<=",
            "rhs": 80,
            "hardness": "hard",
        },
    ],
    "solver_plan": {
        "recommended_method": "本地 HiGHS MILP",
        "solver_family": "MILP",
        "missing_data": [],
        "assumptions": ["利润和资源消耗在计划期内保持不变"],
    },
}

SYSTEM_PROMPT = """You are an optimization modeler for a consulting firm.
Return compact valid JSON only. Never solve the problem yourself.
Extract a linear LP/MILP model that can be executed by a local HiGHS solver.
Keep business labels in the source language."""


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("模型输出必须是 JSON 对象。")
    return parsed


def normalize(problem: dict[str, Any], raw_text: str, llm: dict[str, Any]) -> dict[str, Any]:
    variables = problem.get("decision_variables") or problem.get("variables") or []
    parameters = problem.get("parameters") or []
    constraints = problem.get("constraints") or []
    entities = problem.get("entities") or []
    relationships = problem.get("relationships") or []
    objective = problem.get("objective") or {}
    if not isinstance(objective, dict):
        objective = {"name": "目标函数", "expression": str(objective), "coefficients": {}}
        problem["objective"] = objective
    problem.setdefault("decision_variables", variables)
    problem.setdefault("parameters", parameters)
    problem.setdefault("constraints", constraints)
    problem.setdefault("entities", entities)
    problem.setdefault("relationships", relationships)
    graph = build_graph(problem)
    tables = build_ontology_tables(problem, graph)
    return {
        "summary": {
            "title": problem.get("title") or "通用优化问题",
            "domain": problem.get("domain") or "",
            "problem_type": problem.get("problem_type") or "",
            "objective_sense": problem.get("objective_sense") or "",
            "entity_count": len(entities),
            "variable_count": len(variables),
            "parameter_count": len(parameters),
            "constraint_count": len(constraints),
        },
        "problem": problem,
        "solver_input": problem,
        "problem_text": raw_text,
        "entities": tables["entities"],
        "attributes": tables["attributes"],
        "relationships": tables["relationships"],
        "tables": {
            "variables": tables["variables"],
            "parameters": tables["parameters"],
            "constraints": tables["constraints"],
        },
        "ontology_audit": tables["audit"],
        "graph": graph,
        "llm": llm,
    }


def default_example() -> dict[str, Any]:
    return {
        "title": DEFAULT_EXAMPLE_PROBLEM["title"],
        "domain": DEFAULT_EXAMPLE_PROBLEM["domain"],
        "text": DEFAULT_EXAMPLE_TEXT,
        "preview": {
            **normalize(
                json.loads(json.dumps(DEFAULT_EXAMPLE_PROBLEM, ensure_ascii=False)),
                DEFAULT_EXAMPLE_TEXT,
                {"provider": "sample", "model": "内置示例", "privacy": "local", "elapsed_seconds": 0},
            ),
            "is_preview": True,
        },
    }


def build_with_ai(
    text: str,
    *,
    domain: str | None = None,
    objective_hint: str | None = None,
    provider: str = "ollama",
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    source = re.sub(r"\n{3,}", "\n\n", (text or "").strip())[:16000]
    if not source:
        raise ValueError("请输入求解问题。")
    prompt = f"""Extract a strict linear optimization model.
Required top-level keys:
title, domain, problem_type, objective_sense, objective, entities, relationships,
decision_variables, parameters, constraints, solver_plan.

objective:
{{"name":"", "expression":"", "coefficients":{{"variable_id":number}}, "constant":0, "description":""}}

entities items:
{{"id":"", "name":"", "type":"", "description":"", "source":"problem text",
"confidence":"EXTRACTED|AMBIGUOUS", "properties":{{"property_name":{{"value":null,"unit":"","description":""}}}}}}

decision_variables items:
{{"id":"", "name":"", "type":"continuous|integer|binary", "lower_bound":number|null,
"upper_bound":number|null, "unit":"", "description":""}}

constraints items:
{{"id":"", "name":"", "expression":"", "coefficients":{{"variable_id":number}},
"sense":"<=|>=|=", "rhs":number, "hardness":"hard", "description":""}}

relationships items:
{{"source":"entity_id", "relation":"", "target":"entity_id", "description":"",
"confidence":"EXTRACTED|AMBIGUOUS"}}
Do not invent missing numeric values. Put missing items in solver_plan.missing_data.
If the problem cannot be represented as a linear model, still return the ontology,
but leave unsupported coefficients empty and explain why in solver_plan.

Domain hint: {domain or "unknown"}
Objective hint: {objective_hint or "unknown"}

Problem:
{source}"""
    response = chat(
        prompt,
        provider=provider,
        model=model,
        api_key=api_key,
        system=SYSTEM_PROMPT,
        json_mode=True,
        max_tokens=2200,
    )
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "模型调用失败。")
    parsed = _extract_json(response.get("content", ""))
    llm = {
        key: response.get(key)
        for key in ("provider", "privacy", "model", "base_url", "elapsed_seconds", "metrics")
    }
    return normalize(parsed, source, llm)
