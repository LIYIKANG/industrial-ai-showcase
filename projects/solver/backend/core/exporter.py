from __future__ import annotations

import io
import json

import pandas as pd


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def excel_bytes(ontology=None, result=None):
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        if ontology:
            pd.DataFrame(ontology.get("entities", [])).to_excel(writer, sheet_name="实体表", index=False)
            pd.DataFrame(ontology.get("attributes", [])).to_excel(writer, sheet_name="属性表", index=False)
            pd.DataFrame(ontology.get("relationships", [])).to_excel(writer, sheet_name="关系表", index=False)
            pd.DataFrame([ontology.get("summary", {})]).to_excel(writer, sheet_name="项目摘要", index=False)
            pd.DataFrame([ontology.get("ontology_audit", {})]).to_excel(writer, sheet_name="建模审计", index=False)
            problem = ontology.get("problem") or ontology.get("solver_input") or {}
            tables = ontology.get("tables") or {}
            pd.DataFrame(tables.get("variables") or problem.get("decision_variables", [])).to_excel(
                writer, sheet_name="决策变量", index=False
            )
            pd.DataFrame(tables.get("parameters") or problem.get("parameters", [])).to_excel(
                writer, sheet_name="模型参数", index=False
            )
            pd.DataFrame(tables.get("constraints") or problem.get("constraints", [])).to_excel(
                writer, sheet_name="约束条件", index=False
            )
            pd.DataFrame([problem.get("objective", {})]).to_excel(writer, sheet_name="目标函数", index=False)
        if result:
            summary = {
                key: value
                for key, value in result.items()
                if key not in {"variables", "solution_rows", "constraints_check", "validation"}
            }
            pd.DataFrame([summary]).to_excel(writer, sheet_name="求解摘要", index=False)
            pd.DataFrame(result.get("variables", [])).to_excel(writer, sheet_name="求解变量", index=False)
            pd.DataFrame(result.get("constraints_check", [])).to_excel(writer, sheet_name="约束校验", index=False)
            validation = result.get("validation") or {}
            pd.DataFrame(validation.get("errors", []) + validation.get("warnings", [])).to_excel(
                writer, sheet_name="模型校验", index=False
            )
    return bio.getvalue()
