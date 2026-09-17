"""产品数据仓储模块。

当前使用 mock_db/product_price_database.json 作为本地模拟产品价格库。
未来真实数据库应该替换这个文件中的读写实现，例如改为 MySQL、PostgreSQL 或业务系统 DAO。
"""

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
PRODUCTS_FILE = BASE_DIR / "mock_db" / "product_price_database.json"
OPERATION_LOG_FILE = BASE_DIR / "logs" / "operation_log.json"


class ProductRepository:
    """封装产品和操作日志的本地 JSON 读写。"""

    def __init__(self, products_file=None, operation_log_file=None):
        self.products_file = Path(products_file) if products_file else PRODUCTS_FILE
        self.operation_log_file = (
            Path(operation_log_file) if operation_log_file else OPERATION_LOG_FILE
        )
        self._ensure_storage()

    def _ensure_storage(self):
        """确保本地模拟数据库和日志文件存在。"""
        self.products_file.parent.mkdir(parents=True, exist_ok=True)
        self.operation_log_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.products_file.exists():
            self._write_json_array(self.products_file, [])
        if not self.operation_log_file.exists():
            self._write_json_array(self.operation_log_file, [])

    def _read_json_array(self, file_path):
        """读取 JSON 数组文件。"""
        if not file_path.exists():
            self._write_json_array(file_path, [])

        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            return []

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{file_path} 不是合法 JSON，请检查文件内容") from exc

        if not isinstance(data, list):
            raise ValueError(f"{file_path} 的顶层结构必须是数组")

        return data

    def _write_json_array(self, file_path, data):
        """写入 JSON 数组文件。"""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_by_sku(self, sku):
        """根据 SKU 查询产品，兼容真实表中的 Part NO. 和产品编号。"""
        products = self._read_json_array(self.products_file)
        for product in products:
            sku_aliases = product.get("sku_aliases") or []
            lookup_values = {
                product.get("internal_sku"),
                product.get("part_no"),
                product.get("product_code"),
                *sku_aliases,
            }
            if sku in lookup_values:
                return dict(product)
        return None

    def create_product(self, product_data):
        """新增产品数据。"""
        products = self._read_json_array(self.products_file)
        products.append(dict(product_data))
        self._write_json_array(self.products_file, products)
        return dict(product_data)

    def update_product_price(self, sku, new_price, moq=None):
        """更新指定 SKU 的含税单价；如有 MOQ，则优先更新对应阶梯价。"""
        products = self._read_json_array(self.products_file)

        for product in products:
            sku_aliases = product.get("sku_aliases") or []
            lookup_values = {
                product.get("internal_sku"),
                product.get("part_no"),
                product.get("product_code"),
                *sku_aliases,
            }
            if sku in lookup_values:
                updated_tier = False

                for tier in product.get("price_tiers", []):
                    if moq is not None and tier.get("moq") == moq:
                        tier["tax_included_price"] = new_price
                        updated_tier = True
                        break

                if not updated_tier:
                    product["tax_included_price"] = new_price

                if moq is None or product.get("moq") == moq:
                    product["tax_included_price"] = new_price

                self._write_json_array(self.products_file, products)
                return dict(product)

        raise KeyError(f"未找到 SKU：{sku}")

    def append_operation_log(self, log_data):
        """追加操作日志。"""
        logs = self._read_json_array(self.operation_log_file)
        logs.append(dict(log_data))
        self._write_json_array(self.operation_log_file, logs)
