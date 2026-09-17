"""
core/customer_line_item_parser.py
=================================
采购订单/报价单明细行解析。

当前客户关键词字段仍是配置化的单字段模型；这里补充多行明细结构，
用于承载同一份文件里多个 SKU/价格的场景。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


_ORDER_LINE_RE = re.compile(
    r"^\s*(?P<line_no>\d{1,4})\s+"
    r"(?P<item_no>[A-Z0-9][A-Z0-9._/-]*)\s+"
    r"(?P<middle>.+?)\s+"
    r"(?P<unit>Pcs|PCS|pcs|PC|pc|EA|ea|个|件|套)\s+"
    r"(?P<quantity>[\d,]+(?:\.\d+)?)\s+"
    r"(?P<unit_price>[¥￥]?\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<amount>[¥￥]?\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<lead_time>\d{4}[-/]\d{1,2}[-/]\d{1,2})"
    r"(?:\s+(?P<remark>.*))?\s*$"
)


def _clean_number(value: Any) -> str:
    return str(value or "").replace("¥", "").replace("￥", "").replace(",", "").strip()


def _parse_float(value: Any) -> float | str:
    cleaned = _clean_number(value)
    if not cleaned:
        return ""
    try:
        return float(cleaned)
    except ValueError:
        return str(value or "").strip()


def _parse_quantity(value: Any) -> int | float | str:
    parsed = _parse_float(value)
    if isinstance(parsed, float) and parsed.is_integer():
        return int(parsed)
    return parsed


def _parse_int(value: Any) -> int | str:
    parsed = _parse_quantity(value)
    if isinstance(parsed, int):
        return parsed
    if isinstance(parsed, float) and parsed.is_integer():
        return int(parsed)
    return "" if value in (None, "") else str(value).strip()


def _first_value(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return ""


def _split_name_and_part(middle: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", str(middle or "").strip())
    if not text:
        return "", ""
    parts = text.rsplit(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0].strip(), parts[1].strip()


def _normalize_price_entries(raw: Dict[str, Any], *, source_table: str = "") -> List[Dict[str, Any]]:
    raw_prices = raw.get("prices")
    if isinstance(raw_prices, dict):
        raw_prices = [raw_prices]
    if not isinstance(raw_prices, list):
        raw_prices = []

    if not raw_prices:
        direct_price = _first_value(raw, "tax_included_price", "unit_price", "price", "含税单价")
        direct_amount = _first_value(raw, "amount", "tax_included_amount", "价税合计")
        if direct_price not in (None, "") or direct_amount not in (None, ""):
            raw_prices = [
                {
                    "tax_included_price": direct_price,
                    "amount": direct_amount,
                    "moq": _first_value(raw, "moq", "MOQ", "minimum_order_quantity", "最小定量"),
                    "quantity": _first_value(raw, "quantity", "qty", "数量"),
                    "unit": _first_value(raw, "unit", "单位"),
                    "price_type": _first_value(raw, "price_type", "价格类型") or "含税单价",
                    "source_table": source_table,
                    "note": _first_value(raw, "note", "备注说明"),
                }
            ]

    prices: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw_price in raw_prices:
        if not isinstance(raw_price, dict):
            raw_price = {"tax_included_price": raw_price}
        price_value = _parse_float(
            _first_value(raw_price, "tax_included_price", "unit_price", "price", "含税单价")
        )
        amount = _parse_float(_first_value(raw_price, "amount", "tax_included_amount", "价税合计"))
        moq = str(_first_value(raw_price, "moq", "MOQ", "minimum_order_quantity", "最小定量")).strip()
        quantity = _parse_quantity(_first_value(raw_price, "quantity", "qty", "数量"))
        unit = str(_first_value(raw_price, "unit", "单位")).strip()
        price_type = str(_first_value(raw_price, "price_type", "价格类型")).strip() or "含税单价"
        table = str(_first_value(raw_price, "source_table", "table_name", "表格名称")).strip() or source_table
        note = str(_first_value(raw_price, "note", "备注说明")).strip()
        confidence = _first_value(raw_price, "confidence", "置信度")

        key = (str(price_value), str(moq), str(quantity), price_type)
        if key in seen:
            continue
        seen.add(key)

        errors: list[str] = []
        if not isinstance(price_value, (int, float)):
            errors.append("含税单价格式无效")
        if quantity != "" and not isinstance(quantity, (int, float)):
            errors.append("数量格式无效")

        prices.append(
            {
                "tax_included_price": price_value,
                "amount": amount,
                "moq": moq,
                "quantity": quantity,
                "unit": unit,
                "price_type": price_type,
                "source_table": table,
                "note": note,
                "confidence": confidence,
                "validation_status": "error" if errors else "ok",
                "errors": errors,
            }
        )
    return prices


def _price_matches_hidden_rule(price: Dict[str, Any], hidden_terms: list[str]) -> bool:
    if not hidden_terms:
        return False
    haystack = " ".join(
        str(price.get(key, "") or "")
        for key in ("price_type", "source_table", "note")
    )
    return any(term and term in haystack for term in hidden_terms)


def _filter_price_entries(
    prices: List[Dict[str, Any]],
    *,
    config: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """隐藏采购订单单价等兜底行，只保留价格表/阶梯表里的明细行。"""
    if len(prices) <= 1:
        return prices

    line_config = (config or {}).get("line_items", {})
    hidden_terms = line_config.get("hide_price_rows_when_other_prices_exist", [])
    if not isinstance(hidden_terms, list):
        hidden_terms = []

    filtered = [
        price
        for price in prices
        if not _price_matches_hidden_rule(price, hidden_terms)
    ]
    return filtered or prices


def _refresh_item_status_and_summary(item: Dict[str, Any]) -> None:
    errors = []
    if not item.get("business_key_value"):
        errors.append("缺少 SKU/主键")
    if not item.get("prices"):
        errors.append("缺少价格")
    for price in item.get("prices", []):
        errors.extend(price.get("errors", []) or [])

    item["errors"] = list(dict.fromkeys(errors))
    item["validation_status"] = "error" if item["errors"] else "ok"
    first_price = item.get("prices", [{}])[0] if item.get("prices") else {}
    item["tax_included_price"] = first_price.get("tax_included_price", "")
    item["amount"] = first_price.get("amount", "")
    item["quantity"] = first_price.get("quantity", item.get("quantity", ""))
    item["unit"] = first_price.get("unit", item.get("unit", ""))


def _merge_line_item(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key in ("prices", "errors", "validation_status"):
            continue
        if target.get(key) in (None, "") and value not in (None, ""):
            target[key] = value

    price_keys = {
        (
            str(price.get("tax_included_price", "")),
            str(price.get("moq", "")),
            str(price.get("quantity", "")),
            str(price.get("price_type", "")),
            str(price.get("source_table", "")),
        )
        for price in target.get("prices", [])
    }
    for price in incoming.get("prices", []):
        key = (
            str(price.get("tax_included_price", "")),
            str(price.get("moq", "")),
            str(price.get("quantity", "")),
            str(price.get("price_type", "")),
            str(price.get("source_table", "")),
        )
        if key not in price_keys:
            target.setdefault("prices", []).append(price)
            price_keys.add(key)

    _refresh_item_status_and_summary(target)


def normalize_customer_line_items(
    raw_items: List[Dict[str, Any]] | Any,
    *,
    source: str = "",
    config: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """标准化 VLM/文本解析出的明细行。"""
    if not isinstance(raw_items, list):
        return []

    line_config = (config or {}).get("line_items", {})
    if line_config and line_config.get("enabled") is False:
        return []

    business_key_id = str(line_config.get("business_key", "sku") or "sku")
    items: List[Dict[str, Any]] = []
    by_key: Dict[str, Dict[str, Any]] = {}

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        line_no = _parse_int(_first_value(raw, "line_no", "no", "row_no", "行号", "编号"))
        item_no = str(_first_value(raw, "item_no", "customer_item_no", "code", "代码")).strip()
        sku = str(_first_value(raw, "sku", "SKU", "internal_sku", "product_code", "产品编号")).strip()
        product_code = str(_first_value(raw, "product_code", "产品编号")).strip() or sku
        product_name = str(_first_value(raw, "product_name", "part_name", "name", "品名", "名称")).strip()
        part_no = str(_first_value(raw, "part_no", "description", "model", "规格型号")).strip()
        internal_sku = str(_first_value(raw, "internal_sku", "内部SKU")).strip() or sku or part_no
        material_special = str(
            _first_value(raw, "material_special", "material", "材质及特殊特性", "材质", "材料")
        ).strip()
        hardness = str(_first_value(raw, "hardness", "硬度", "硬度范围")).strip()
        color = str(_first_value(raw, "color", "colour", "颜色", "色")).strip()
        lead_time = str(_first_value(raw, "lead_time", "delivery_date", "交货日期")).strip()
        remark = str(_first_value(raw, "remark", "备注")).strip()
        note = str(_first_value(raw, "note", "备注说明")).strip()
        page = _first_value(raw, "page", "page_no", "page_number")
        confidence = _first_value(raw, "confidence", "置信度")
        source_table = str(_first_value(raw, "source_table", "table_name", "表格名称")).strip()

        prices = _filter_price_entries(
            _normalize_price_entries(raw, source_table=source_table),
            config=config,
        )

        key_value = str(_first_value(raw, business_key_id)).strip()
        if not key_value:
            key_value = sku or product_code or internal_sku or part_no or item_no

        errors: list[str] = []
        if not key_value:
            errors.append("缺少 SKU/主键")
        if not prices:
            errors.append("缺少价格")
        for price in prices:
            errors.extend(price.get("errors", []) or [])

        first_price = prices[0] if prices else {}

        item = {
            "line_no": line_no,
            "sku": sku or product_code or internal_sku or part_no or item_no,
            "product_code": product_code,
            "item_no": item_no,
            "product_name": product_name,
            "part_no": part_no,
            "internal_sku": internal_sku,
            "material_special": material_special,
            "hardness": hardness,
            "color": color,
            "unit": first_price.get("unit", ""),
            "quantity": first_price.get("quantity", ""),
            "tax_included_price": first_price.get("tax_included_price", ""),
            "amount": first_price.get("amount", ""),
            "prices": prices,
            "lead_time": lead_time,
            "remark": remark,
            "business_key_id": business_key_id,
            "business_key_value": key_value,
            "source": source or str(_first_value(raw, "source", "来源")).strip(),
            "source_table": source_table,
            "page": page,
            "confidence": confidence,
            "note": note,
            "validation_status": "error" if errors else "ok",
            "errors": list(dict.fromkeys(errors)),
        }

        merge_key = key_value or f"{line_no}:{item_no}:{part_no}:{len(items)}"
        if merge_key in by_key:
            _merge_line_item(by_key[merge_key], item)
        else:
            by_key[merge_key] = item
            items.append(item)

    for item in items:
        item["prices"] = _filter_price_entries(item.get("prices", []), config=config)
        _refresh_item_status_and_summary(item)

    return items


def extract_customer_line_items_from_text(
    text: str,
    *,
    source: str = "",
    config: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """从采购订单文字层抽取多行商品明细。

    返回结构保持通用，后续可由 config["line_items"] 替换列定义或主键策略。
    """
    line_config = (config or {}).get("line_items", {})
    if line_config and line_config.get("enabled") is False:
        return []

    raw_items: List[Dict[str, Any]] = []

    for raw_line in (text or "").splitlines():
        match = _ORDER_LINE_RE.match(raw_line)
        if not match:
            continue

        data = match.groupdict()
        product_name, part_no = _split_name_and_part(data.get("middle", ""))
        raw_items.append(
            {
                "line_no": int(data["line_no"]),
                "sku": part_no,
                "item_no": data["item_no"],
                "product_name": product_name,
                "part_no": part_no,
                "prices": [
                    {
                        "tax_included_price": data.get("unit_price"),
                        "amount": data.get("amount"),
                        "quantity": data.get("quantity"),
                        "unit": data.get("unit", ""),
                        "price_type": "采购订单含税单价",
                    }
                ],
                "lead_time": data.get("lead_time", ""),
                "remark": data.get("remark") or "",
            }
        )

    return normalize_customer_line_items(raw_items, source=source, config=config)
