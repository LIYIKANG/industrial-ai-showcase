"""统一消息处理入口。

企业微信真实回调、/debug/wecom、本地终端都可以调用 process_user_message。
这里不写企业微信协议细节，只负责把用户文本交给现有核心业务逻辑。
"""

from datetime import datetime

from core.business_logic import process_product
from core.parser import parse_product_input
from core.response_builder import (
    build_terminal_response,
    build_validation_error_response,
)
from core.validator import validate_product_data
from repository.product_repository import ProductRepository


def _now_text():
    """生成带本地时区的操作时间。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append_log_safely(repository, log_data):
    """日志失败不影响企业微信回包，避免用户侧一直等待。"""
    try:
        repository.append_operation_log(log_data)
    except Exception:
        # 真实项目可替换为 logging.exception，目前保持核心流程不中断。
        pass


def _build_log_entry(raw_text, user_id, parsed_data, result):
    """组装统一日志结构。"""
    return {
        "operation_time": _now_text(),
        "source": "wecom" if user_id else "unknown",
        "user_id": user_id,
        "sku": parsed_data.get("internal_sku"),
        "operation_type": result.get("result_type"),
        "raw_input": raw_text,
        "parsed_fields": parsed_data,
        "old_price": result.get("old_price"),
        "new_price": result.get("new_price") or result.get("final_price"),
        "process_result": result,
    }


def process_user_message(content, user_id=None, repository=None):
    """处理企业微信用户文本消息，返回可直接回复给用户的文本。"""
    repo = repository or ProductRepository()
    parsed_data = parse_product_input(content)

    validation_result = validate_product_data(parsed_data)
    if not validation_result["is_valid"]:
        result = {
            "result_type": "validation_failed",
            "message": "字段校验失败，请补充或修正后重新提交",
            "sku": parsed_data.get("internal_sku"),
            "submitted_price": parsed_data.get("tax_included_price"),
            "submitted_product": parsed_data,
            "database_product": None,
            "database_product_after": None,
            "errors": validation_result["errors"],
            "missing_fields": validation_result["missing_fields"],
        }
        _append_log_safely(repo, _build_log_entry(content, user_id, parsed_data, result))
        return build_validation_error_response(result)

    result = process_product(parsed_data, repository=repo)
    _append_log_safely(repo, _build_log_entry(content, user_id, parsed_data, result))
    return build_terminal_response(result)
