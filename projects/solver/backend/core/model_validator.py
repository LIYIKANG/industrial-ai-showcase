from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def validate_model(problem: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    variables = problem.get("decision_variables") or problem.get("variables") or []
    constraints = problem.get("constraints") or []
    objective = problem.get("objective") or {}
    if not isinstance(objective, dict):
        objective = {"expression": str(objective)}

    ids: list[str] = []
    for index, variable in enumerate(variables):
        path = f"decision_variables[{index}]"
        if not isinstance(variable, dict):
            errors.append({"path": path, "message": "变量必须是对象。"})
            continue
        variable_id = str(variable.get("id") or "").strip()
        if not variable_id:
            errors.append({"path": f"{path}.id", "message": "变量缺少唯一 ID。"})
            continue
        if variable_id in ids:
            errors.append({"path": f"{path}.id", "message": f"变量 ID 重复：{variable_id}"})
        ids.append(variable_id)
        variable_type = str(variable.get("type") or "continuous").lower()
        if variable_type not in {"continuous", "integer", "binary"}:
            errors.append({"path": f"{path}.type", "message": "变量类型必须是 continuous、integer 或 binary。"})
        lower = _number(variable.get("lower_bound"))
        upper = _number(variable.get("upper_bound"))
        if lower is not None and upper is not None and lower > upper:
            errors.append({"path": path, "message": "变量下界不能大于上界。"})

    if not ids:
        errors.append({"path": "decision_variables", "message": "至少需要一个决策变量。"})

    coefficients = objective.get("coefficients")
    if not isinstance(coefficients, dict):
        errors.append({"path": "objective.coefficients", "message": "目标函数缺少结构化系数字典。"})
    else:
        unknown = sorted(set(coefficients) - set(ids))
        if unknown:
            errors.append({"path": "objective.coefficients", "message": f"目标函数引用未知变量：{', '.join(unknown)}"})
        missing = sorted(set(ids) - set(coefficients))
        if missing:
            warnings.append({"path": "objective.coefficients", "message": f"以下变量目标系数默认为 0：{', '.join(missing)}"})

    sense = str(problem.get("objective_sense") or "").lower()
    if sense not in {"maximize", "minimize"}:
        errors.append({"path": "objective_sense", "message": "目标方向必须是 maximize 或 minimize。"})

    for index, constraint in enumerate(constraints):
        path = f"constraints[{index}]"
        if not isinstance(constraint, dict):
            errors.append({"path": path, "message": "约束必须是对象。"})
            continue
        coeffs = constraint.get("coefficients")
        if not isinstance(coeffs, dict) or not coeffs:
            errors.append({"path": f"{path}.coefficients", "message": "约束缺少结构化系数。"})
            continue
        unknown = sorted(set(coeffs) - set(ids))
        if unknown:
            errors.append({"path": f"{path}.coefficients", "message": f"约束引用未知变量：{', '.join(unknown)}"})
        relation = str(constraint.get("sense") or "").strip()
        if relation not in {"<=", ">=", "="}:
            errors.append({"path": f"{path}.sense", "message": "约束方向必须是 <=、>= 或 =。"})
        if _number(constraint.get("rhs")) is None:
            errors.append({"path": f"{path}.rhs", "message": "约束右端值必须是有限数值。"})

    if not constraints:
        warnings.append({"path": "constraints", "message": "模型没有约束，可能产生无界解。"})

    solver_family = "MILP" if any(
        str(item.get("type") or "").lower() in {"integer", "binary"}
        for item in variables
        if isinstance(item, dict)
    ) else "LP"
    return {
        "valid": not errors,
        "ready_for_local_solver": not errors,
        "solver_family": solver_family,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "variables": len(variables),
            "constraints": len(constraints),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }
