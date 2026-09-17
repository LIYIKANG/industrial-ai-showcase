from __future__ import annotations

import json
from typing import Any


def _text(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _entity_rows(problem: dict[str, Any], graph: dict[str, Any]) -> list[dict[str, Any]]:
    degree = {
        node["source_id"]: node.get("degree", 0)
        for node in graph.get("nodes", [])
        if node.get("category") == "entity"
    }
    rows = []
    for index, entity in enumerate(problem.get("entities") or []):
        item = entity if isinstance(entity, dict) else {"name": str(entity)}
        entity_id = str(item.get("id") or item.get("name") or f"entity_{index + 1}")
        properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        rows.append(
            {
                "实体ID": entity_id,
                "实体名称": item.get("name") or entity_id,
                "实体类型": item.get("type") or "业务实体",
                "说明": item.get("description") or "",
                "属性数量": len(properties),
                "关系数量": degree.get(entity_id, 0),
                "来源": item.get("source") or "AI抽取",
                "置信度": item.get("confidence") or "EXTRACTED",
            }
        )
    return rows


def _attribute_rows(problem: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        owner_id: str,
        owner_name: str,
        category: str,
        name: str,
        value: Any,
        *,
        unit: str = "",
        data_type: str = "",
        lower: Any = "",
        upper: Any = "",
        source: str = "模型结构",
        description: str = "",
    ) -> None:
        rows.append(
            {
                "所属对象ID": owner_id,
                "所属对象名称": owner_name,
                "属性分类": category,
                "属性名称": name,
                "属性值": _text(value),
                "单位": unit,
                "数据类型": data_type,
                "下界": lower,
                "上界": upper,
                "来源": source,
                "说明": description,
            }
        )

    for index, entity in enumerate(problem.get("entities") or []):
        item = entity if isinstance(entity, dict) else {"name": str(entity)}
        owner_id = str(item.get("id") or item.get("name") or f"entity_{index + 1}")
        owner_name = str(item.get("name") or owner_id)
        properties = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        for name, value in properties.items():
            if isinstance(value, dict):
                add(
                    owner_id,
                    owner_name,
                    "实体属性",
                    str(name),
                    value.get("value"),
                    unit=str(value.get("unit") or ""),
                    data_type=str(value.get("type") or type(value.get("value")).__name__),
                    source=str(value.get("source") or item.get("source") or "AI抽取"),
                    description=str(value.get("description") or ""),
                )
            else:
                add(owner_id, owner_name, "实体属性", str(name), value, data_type=type(value).__name__, source="AI抽取")

    for index, variable in enumerate(problem.get("decision_variables") or problem.get("variables") or []):
        item = variable if isinstance(variable, dict) else {"name": str(variable)}
        owner_id = str(item.get("id") or item.get("name") or f"variable_{index + 1}")
        owner_name = str(item.get("name") or owner_id)
        add(
            owner_id,
            owner_name,
            "决策变量",
            "变量定义",
            item.get("type") or "continuous",
            unit=str(item.get("unit") or ""),
            data_type=str(item.get("type") or "continuous"),
            lower=item.get("lower_bound"),
            upper=item.get("upper_bound"),
            description=str(item.get("description") or ""),
        )

    for index, parameter in enumerate(problem.get("parameters") or []):
        item = parameter if isinstance(parameter, dict) else {"value": parameter}
        owner_id = str(item.get("id") or item.get("name") or f"parameter_{index + 1}")
        owner_name = str(item.get("name") or owner_id)
        add(
            owner_id,
            owner_name,
            "模型参数",
            "参数值",
            item.get("value"),
            unit=str(item.get("unit") or ""),
            data_type=str(item.get("type") or type(item.get("value")).__name__),
            source=str(item.get("source") or "业务输入"),
            description=str(item.get("description") or ""),
        )

    objective = problem.get("objective") if isinstance(problem.get("objective"), dict) else {}
    objective_name = str(objective.get("name") or "目标函数")
    add(
        "OBJECTIVE",
        objective_name,
        "目标函数",
        "表达式",
        objective.get("expression") or "",
        source="数学模型",
        description=str(objective.get("description") or ""),
    )
    add("OBJECTIVE", objective_name, "目标函数", "常数项", objective.get("constant", 0), data_type="number")
    for variable_id, coefficient in (objective.get("coefficients") or {}).items():
        add(
            "OBJECTIVE",
            objective_name,
            "目标系数",
            str(variable_id),
            coefficient,
            data_type="number",
            description=f"变量 {variable_id} 在目标函数中的系数",
        )

    for index, constraint in enumerate(problem.get("constraints") or []):
        item = constraint if isinstance(constraint, dict) else {"expression": str(constraint)}
        owner_id = str(item.get("id") or item.get("name") or f"constraint_{index + 1}")
        owner_name = str(item.get("name") or owner_id)
        add(
            owner_id,
            owner_name,
            "约束条件",
            "约束定义",
            item.get("expression") or "",
            data_type=str(item.get("hardness") or "hard"),
            lower=item.get("sense") if item.get("sense") in {">=", "="} else "",
            upper=item.get("rhs") if item.get("sense") in {"<=", "="} else "",
            description=str(item.get("description") or ""),
        )
        add(owner_id, owner_name, "约束条件", "比较符", item.get("sense") or "", data_type="operator")
        add(owner_id, owner_name, "约束条件", "右端值", item.get("rhs"), data_type="number")
        for variable_id, coefficient in (item.get("coefficients") or {}).items():
            add(
                owner_id,
                owner_name,
                "约束系数",
                str(variable_id),
                coefficient,
                data_type="number",
                description=f"变量 {variable_id} 在约束 {owner_name} 中的系数",
            )

    plan = problem.get("solver_plan") if isinstance(problem.get("solver_plan"), dict) else {}
    for key, label in (
        ("recommended_method", "推荐求解方法"),
        ("solver_family", "求解器类别"),
        ("missing_data", "缺失数据"),
        ("assumptions", "建模假设"),
    ):
        if key in plan:
            add("SOLVER_PLAN", "求解计划", "求解配置", label, plan.get(key), source="建模审计")
    return rows


def _relationship_rows(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {node.get("id"): node for node in graph.get("nodes", [])}
    rows = []
    for edge in graph.get("edges", []):
        source = nodes.get(edge.get("source"), {})
        target = nodes.get(edge.get("target"), {})
        inferred = bool(edge.get("structural")) or edge.get("confidence") == "INFERRED"
        rows.append(
            {
                "关系ID": edge.get("id"),
                "实体1ID": source.get("source_id") or edge.get("source"),
                "实体1名称": source.get("label") or edge.get("source"),
                "关系": edge.get("relation") or "关联",
                "实体2ID": target.get("source_id") or edge.get("target"),
                "实体2名称": target.get("label") or edge.get("target"),
                "关系描述/约束": edge.get("description") or "",
                "置信度": edge.get("confidence") or "INFERRED",
                "置信度分数": edge.get("confidence_score"),
                "来源": "模型结构推断" if inferred else "业务文本抽取",
                "是否推断": "是" if inferred else "否",
            }
        )
    return rows


def build_ontology_tables(problem: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    entities = _entity_rows(problem, graph)
    attributes = _attribute_rows(problem)
    relationships = _relationship_rows(graph)
    variables = problem.get("decision_variables") or problem.get("variables") or []
    parameters = problem.get("parameters") or []
    constraints = problem.get("constraints") or []
    plan = problem.get("solver_plan") if isinstance(problem.get("solver_plan"), dict) else {}
    missing_descriptions = sum(1 for row in entities if not row["说明"])
    audit = {
        "完整性评分": round(
            100
            * sum(
                (
                    bool(entities),
                    bool(variables),
                    bool(problem.get("objective")),
                    bool(constraints),
                    not bool(plan.get("missing_data")),
                )
            )
            / 5
        ),
        "实体数": len(entities),
        "属性明细数": len(attributes),
        "关系数": len(relationships),
        "推断关系数": sum(1 for row in relationships if row["是否推断"] == "是"),
        "歧义关系数": sum(1 for row in relationships if row["置信度"] == "AMBIGUOUS"),
        "孤立节点数": graph.get("stats", {}).get("isolated_count", 0),
        "缺少说明的实体数": missing_descriptions,
        "缺失数据": plan.get("missing_data") or [],
        "建模假设": plan.get("assumptions") or [],
    }
    return {
        "entities": entities,
        "attributes": attributes,
        "relationships": relationships,
        "variables": variables,
        "parameters": parameters,
        "constraints": constraints,
        "audit": audit,
    }
