"""产品字段校验模块。"""


REQUIRED_FIELDS = {
    "internal_sku": "SKU",
    "material_special": "材质及特殊特性",
    "hardness": "硬度",
    "color": "颜色",
    "tax_included_price": "含税单价",
}


def _is_missing(value):
    """判断字段是否缺失；数字 0 是有效值，不能当作缺失。"""
    return value is None or (isinstance(value, str) and value.strip() == "")


def validate_product_data(product_data):
    """检查必填字段和价格格式，返回结构化校验结果。"""
    errors = list(product_data.get("_parse_errors", []))
    missing_fields = []

    for key, display_name in REQUIRED_FIELDS.items():
        if key not in product_data or _is_missing(product_data.get(key)):
            missing_fields.append(display_name)

    price = product_data.get("tax_included_price")
    if "含税单价" not in missing_fields:
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            errors.append("含税单价必须是有效数字")
        elif price < 0:
            errors.append("含税单价不能为负数")

    moq = product_data.get("moq")
    if not _is_missing(moq):
        if not isinstance(moq, int) or isinstance(moq, bool):
            errors.append("MOQ 必须是有效整数")
        elif moq < 0:
            errors.append("MOQ 不能为负数")

    return {
        "is_valid": not missing_fields and not errors,
        "missing_fields": missing_fields,
        "errors": errors,
    }
