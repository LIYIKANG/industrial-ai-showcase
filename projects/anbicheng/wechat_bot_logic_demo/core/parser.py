"""客户产品文本解析模块。"""

import re


FIELD_ALIASES = {
    "partno.": "part_no",
    "partno": "part_no",
    "产品编号": "product_code",
    "产品状态": "product_status",
    "产品品名": "product_name",
    "材质及特殊特性": "material_special",
    "硬度": "hardness",
    "颜色": "color",
    "不含税价": "tax_excluded_price",
    "含税价": "tax_included_price",
    "含税单价": "tax_included_price",
    "最小定量": "moq",
    "最小订量": "moq",
    "内部sku": "internal_sku",
    "sku": "internal_sku",
    "moq": "moq",
}

COMMA_FORMAT_FIELDS = [
    "internal_sku",
    "material_special",
    "hardness",
    "color",
    "tax_included_price",
    "moq",
]

MIN_COMMA_FIELD_COUNT = 5
MAX_COMMA_FIELD_COUNT = 6


def _normalize_field_name(field_name):
    """统一字段名格式，兼容字段名前后的空格和英文大小写。"""
    return re.sub(r"\s+", "", field_name.strip()).lower()


def _parse_price(value):
    """尝试把价格转换为数字；失败时保留原值，交给 validator 返回明确错误。"""
    value = value.strip().replace("¥", "").replace("￥", "").replace(",", "")
    if value == "":
        return ""

    try:
        return float(value)
    except ValueError:
        return value


def _parse_moq(value):
    """尝试把 MOQ 转换为整数；失败时保留原值，交给 validator 返回明确错误。"""
    value = value.strip().upper().replace(",", "")
    if value == "":
        return ""

    if value.endswith("PCS"):
        value = value[:-3].strip()

    if value.endswith("K"):
        number_text = value[:-1].strip()
        try:
            return int(float(number_text) * 1000)
        except ValueError:
            return value

    try:
        return int(value)
    except ValueError:
        return value


def _parse_value(target_key, raw_value):
    """根据目标字段类型转换字段值。"""
    value = raw_value.strip()

    if target_key == "tax_included_price":
        return _parse_price(value)
    if target_key == "moq":
        return _parse_moq(value)

    return value


def _parse_comma_separated_input(raw_text):
    """解析固定位置英文逗号分隔格式。"""
    parts = [part.strip() for part in raw_text.strip().split(",")]
    parsed_data = {"_input_format": "comma"}

    fields_to_parse = min(len(parts), MAX_COMMA_FIELD_COUNT)
    for index, target_key in enumerate(COMMA_FORMAT_FIELDS[:fields_to_parse]):
        parsed_data[target_key] = _parse_value(target_key, parts[index])

    if "moq" not in parsed_data:
        parsed_data["moq"] = ""

    if len(parts) < MIN_COMMA_FIELD_COUNT:
        parsed_data["_parse_errors"] = [
            "固定位置格式字段数量不足，至少需要提供：SKU, 材质及特殊特性, 硬度, 颜色, 含税单价"
        ]
        return parsed_data

    if len(parts) > MAX_COMMA_FIELD_COUNT:
        parsed_data["_parse_errors"] = [
            "固定位置格式字段数量过多，请按 5 到 6 个字段提交"
        ]
        return parsed_data

    return parsed_data


def parse_product_input(raw_text):
    """把用户粘贴的多行文本解析成结构化字典。"""
    # 第一版 Demo 中，英文逗号表示固定位置格式，优先按该格式解析。
    if "," in raw_text:
        return _parse_comma_separated_input(raw_text)

    parsed_data = {}

    for line in raw_text.splitlines():
        if not line.strip():
            continue

        match = re.match(r"^\s*(.*?)\s*[:：]\s*(.*?)\s*$", line)
        if not match:
            continue

        raw_key, raw_value = match.groups()
        normalized_key = _normalize_field_name(raw_key)
        target_key = FIELD_ALIASES.get(normalized_key)
        if not target_key:
            continue

        parsed_data[target_key] = _parse_value(target_key, raw_value)

    return parsed_data
