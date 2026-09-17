"""终端输出构建模块。"""


STANDARD_FORMAT_TIP = """请按照以下格式提交：
SKU, 材质及特殊特性, 硬度, 颜色, 含税单价, MOQ

示例：
LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.79, 1000

MOQ 也支持 1K、10K、500PCS 这类写法。"""


def _yes_no(value):
    """把布尔值转换成中文显示。"""
    return "是" if value else "否"


def _format_price(value):
    """统一价格展示格式。"""
    if value is None or value == "":
        return "无"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _format_value(value):
    """统一普通字段展示格式。"""
    if value is None or value == "":
        return "无"
    return str(value)


def _format_price_tiers(product):
    """格式化数据库阶梯价。"""
    tiers = product.get("price_tiers") or []
    if not tiers:
        return ["阶梯价：无"]

    lines = ["阶梯价："]
    for tier in tiers:
        moq = tier.get("moq_text") or tier.get("moq") or "无"
        price = _format_price(tier.get("tax_included_price"))
        lines.append(f"  - MOQ {moq}：{price}")
    return lines


def _format_submitted_product(product):
    """格式化客户本次录入内容。"""
    if not product:
        return ["客户本次录入：无"]

    return [
        "客户本次录入：",
        f"  SKU：{_format_value(product.get('internal_sku'))}",
        f"  材质及特殊特性：{_format_value(product.get('material_special'))}",
        f"  硬度：{_format_value(product.get('hardness'))}",
        f"  颜色：{_format_value(product.get('color'))}",
        f"  含税单价：{_format_price(product.get('tax_included_price'))}",
        f"  MOQ：{_format_value(product.get('moq'))}",
    ]


def _format_database_product(product):
    """格式化数据库当前匹配内容。"""
    if not product:
        return ["数据库当前内容：未找到匹配记录"]

    lines = [
        "数据库当前内容：",
        f"  产品编号：{_format_value(product.get('product_code'))}",
        f"  产品品名：{_format_value(product.get('product_name'))}",
        f"  Part NO.：{_format_value(product.get('part_no'))}",
        f"  内部 SKU：{_format_value(product.get('internal_sku'))}",
        f"  材质及特殊特性：{_format_value(product.get('material_special'))}",
        f"  硬度：{_format_value(product.get('hardness'))}",
        f"  颜色：{_format_value(product.get('color'))}",
        f"  当前含税单价：{_format_price(product.get('tax_included_price'))}",
        f"  当前 MOQ：{_format_value(product.get('moq_text') or product.get('moq'))}",
    ]
    lines.extend(_format_price_tiers(product))
    return lines


def build_terminal_response(result):
    """根据业务结果生成清晰的终端输出。"""
    lines = [
        "",
        "========== 本次处理结果 ==========",
        f"处理结果类型：{result.get('result_type')}",
        f"SKU：{result.get('sku')}",
        f"本次提交 MOQ：{result.get('moq') or '无'}",
        f"匹配数据库 MOQ：{result.get('matched_moq') or '无'}",
        f"是否新增：{_yes_no(result.get('is_created'))}",
        f"是否更新价格：{_yes_no(result.get('is_price_updated'))}",
        f"原价格：{_format_price(result.get('old_price'))}",
        f"本次提交价格：{_format_price(result.get('submitted_price'))}",
        f"最终数据库价格：{_format_price(result.get('final_price'))}",
        f"提示信息：{result.get('message')}",
        "",
    ]
    lines.extend(_format_submitted_product(result.get("submitted_product")))
    lines.append("")
    lines.extend(_format_database_product(result.get("database_product_after")))
    lines.append("==================================")
    return "\n".join(lines)


def build_validation_error_response(result):
    """生成字段校验失败时的终端输出。"""
    lines = [
        "",
        "========== 字段校验失败 ==========",
        f"处理结果类型：{result.get('result_type')}",
        f"SKU：{result.get('sku') or '未识别'}",
        f"本次提交价格：{_format_price(result.get('submitted_price'))}",
        f"提示信息：{result.get('message')}",
    ]

    missing_fields = result.get("missing_fields") or []
    errors = result.get("errors") or []

    if missing_fields:
        lines.append(f"缺失字段：{', '.join(missing_fields)}")
    if errors:
        lines.append(f"错误原因：{'; '.join(errors)}")

    lines.append("")
    lines.extend(_format_submitted_product(result.get("submitted_product")))
    lines.extend(["", STANDARD_FORMAT_TIP])
    lines.append("==================================")
    return "\n".join(lines)
