"""本地终端入口。

未来企业微信入口应该接在 handle_product_text(raw_text) 这一层：
企业微信 Bot 收到消息后，把消息正文作为 raw_text 传入即可，不需要改核心业务逻辑。
"""

from datetime import datetime
import traceback

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
    """日志失败不应影响主流程，但需要在终端提示。"""
    try:
        repository.append_operation_log(log_data)
    except Exception as exc:  # noqa: BLE001 - Demo 阶段直接给出可读错误
        print(f"警告：操作日志写入失败：{exc}")


def _build_log_entry(raw_text, parsed_data, result):
    """组装统一日志结构。"""
    return {
        "operation_time": _now_text(),
        "sku": parsed_data.get("internal_sku"),
        "operation_type": result.get("result_type"),
        "raw_input": raw_text,
        "parsed_fields": parsed_data,
        "old_price": result.get("old_price"),
        "new_price": result.get("new_price") or result.get("final_price"),
        "process_result": result,
    }


def handle_product_text(raw_text, repository=None):
    """处理一段客户产品文本，并返回可打印的终端结果。"""
    repo = repository or ProductRepository()
    parsed_data = parse_product_input(raw_text)

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
        _append_log_safely(repo, _build_log_entry(raw_text, parsed_data, result))
        return build_validation_error_response(result)

    result = process_product(parsed_data, repository=repo)
    _append_log_safely(repo, _build_log_entry(raw_text, parsed_data, result))
    return build_terminal_response(result)


def _read_multiline_input():
    """读取多行输入，遇到空行提交；保留 Ctrl+D 兼容管道或旧习惯。"""
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break

        if line.strip() == "":
            break

        lines.append(line)

    return "\n".join(lines)


def main():
    """从终端读取多行输入并执行处理。"""
    print("请粘贴客户产品数据，输入结束后按一次空回车提交：")

    try:
        raw_text = _read_multiline_input()
        if not raw_text.strip():
            print("未读取到输入内容，请重新运行后粘贴客户产品数据。")
            return

        response_text = handle_product_text(raw_text)
        print(response_text)
    except KeyboardInterrupt:
        print("\n已取消本次处理。")
    except Exception as exc:  # noqa: BLE001 - Demo 阶段保留顶层兜底
        print("程序处理失败，请检查输入或本地 JSON 文件。")
        print(f"错误原因：{exc}")
        print("调试信息：")
        traceback.print_exc()


if __name__ == "__main__":
    main()
