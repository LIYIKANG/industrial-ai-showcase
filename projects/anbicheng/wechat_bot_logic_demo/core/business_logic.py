"""核心业务逻辑模块。"""

from repository.product_repository import ProductRepository


PRODUCT_STORAGE_FIELDS = {
    "part_no",
    "product_code",
    "product_status",
    "product_image",
    "product_name",
    "material_special",
    "hardness",
    "color",
    "tax_excluded_price",
    "tax_included_price",
    "internal_sku",
    "sku_aliases",
    "moq",
    "moq_text",
    "price_tiers",
}

REQUIRED_CREATE_FIELDS = {
    "internal_sku": "SKU",
    "material_special": "材质及特殊特性",
    "hardness": "硬度",
    "color": "颜色",
    "tax_included_price": "含税单价",
}


def _is_empty_price(value):
    """数据库中价格为空时，允许本次提交价格补写进去。"""
    return value is None or value == ""


def _to_float_price(value):
    """把本地 JSON 中的价格转换为可比较数字。"""
    if isinstance(value, bool):
        raise ValueError("数据库中的价格格式错误：布尔值不是有效价格")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"数据库中的价格格式错误：{value}") from exc


def _normalize_moq(value):
    """把 MOQ 转为整数；为空时返回 None。"""
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MOQ 格式错误：{value}") from exc


def _find_price_tier(product, moq):
    """在真实表的阶梯价格中查找对应 MOQ 档位。"""
    if moq is None:
        return None

    for tier in product.get("price_tiers", []):
        if tier.get("moq") == moq:
            return tier

    return None


def _get_database_price(product, moq):
    """获取用于比较的数据库价格，优先使用对应 MOQ 阶梯价。"""
    matched_tier = _find_price_tier(product, moq)
    if matched_tier is not None:
        return matched_tier.get("tax_included_price"), matched_tier

    return product.get("tax_included_price"), None


def _is_missing(value):
    """判断新增产品所需字段是否为空。"""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _find_missing_create_fields(product_data):
    """新增产品前再次确认字段完整，方便后续绕过终端入口时复用。"""
    missing_fields = []
    for key, display_name in REQUIRED_CREATE_FIELDS.items():
        if key not in product_data or _is_missing(product_data.get(key)):
            missing_fields.append(display_name)
    return missing_fields


def _build_storage_product(product_data):
    """去掉解析器内部字段，只保存产品业务字段。"""
    product = {
        key: value
        for key, value in product_data.items()
        if key in PRODUCT_STORAGE_FIELDS
    }
    if product.get("moq") not in (None, "") and product.get("tax_included_price") not in (
        None,
        "",
    ):
        product.setdefault(
            "price_tiers",
            [
                {
                    "tax_included_price": product["tax_included_price"],
                    "moq": product["moq"],
                    "moq_text": str(product["moq"]),
                }
            ],
        )
    return product


def _build_submitted_summary(product_data):
    """整理客户本次录入内容，用于回复和日志展示。"""
    return {
        "internal_sku": product_data.get("internal_sku"),
        "material_special": product_data.get("material_special"),
        "hardness": product_data.get("hardness"),
        "color": product_data.get("color"),
        "tax_included_price": product_data.get("tax_included_price"),
        "moq": _normalize_moq(product_data.get("moq")),
    }


def process_product(product_data, repository=None):
    """根据内部 SKU 执行新增、补价、降价更新或暂不更新。"""
    repo = repository or ProductRepository()
    sku = product_data["internal_sku"]
    submitted_price = product_data["tax_included_price"]
    submitted_moq = _normalize_moq(product_data.get("moq"))
    submitted_summary = _build_submitted_summary(product_data)

    existing_product = repo.get_by_sku(sku)

    if existing_product is None:
        missing_fields = _find_missing_create_fields(product_data)
        if missing_fields:
            return {
                "result_type": "need_more_fields",
                "sku": sku,
                "is_created": False,
                "is_price_updated": False,
                "old_price": None,
                "submitted_price": submitted_price,
                "moq": submitted_moq,
                "matched_moq": submitted_moq,
                "new_price": None,
                "final_price": None,
                "missing_fields": missing_fields,
                "submitted_product": submitted_summary,
                "database_product": None,
                "database_product_after": None,
                "message": "当前 SKU 不存在，请补充完整字段后再新增产品",
            }

        product_to_create = _build_storage_product(product_data)
        repo.create_product(product_to_create)
        # 未来 T+ API 应该接在这里：本地新增成功后，同步创建或推送到 T+。
        return {
            "result_type": "created",
            "sku": sku,
            "is_created": True,
            "is_price_updated": False,
            "old_price": None,
            "submitted_price": submitted_price,
            "moq": submitted_moq,
            "matched_moq": submitted_moq,
            "new_price": submitted_price,
            "final_price": submitted_price,
            "submitted_product": submitted_summary,
            "database_product": None,
            "database_product_after": product_to_create,
            "message": "当前 SKU 不存在，已作为新产品数据新增",
        }

    old_price_raw, matched_tier = _get_database_price(existing_product, submitted_moq)
    matched_moq = matched_tier.get("moq") if matched_tier else existing_product.get("moq")

    if _is_empty_price(old_price_raw):
        updated_product = repo.update_product_price(
            sku,
            submitted_price,
            moq=matched_tier.get("moq") if matched_tier else submitted_moq,
        )
        # 未来 T+ API 应该接在这里：价格补充后，同步更新 T+ 对应产品价格。
        return {
            "result_type": "price_filled",
            "sku": sku,
            "is_created": False,
            "is_price_updated": True,
            "old_price": None,
            "submitted_price": submitted_price,
            "moq": submitted_moq,
            "matched_moq": matched_moq,
            "new_price": submitted_price,
            "final_price": submitted_price,
            "submitted_product": submitted_summary,
            "database_product": existing_product,
            "database_product_after": updated_product,
            "message": "当前 SKU 已存在，但原价格为空，已补充本次价格",
        }

    old_price = _to_float_price(old_price_raw)

    if submitted_price < old_price:
        updated_product = repo.update_product_price(
            sku,
            submitted_price,
            moq=matched_tier.get("moq") if matched_tier else submitted_moq,
        )
        # 未来 T+ API 应该接在这里：本地降价更新成功后，再同步 T+ 价格。
        return {
            "result_type": "price_updated",
            "sku": sku,
            "is_created": False,
            "is_price_updated": True,
            "old_price": old_price,
            "submitted_price": submitted_price,
            "moq": submitted_moq,
            "matched_moq": matched_moq,
            "new_price": submitted_price,
            "final_price": submitted_price,
            "submitted_product": submitted_summary,
            "database_product": existing_product,
            "database_product_after": updated_product,
            "message": "本次价格低于原价格，已更新为最新价格",
        }

    return {
        "result_type": "no_update",
        "sku": sku,
        "is_created": False,
        "is_price_updated": False,
        "old_price": old_price,
        "submitted_price": submitted_price,
        "moq": submitted_moq,
        "matched_moq": matched_moq,
        "new_price": None,
        "final_price": old_price,
        "submitted_product": submitted_summary,
        "database_product": existing_product,
        "database_product_after": existing_product,
        "message": "本次价格未低于数据库原价格，暂不更新，可进入人工确认",
    }
