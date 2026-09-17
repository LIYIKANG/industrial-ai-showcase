from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from backend.core.model_validator import validate_model


def _finite(value: Any, default: float) -> float:
    if value in (None, "", "null"):
        return default
    number = float(value)
    return number if math.isfinite(number) else default


def _solve_highs(problem: dict[str, Any], time_limit: float) -> dict[str, Any]:
    from scipy.optimize import Bounds, LinearConstraint, milp

    variables = problem.get("decision_variables") or problem.get("variables") or []
    constraints = problem.get("constraints") or []
    ids = [str(item["id"]) for item in variables]
    index = {variable_id: position for position, variable_id in enumerate(ids)}
    objective = problem.get("objective") or {}
    objective_coeffs = objective.get("coefficients") or {}
    direction = str(problem.get("objective_sense") or "minimize").lower()
    c = np.array([float(objective_coeffs.get(variable_id, 0)) for variable_id in ids], dtype=float)
    solver_c = -c if direction == "maximize" else c

    lower: list[float] = []
    upper: list[float] = []
    integrality: list[int] = []
    for variable in variables:
        variable_type = str(variable.get("type") or "continuous").lower()
        if variable_type == "binary":
            lower.append(0)
            upper.append(1)
            integrality.append(1)
        else:
            lower.append(_finite(variable.get("lower_bound"), 0))
            upper.append(_finite(variable.get("upper_bound"), np.inf))
            integrality.append(1 if variable_type == "integer" else 0)

    rows: list[list[float]] = []
    row_lower: list[float] = []
    row_upper: list[float] = []
    for constraint in constraints:
        coeffs = constraint.get("coefficients") or {}
        rows.append([float(coeffs.get(variable_id, 0)) for variable_id in ids])
        rhs = float(constraint["rhs"])
        sense = constraint["sense"]
        if sense == "<=":
            row_lower.append(-np.inf)
            row_upper.append(rhs)
        elif sense == ">=":
            row_lower.append(rhs)
            row_upper.append(np.inf)
        else:
            row_lower.append(rhs)
            row_upper.append(rhs)

    linear_constraint = None
    if rows:
        linear_constraint = LinearConstraint(
            np.asarray(rows, dtype=float),
            np.asarray(row_lower, dtype=float),
            np.asarray(row_upper, dtype=float),
        )

    started = time.perf_counter()
    result = milp(
        c=solver_c,
        integrality=np.asarray(integrality, dtype=int),
        bounds=Bounds(np.asarray(lower), np.asarray(upper)),
        constraints=linear_constraint,
        options={"time_limit": time_limit, "mip_rel_gap": 0.0001},
    )
    elapsed = round(time.perf_counter() - started, 4)
    status_map = {
        0: "optimal",
        1: "limit_reached",
        2: "infeasible",
        3: "unbounded",
        4: "solver_error",
    }
    status = status_map.get(int(result.status), "unknown")
    values = result.x.tolist() if result.x is not None else []
    variable_rows = [
        {
            "id": variable_id,
            "name": variables[position].get("name") or variable_id,
            "value": round(float(values[position]), 8),
            "unit": variables[position].get("unit") or "",
            "type": variables[position].get("type") or "continuous",
        }
        for position, variable_id in enumerate(ids)
        if position < len(values)
    ]
    objective_constant = float(objective.get("constant") or 0)
    objective_value = None
    if values:
        objective_value = float(np.dot(c, np.asarray(values))) + objective_constant

    checks: list[dict[str, Any]] = []
    for constraint in constraints:
        coeffs = constraint.get("coefficients") or {}
        lhs = sum(float(coeffs.get(variable_id, 0)) * values[index[variable_id]] for variable_id in ids) if values else None
        rhs = float(constraint["rhs"])
        sense = constraint["sense"]
        tolerance = 1e-6
        if lhs is None:
            check_status = "UNKNOWN"
            slack = None
        elif sense == "<=":
            slack = rhs - lhs
            check_status = "OK" if lhs <= rhs + tolerance else "VIOLATED"
        elif sense == ">=":
            slack = lhs - rhs
            check_status = "OK" if lhs >= rhs - tolerance else "VIOLATED"
        else:
            slack = abs(lhs - rhs)
            check_status = "OK" if slack <= tolerance else "VIOLATED"
        checks.append(
            {
                "id": constraint.get("id") or "",
                "constraint": constraint.get("name") or constraint.get("id") or "约束",
                "sense": sense,
                "lhs": round(lhs, 8) if lhs is not None else None,
                "rhs": rhs,
                "slack": round(slack, 8) if slack is not None else None,
                "status": check_status,
            }
        )

    return {
        "status": status,
        "strict_solution": True,
        "engine": "SciPy HiGHS",
        "solver_family": "MILP" if any(integrality) else "LP",
        "privacy": "local",
        "objective_sense": direction,
        "objective_value": round(objective_value, 8) if objective_value is not None else None,
        "variables": variable_rows,
        "solution_rows": variable_rows,
        "constraints_check": checks,
        "elapsed_seconds": elapsed,
        "message": str(result.message),
        "mip_gap": getattr(result, "mip_gap", None),
        "node_count": getattr(result, "mip_node_count", None),
        "warnings": [],
    }


def _solve_cbc(problem: dict[str, Any], time_limit: float) -> dict[str, Any]:
    import pulp

    variables = problem.get("decision_variables") or problem.get("variables") or []
    objective = problem.get("objective") or {}
    direction = str(problem.get("objective_sense") or "minimize").lower()
    model = pulp.LpProblem(
        "Generic_Optimization",
        pulp.LpMaximize if direction == "maximize" else pulp.LpMinimize,
    )
    pulp_variables: dict[str, pulp.LpVariable] = {}
    for item in variables:
        variable_id = str(item["id"])
        variable_type = str(item.get("type") or "continuous").lower()
        category = {
            "continuous": pulp.LpContinuous,
            "integer": pulp.LpInteger,
            "binary": pulp.LpBinary,
        }[variable_type]
        lower = 0 if variable_type == "binary" else item.get("lower_bound")
        upper = 1 if variable_type == "binary" else item.get("upper_bound")
        pulp_variables[variable_id] = pulp.LpVariable(
            variable_id,
            lowBound=None if lower in (None, "") else float(lower),
            upBound=None if upper in (None, "") else float(upper),
            cat=category,
        )
    coefficients = objective.get("coefficients") or {}
    model += (
        pulp.lpSum(float(coefficients.get(variable_id, 0)) * variable for variable_id, variable in pulp_variables.items())
        + float(objective.get("constant") or 0)
    )
    for item in problem.get("constraints") or []:
        coeffs = item.get("coefficients") or {}
        expression = pulp.lpSum(
            float(coeffs.get(variable_id, 0)) * variable for variable_id, variable in pulp_variables.items()
        )
        rhs = float(item["rhs"])
        if item["sense"] == "<=":
            model += expression <= rhs, str(item.get("id") or item.get("name"))
        elif item["sense"] == ">=":
            model += expression >= rhs, str(item.get("id") or item.get("name"))
        else:
            model += expression == rhs, str(item.get("id") or item.get("name"))

    started = time.perf_counter()
    model.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    elapsed = round(time.perf_counter() - started, 4)
    raw_status = pulp.LpStatus.get(model.status, "Unknown")
    status = {
        "Optimal": "optimal",
        "Infeasible": "infeasible",
        "Unbounded": "unbounded",
        "Not Solved": "limit_reached",
    }.get(raw_status, "solver_error")
    variable_rows = [
        {
            "id": variable_id,
            "name": item.get("name") or variable_id,
            "value": round(float(pulp.value(pulp_variables[variable_id]) or 0), 8),
            "unit": item.get("unit") or "",
            "type": item.get("type") or "continuous",
        }
        for item in variables
        for variable_id in [str(item["id"])]
    ]
    value_map = {item["id"]: item["value"] for item in variable_rows}
    checks = []
    for item in problem.get("constraints") or []:
        lhs = sum(float(coef) * value_map.get(variable_id, 0) for variable_id, coef in (item.get("coefficients") or {}).items())
        rhs = float(item["rhs"])
        sense = item["sense"]
        slack = rhs - lhs if sense == "<=" else lhs - rhs if sense == ">=" else abs(lhs - rhs)
        ok = lhs <= rhs + 1e-6 if sense == "<=" else lhs >= rhs - 1e-6 if sense == ">=" else abs(lhs - rhs) <= 1e-6
        checks.append({
            "id": item.get("id") or "",
            "constraint": item.get("name") or item.get("id") or "约束",
            "sense": sense,
            "lhs": round(lhs, 8),
            "rhs": rhs,
            "slack": round(slack, 8),
            "status": "OK" if ok else "VIOLATED",
        })
    return {
        "status": status,
        "strict_solution": True,
        "engine": "PuLP CBC",
        "solver_family": "MILP" if any(item.get("type") in {"integer", "binary"} for item in variables) else "LP",
        "privacy": "local",
        "objective_sense": direction,
        "objective_value": round(float(pulp.value(model.objective)), 8) if model.objective is not None else None,
        "variables": variable_rows,
        "solution_rows": variable_rows,
        "constraints_check": checks,
        "elapsed_seconds": elapsed,
        "message": raw_status,
        "warnings": ["HiGHS 不可用，已自动回退到 CBC。"],
    }


def solve_local(problem: dict[str, Any], *, time_limit: float = 60) -> dict[str, Any]:
    validation = validate_model(problem)
    if not validation["valid"]:
        return {
            "status": "invalid_model",
            "strict_solution": False,
            "engine": None,
            "privacy": "local",
            "validation": validation,
            "warnings": ["模型校验失败，未调用求解器。"],
        }
    try:
        result = _solve_highs(problem, time_limit)
        result["validation"] = validation
        return result
    except Exception as highs_exc:
        try:
            result = _solve_cbc(problem, time_limit)
            result["validation"] = validation
            result["highs_error"] = str(highs_exc)
            return result
        except Exception as cbc_exc:
            error = f"HiGHS: {highs_exc}; CBC: {cbc_exc}"
        return {
            "status": "solver_error",
            "strict_solution": False,
            "engine": "SciPy HiGHS / PuLP CBC",
            "privacy": "local",
            "validation": validation,
            "error": error,
            "warnings": ["两个本地求解器均执行失败。"],
        }
