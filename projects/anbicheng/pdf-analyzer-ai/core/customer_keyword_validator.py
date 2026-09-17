"""
core/customer_keyword_validator.py
==================================
配置驱动的客户关键词校验器。
"""

from __future__ import annotations

from typing import Any, Dict


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _field_map(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {field["id"]: field for field in config.get("fields", [])}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_type(field: Dict[str, Any], value: Any) -> list[str]:
    parser_type = str(field.get("parser_type", "text")).lower()
    label = field.get("label", field["id"])

    if _is_missing(value):
        return []

    if parser_type == "price":
        if not _is_number(value):
            return [f"{label}必须是有效数字"]
        if value < 0:
            return [f"{label}不能为负数"]
        return []

    if parser_type == "moq":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{label}必须是有效整数"]
        if value < 0:
            return [f"{label}不能为负数"]
        return []

    if parser_type == "number":
        if not _is_number(value):
            return [f"{label}必须是有效数字"]
        return []

    if parser_type in ("text", "sku"):
        if not isinstance(value, str):
            return [f"{label}必须是文本"]
        return []

    return []


def _validate_not_allowed(field: Dict[str, Any], value: Any) -> list[str]:
    if _is_missing(value):
        return []
    text = str(value)
    errors = []
    for blocked in field.get("not_allowed", []) or []:
        if blocked and str(blocked) == text:
            errors.append(f"{field.get('label', field['id'])}不能填写为 {blocked}")
    return errors


def validate_customer_fields(
    parsed: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """检查必填、类型和配置化禁用值，返回字段级结构化结果。"""
    field_defs = _field_map(config)
    field_results: Dict[str, Dict[str, Any]] = {}
    missing_fields: list[str] = []
    errors: list[str] = list(parsed.get("parse_errors", []))

    for field_id, field in field_defs.items():
        entry = parsed.get("fields", {}).get(field_id, {})
        value = entry.get("value", "")
        field_errors: list[str] = []
        is_missing = _is_missing(value)

        if field.get("required") and is_missing:
            label = field.get("label", field_id)
            missing_fields.append(label)
            field_errors.append(f"缺少必填字段：{label}")

        field_errors.extend(_validate_type(field, value))
        field_errors.extend(_validate_not_allowed(field, value))
        errors.extend(field_errors)

        if field_errors:
            status = "missing" if is_missing and field.get("required") else "error"
        else:
            status = "ok" if not is_missing else "empty"

        field_results[field_id] = {
            "id": field_id,
            "label": field.get("label", field_id),
            "status": status,
            "errors": field_errors,
            "missing": is_missing,
            "required": bool(field.get("required", False)),
        }

    return {
        "is_valid": not missing_fields and not errors,
        "missing_fields": missing_fields,
        "errors": errors,
        "fields": field_results,
    }
