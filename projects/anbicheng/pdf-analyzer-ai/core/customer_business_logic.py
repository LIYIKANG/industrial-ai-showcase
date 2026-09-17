"""
core/customer_business_logic.py
===============================
客户关键词业务规则处理。
"""

from __future__ import annotations

from typing import Any, Dict

from repository.customer_repository import CustomerRepository


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _config_fields(config: Dict[str, Any]) -> list[Dict[str, Any]]:
    return list(config.get("fields", []))


def get_business_key_field(config: Dict[str, Any]) -> Dict[str, Any]:
    for field in _config_fields(config):
        if field.get("business_key"):
            return field
    for field in _config_fields(config):
        if field.get("required"):
            return field
    raise ValueError("客户关键词配置至少需要一个字段")


def _find_first_parser_type(config: Dict[str, Any], parser_type: str) -> Dict[str, Any] | None:
    for field in _config_fields(config):
        if str(field.get("parser_type", "")).lower() == parser_type:
            return field
    return None


def _normalize_moq(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float_price(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("数据库中的价格格式错误：布尔值不是有效价格")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"数据库中的价格格式错误：{value}") from exc


def _find_price_tier(product: Dict[str, Any], moq: int | None) -> Dict[str, Any] | None:
    if moq is None:
        return None
    for tier in product.get("price_tiers", []) or []:
        if tier.get("moq") == moq:
            return tier
    return None


def _get_database_price(
    product: Dict[str, Any],
    price_field_id: str,
    moq: int | None,
) -> tuple[Any, Dict[str, Any] | None]:
    tier = _find_price_tier(product, moq)
    if tier is not None:
        return tier.get(price_field_id), tier
    return product.get(price_field_id), None


def _strip_repository_metadata(product: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in product.items()
        if not str(key).startswith("_")
    }


def _build_submitted_product(parsed: Dict[str, Any]) -> Dict[str, Any]:
    return {
        field_id: entry.get("value")
        for field_id, entry in parsed.get("fields", {}).items()
        if not _is_missing(entry.get("value"))
    }


def _build_storage_product(parsed: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    product = _build_submitted_product(parsed)
    price_field = _find_first_parser_type(config, "price")
    moq_field = _find_first_parser_type(config, "moq")
    if price_field and moq_field:
        price = product.get(price_field["id"])
        moq = _normalize_moq(product.get(moq_field["id"]))
        if not _is_missing(price) and moq is not None:
            product.setdefault(
                "price_tiers",
                [
                    {
                        price_field["id"]: price,
                        "moq": moq,
                        "moq_text": str(moq),
                    }
                ],
            )
    return product


def _line_item_key_value(line_item: Dict[str, Any], business_key_id: str) -> str:
    candidates = [
        line_item.get("business_key_value"),
        line_item.get(business_key_id),
        line_item.get("sku"),
        line_item.get("product_code"),
        line_item.get("internal_sku"),
        line_item.get("part_no"),
        line_item.get("item_no"),
    ]
    for value in candidates:
        if not _is_missing(value):
            return str(value).strip()
    return ""


def _lowest_line_item_price(line_item: Dict[str, Any]) -> tuple[float | None, Dict[str, Any] | None]:
    candidates: list[tuple[float, Dict[str, Any]]] = []
    prices = line_item.get("prices", []) or []
    if not isinstance(prices, list):
        prices = []

    for price in prices:
        if not isinstance(price, dict):
            continue
        raw_price = price.get("tax_included_price")
        if _is_missing(raw_price):
            continue
        try:
            candidates.append((_to_float_price(raw_price), price))
        except ValueError:
            continue

    if not candidates and not _is_missing(line_item.get("tax_included_price")):
        try:
            candidates.append(
                (
                    _to_float_price(line_item.get("tax_included_price")),
                    {
                        "tax_included_price": line_item.get("tax_included_price"),
                        "moq": line_item.get("moq", ""),
                        "quantity": line_item.get("quantity", ""),
                        "unit": line_item.get("unit", ""),
                        "amount": line_item.get("amount", ""),
                        "source_table": line_item.get("source_table", ""),
                    },
                )
            )
        except ValueError:
            pass

    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def _build_line_item_price_tiers(line_item: Dict[str, Any]) -> list[Dict[str, Any]]:
    tiers = []
    for price in line_item.get("prices", []) or []:
        if not isinstance(price, dict) or _is_missing(price.get("tax_included_price")):
            continue
        try:
            value = _to_float_price(price.get("tax_included_price"))
        except ValueError:
            continue
        tiers.append(
            {
                "tax_included_price": value,
                "moq": price.get("moq", ""),
                "quantity": price.get("quantity", ""),
                "unit": price.get("unit", ""),
                "amount": price.get("amount", ""),
                "price_type": price.get("price_type", ""),
                "source_table": price.get("source_table", ""),
            }
        )
    return tiers


def _build_line_item_product(
    line_item: Dict[str, Any],
    *,
    business_key_id: str,
    business_key_value: str,
    submitted_price: float | None,
) -> Dict[str, Any]:
    product = {
        "sku": line_item.get("sku") or business_key_value,
        "internal_sku": line_item.get("internal_sku") or line_item.get("sku") or business_key_value,
        "product_code": line_item.get("product_code", ""),
        "item_no": line_item.get("item_no", ""),
        "product_name": line_item.get("product_name", ""),
        "part_no": line_item.get("part_no", ""),
        "material_special": line_item.get("material_special", ""),
        "hardness": line_item.get("hardness", ""),
        "color": line_item.get("color", ""),
        "unit": line_item.get("unit", ""),
        "lead_time": line_item.get("lead_time", ""),
        "source_table": line_item.get("source_table", ""),
        "tax_included_price": submitted_price if submitted_price is not None else "",
        "price_tiers": _build_line_item_price_tiers(line_item),
        "prices": line_item.get("prices", []) or [],
    }
    product[business_key_id] = business_key_value
    return product


def commit_customer_line_item_product(
    line_item: Dict[str, Any],
    config: Dict[str, Any],
    repository: CustomerRepository,
    *,
    company_id: int | None = None,
) -> Dict[str, Any]:
    """把单个 SKU 明细录入产品库；价格按当前行可识别价格中的最低价落库。"""
    line_config = config.get("line_items", {}) if isinstance(config.get("line_items"), dict) else {}
    business_key_id = str(line_config.get("business_key", "sku") or "sku")
    key_value = _line_item_key_value(line_item, business_key_id)
    submitted_price, selected_price = _lowest_line_item_price(line_item)

    if not key_value:
        return {
            "result_type": "validation_failed",
            "action": "blocked",
            "business_key_id": business_key_id,
            "business_key_value": "",
            "is_created": False,
            "is_updated": False,
            "old_value": None,
            "new_value": submitted_price,
            "selected_price": selected_price,
            "database_product": None,
            "database_product_after": None,
            "message": "缺少 SKU/主键，未录入模拟数据库",
        }

    repository.ensure_seeded()
    product_to_store = _build_line_item_product(
        line_item,
        business_key_id=business_key_id,
        business_key_value=key_value,
        submitted_price=submitted_price,
    )
    existing_record = repository.get_by_business_key(
        business_key_id,
        key_value,
        company_id=company_id,
    )

    if existing_record is None:
        created = repository.create_product(
            product_to_store,
            business_key_id,
            company_id=company_id,
        )
        return {
            "result_type": "created",
            "action": "created",
            "business_key_id": business_key_id,
            "business_key_value": key_value,
            "is_created": True,
            "is_updated": False,
            "old_value": None,
            "new_value": submitted_price,
            "selected_price": selected_price,
            "database_product": None,
            "database_product_after": repository.product_to_dict(created),
            "message": "模拟数据库中未找到该 SKU，已新增记录",
        }

    existing_product = repository.product_to_dict(existing_record) or {}
    old_price_raw = existing_product.get("tax_included_price")
    merged_product = {
        **_strip_repository_metadata(existing_product),
        **product_to_store,
    }

    if submitted_price is None:
        merged_product["tax_included_price"] = old_price_raw if not _is_missing(old_price_raw) else ""
        updated = repository.update_product(existing_record, merged_product, business_key_id)
        return {
            "result_type": "updated_without_price",
            "action": "metadata_updated",
            "business_key_id": business_key_id,
            "business_key_value": key_value,
            "is_created": False,
            "is_updated": True,
            "old_value": old_price_raw,
            "new_value": merged_product["tax_included_price"],
            "selected_price": None,
            "database_product": existing_product,
            "database_product_after": repository.product_to_dict(updated),
            "message": "该 SKU 未识别到价格，已录入基础信息，价格保持为空或保留原值",
        }

    if _is_missing(old_price_raw):
        merged_product["tax_included_price"] = submitted_price
        updated = repository.update_product(existing_record, merged_product, business_key_id)
        return {
            "result_type": "price_filled",
            "action": "updated",
            "business_key_id": business_key_id,
            "business_key_value": key_value,
            "is_created": False,
            "is_updated": True,
            "old_value": None,
            "new_value": submitted_price,
            "selected_price": selected_price,
            "database_product": existing_product,
            "database_product_after": repository.product_to_dict(updated),
            "message": "模拟数据库中已有 SKU 但价格为空，已录入本次最低价",
        }

    try:
        old_price = _to_float_price(old_price_raw)
    except ValueError:
        old_price = None

    if old_price is None or submitted_price < old_price:
        merged_product["tax_included_price"] = submitted_price
        updated = repository.update_product(existing_record, merged_product, business_key_id)
        return {
            "result_type": "price_updated",
            "action": "updated",
            "business_key_id": business_key_id,
            "business_key_value": key_value,
            "is_created": False,
            "is_updated": True,
            "old_value": old_price_raw,
            "new_value": submitted_price,
            "selected_price": selected_price,
            "database_product": existing_product,
            "database_product_after": repository.product_to_dict(updated),
            "message": "本次最低价低于模拟数据库原价格，已更新",
        }

    merged_product["tax_included_price"] = old_price_raw
    updated = repository.update_product(existing_record, merged_product, business_key_id)
    return {
        "result_type": "no_update",
        "action": "kept_existing_price",
        "business_key_id": business_key_id,
        "business_key_value": key_value,
        "is_created": False,
        "is_updated": False,
        "old_value": old_price,
        "new_value": old_price,
        "submitted_price": submitted_price,
        "selected_price": selected_price,
        "database_product": existing_product,
        "database_product_after": repository.product_to_dict(updated),
        "message": "模拟数据库已有更低或相同价格，保留原价格",
    }


def _validation_failed_result(
    parsed: Dict[str, Any],
    validation: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    business_key = get_business_key_field(config)
    submitted = _build_submitted_product(parsed)
    return {
        "result_type": "validation_failed",
        "action": "blocked",
        "business_key_id": business_key["id"],
        "business_key_value": submitted.get(business_key["id"]),
        "is_created": False,
        "is_updated": False,
        "requires_manual_confirmation": True,
        "missing_fields": validation.get("missing_fields", []),
        "errors": validation.get("errors", []),
        "submitted_product": submitted,
        "database_product": None,
        "database_product_after": None,
        "old_value": None,
        "new_value": None,
        "message": "字段校验失败，请补充或修正后重新提交",
    }


def process_customer_product(
    parsed: Dict[str, Any],
    validation: Dict[str, Any],
    config: Dict[str, Any],
    repository: CustomerRepository,
    *,
    company_id: int | None = None,
) -> Dict[str, Any]:
    """根据配置化主键查询产品并执行新增、更新、保持或人工确认。"""
    business_key = get_business_key_field(config)
    price_field = _find_first_parser_type(config, "price")
    moq_field = _find_first_parser_type(config, "moq")

    if not validation.get("is_valid"):
        return _validation_failed_result(parsed, validation, config)

    repository.ensure_seeded()
    submitted = _build_submitted_product(parsed)
    key_value = submitted.get(business_key["id"])
    existing_record = repository.get_by_business_key(
        business_key["id"],
        str(key_value or ""),
        company_id=company_id,
    )

    if existing_record is None:
        product_to_create = _build_storage_product(parsed, config)
        created = repository.create_product(
            product_to_create,
            business_key["id"],
            company_id=company_id,
        )
        return {
            "result_type": "created",
            "action": "created",
            "business_key_id": business_key["id"],
            "business_key_value": key_value,
            "is_created": True,
            "is_updated": False,
            "requires_manual_confirmation": False,
            "old_value": None,
            "new_value": submitted.get(price_field["id"]) if price_field else None,
            "matched_moq": submitted.get(moq_field["id"]) if moq_field else None,
            "submitted_product": submitted,
            "database_product": None,
            "database_product_after": repository.product_to_dict(created),
            "message": "当前主键不存在，已作为新产品数据新增",
        }

    existing_product = repository.product_to_dict(existing_record) or {}
    if price_field is None:
        return {
            "result_type": "need_manual_confirmation",
            "action": "manual_confirm",
            "business_key_id": business_key["id"],
            "business_key_value": key_value,
            "is_created": False,
            "is_updated": False,
            "requires_manual_confirmation": True,
            "old_value": None,
            "new_value": None,
            "submitted_product": submitted,
            "database_product": existing_product,
            "database_product_after": existing_product,
            "message": "配置中没有价格字段，请人工确认处理方式",
        }

    submitted_price = submitted.get(price_field["id"])
    submitted_moq = _normalize_moq(submitted.get(moq_field["id"])) if moq_field else None
    old_price_raw, matched_tier = _get_database_price(
        existing_product,
        price_field["id"],
        submitted_moq,
    )
    matched_moq = matched_tier.get("moq") if matched_tier else existing_product.get("moq")

    if _is_missing(old_price_raw):
        updated = repository.update_product_price(
            existing_record,
            price_field["id"],
            submitted_price,
            moq=matched_tier.get("moq") if matched_tier else submitted_moq,
        )
        return {
            "result_type": "price_filled",
            "action": "updated",
            "business_key_id": business_key["id"],
            "business_key_value": key_value,
            "is_created": False,
            "is_updated": True,
            "requires_manual_confirmation": False,
            "old_value": None,
            "new_value": submitted_price,
            "matched_moq": matched_moq,
            "submitted_product": submitted,
            "database_product": existing_product,
            "database_product_after": repository.product_to_dict(updated),
            "message": "当前主键已存在，但原价格为空，已补充本次价格",
        }

    old_price = _to_float_price(old_price_raw)
    if submitted_price < old_price:
        updated = repository.update_product_price(
            existing_record,
            price_field["id"],
            submitted_price,
            moq=matched_tier.get("moq") if matched_tier else submitted_moq,
        )
        return {
            "result_type": "price_updated",
            "action": "updated",
            "business_key_id": business_key["id"],
            "business_key_value": key_value,
            "is_created": False,
            "is_updated": True,
            "requires_manual_confirmation": False,
            "old_value": old_price,
            "new_value": submitted_price,
            "matched_moq": matched_moq,
            "submitted_product": submitted,
            "database_product": existing_product,
            "database_product_after": repository.product_to_dict(updated),
            "message": "本次价格低于原价格，已更新为最新价格",
        }

    return {
        "result_type": "no_update",
        "action": "manual_confirm",
        "business_key_id": business_key["id"],
        "business_key_value": key_value,
        "is_created": False,
        "is_updated": False,
        "requires_manual_confirmation": True,
        "old_value": old_price,
        "new_value": old_price,
        "submitted_price": submitted_price,
        "matched_moq": matched_moq,
        "submitted_product": submitted,
        "database_product": existing_product,
        "database_product_after": existing_product,
        "message": "本次价格未低于数据库原价格，暂不自动更新，请人工确认",
    }


def build_customer_result_fields(
    parsed: Dict[str, Any],
    validation: Dict[str, Any],
    config: Dict[str, Any],
) -> list[Dict[str, Any]]:
    """构造前端校对表格所需的字段结果结构。"""
    validation_fields = validation.get("fields", {})
    rows = []
    for field in _config_fields(config):
        field_id = field["id"]
        entry = parsed.get("fields", {}).get(field_id, {})
        status = validation_fields.get(field_id, {}).get("status", "empty")
        errors = validation_fields.get(field_id, {}).get("errors", [])
        value = entry.get("value", "")
        rows.append(
            {
                "id": field_id,
                "label": field.get("label", field_id),
                "raw_value": entry.get("raw_value", ""),
                "standard_value": value,
                "value": value,
                "filled": not _is_missing(value),
                "required": bool(field.get("required", False)),
                "parser_type": field.get("parser_type", "text"),
                "business_key": bool(field.get("business_key", False)),
                "output_cell": field.get("output_cell", ""),
                "validation_status": status,
                "errors": errors,
                "note": entry.get("note", ""),
                "source": entry.get("source", ""),
                "source_alias": entry.get("source_alias", ""),
            }
        )
    return rows
