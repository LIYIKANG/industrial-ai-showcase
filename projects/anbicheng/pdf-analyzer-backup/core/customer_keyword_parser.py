"""
core/customer_keyword_parser.py
===============================
配置驱动的客户关键词解析器。

支持：
  - 字段名:值 / 字段名：值
  - 固定位置英文逗号格式
  - Claude 返回的 {field_id: value} 或 {alias: value}
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable


def _normalize_field_name(field_name: str) -> str:
    """统一字段名格式，兼容空格、英文大小写和全角冒号前后的噪音。"""
    return re.sub(r"\s+", "", str(field_name or "").strip()).lower()


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _config_fields(config: Dict[str, Any]) -> list[Dict[str, Any]]:
    return list(config.get("fields", []))


def _alias_map(config: Dict[str, Any]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for field in _config_fields(config):
        fid = field["id"]
        for name in [fid, field.get("label", fid), *field.get("aliases", [])]:
            aliases[_normalize_field_name(name)] = fid
    return aliases


def _field_by_id(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {field["id"]: field for field in _config_fields(config)}


def _parse_price(raw_value: Any) -> Any:
    value = str(raw_value or "").strip()
    value = (
        value.replace("¥", "")
        .replace("￥", "")
        .replace(",", "")
        .replace("元", "")
        .strip()
    )
    if value == "":
        return ""
    try:
        return float(value)
    except ValueError:
        return raw_value


def _parse_moq(raw_value: Any) -> Any:
    value = str(raw_value or "").strip().upper().replace(",", "")
    if value == "":
        return ""
    for suffix in ("PCS", "个", "件"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].strip()
            break
    if value.endswith("K"):
        try:
            return int(float(value[:-1].strip()) * 1000)
        except ValueError:
            return raw_value
    try:
        return int(value)
    except ValueError:
        return raw_value


def _parse_number(raw_value: Any) -> Any:
    value = str(raw_value or "").strip().replace(",", "")
    if value == "":
        return ""
    try:
        number = float(value)
    except ValueError:
        return raw_value
    return int(number) if number.is_integer() else number


def parse_value(field: Dict[str, Any], raw_value: Any) -> Any:
    """按字段 parser_type 标准化值；失败时保留原值交给 validator 报错。"""
    parser_type = str(field.get("parser_type", "text")).lower()
    if parser_type == "price":
        return _parse_price(raw_value)
    if parser_type == "moq":
        return _parse_moq(raw_value)
    if parser_type == "number":
        return _parse_number(raw_value)
    return str(raw_value or "").strip()


def _empty_field_entry(field: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": field["id"],
        "label": field.get("label", field["id"]),
        "raw_value": "",
        "value": "",
        "source_alias": "",
        "source": "",
        "note": "",
        "parser_type": field.get("parser_type", "text"),
        "required": bool(field.get("required", False)),
        "business_key": bool(field.get("business_key", False)),
        "output_cell": field.get("output_cell", ""),
        "input_position": field.get("input_position"),
    }


def _new_result(config: Dict[str, Any], input_format: str) -> Dict[str, Any]:
    fields = {field["id"]: _empty_field_entry(field) for field in _config_fields(config)}
    return {
        "config_id": config.get("id", "default"),
        "input_format": input_format,
        "fields": fields,
        "normalized": {fid: "" for fid in fields},
        "parse_errors": [],
        "unknown_fields": [],
    }


def _set_field_value(
    result: Dict[str, Any],
    config: Dict[str, Any],
    field_id: str,
    raw_value: Any,
    *,
    source_alias: str = "",
    source: str = "",
    note: str = "",
) -> None:
    field = _field_by_id(config).get(field_id)
    if not field:
        return
    value = parse_value(field, raw_value)
    entry = result["fields"][field_id]
    entry.update(
        {
            "raw_value": "" if raw_value is None else str(raw_value).strip(),
            "value": value,
            "source_alias": source_alias,
            "source": source,
            "note": note,
        }
    )
    result["normalized"][field_id] = value


def _has_key_value_line(raw_text: str) -> bool:
    for line in raw_text.splitlines():
        if re.match(r"^\s*.+?\s*[:：]\s*.*$", line):
            return True
    return False


def _position_fields(config: Dict[str, Any]) -> list[Dict[str, Any]]:
    return sorted(
        [field for field in _config_fields(config) if field.get("input_position") is not None],
        key=lambda field: int(field.get("input_position", 0)),
    )


def parse_customer_text(raw_text: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """解析人工输入文本。"""
    text = raw_text or ""
    if not text.strip():
        result = _new_result(config, "empty")
        result["parse_errors"].append("输入内容为空")
        return result

    if _has_key_value_line(text):
        result = _new_result(config, "key_value")
        aliases = _alias_map(config)
        for line in text.splitlines():
            if not line.strip():
                continue
            match = re.match(r"^\s*(.*?)\s*[:：]\s*(.*?)\s*$", line)
            if not match:
                result["parse_errors"].append(f"无法识别的行：{line.strip()}")
                continue
            raw_key, raw_value = match.groups()
            field_id = aliases.get(_normalize_field_name(raw_key))
            if not field_id:
                result["unknown_fields"].append(raw_key.strip())
                continue
            _set_field_value(
                result,
                config,
                field_id,
                raw_value,
                source_alias=raw_key.strip(),
                source="manual",
            )
        return result

    result = _new_result(config, "comma")
    parts = [part.strip() for part in text.strip().split(",")]
    fmt = config.get("input_format", {})
    min_count = int(fmt.get("comma_min_fields", 1))
    max_count = int(fmt.get("comma_max_fields", len(_position_fields(config))))

    if len(parts) < min_count:
        result["parse_errors"].append(
            f"固定位置格式字段数量不足，至少需要提供 {min_count} 个字段"
        )
    if len(parts) > max_count:
        result["parse_errors"].append(
            f"固定位置格式字段数量过多，请按 {min_count} 到 {max_count} 个字段提交"
        )

    for field, raw_value in zip(_position_fields(config), parts):
        _set_field_value(
            result,
            config,
            field["id"],
            raw_value,
            source_alias=f"position_{field.get('input_position')}",
            source="manual",
        )
    return result


def parse_extracted_values(
    extracted: Dict[str, Any],
    notes: Dict[str, str] | None,
    config: Dict[str, Any],
    sources: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """解析 AI 抽取结果，兼容字段 id 或别名作为 key。"""
    result = _new_result(config, "ai_extract")
    aliases = _alias_map(config)
    notes = notes or {}
    sources = sources or {}

    for raw_key, raw_value in (extracted or {}).items():
        field_id = aliases.get(_normalize_field_name(raw_key))
        if not field_id:
            result["unknown_fields"].append(str(raw_key))
            continue
        _set_field_value(
            result,
            config,
            field_id,
            raw_value,
            source_alias=str(raw_key),
            source=sources.get(field_id, sources.get(str(raw_key), "")),
            note=notes.get(field_id, notes.get(str(raw_key), "")),
        )
    return result


def merge_parsed_results(results: Iterable[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """合并多文件解析结果，优先保留非空值。"""
    merged = _new_result(config, "ai_extract")
    for result in results:
        merged["parse_errors"].extend(result.get("parse_errors", []))
        merged["unknown_fields"].extend(result.get("unknown_fields", []))
        for field_id, entry in result.get("fields", {}).items():
            if _is_missing(entry.get("value")):
                continue
            current = merged["fields"].get(field_id, {})
            if _is_missing(current.get("value")):
                merged["fields"][field_id] = dict(entry)
                merged["normalized"][field_id] = entry.get("value")
    return merged


def build_ai_field_definitions(config: Dict[str, Any]) -> list[Dict[str, Any]]:
    """转换为 Claude 提示词用字段定义。"""
    prompt_fields = []
    for field in _config_fields(config):
        prompt_fields.append(
            {
                "id": field["id"],
                "label": field.get("label", field["id"]),
                "aliases": field.get("aliases", []),
                "required": bool(field.get("required", False)),
                "source_hint": field.get("source_hint", ""),
                "not_allowed": field.get("not_allowed", []),
                "format": field.get("format", ""),
                "examples": field.get("examples", []),
            }
        )
    return prompt_fields
