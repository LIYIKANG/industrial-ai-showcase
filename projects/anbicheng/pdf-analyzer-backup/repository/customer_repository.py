"""
repository/customer_repository.py
=================================
客户关键词系统的数据仓储。

默认使用 SQLAlchemy，因此本地 SQLite 和生产 PostgreSQL 共用同一套业务代码。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from auth.models import CustomerOperationLog, CustomerProduct
from core.config import settings


def _read_json_obj(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _write_json_obj(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _lookup_values(product: Dict[str, Any], business_key_id: str) -> set[str]:
    values = {
        product.get(business_key_id),
        product.get("internal_sku"),
        product.get("part_no"),
        product.get("product_code"),
    }
    values.update(product.get("sku_aliases") or [])
    return {str(v) for v in values if v not in (None, "")}


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _to_float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or _is_missing(value):
        return None
    try:
        return float(str(value).replace("¥", "").replace("￥", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _price_values(product: Dict[str, Any]) -> list[float]:
    values: list[float] = []
    direct = _to_float_or_none(product.get("tax_included_price"))
    if direct is not None:
        values.append(direct)
    for tier in product.get("price_tiers", []) or []:
        if not isinstance(tier, dict):
            continue
        price = _to_float_or_none(tier.get("tax_included_price"))
        if price is not None:
            values.append(price)
    for price_row in product.get("prices", []) or []:
        if not isinstance(price_row, dict):
            continue
        price = _to_float_or_none(price_row.get("tax_included_price"))
        if price is not None:
            values.append(price)
    return values


def _first_text(product: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = product.get(key)
        if not _is_missing(value):
            return str(value)
    return ""


class CustomerRepository:
    """客户产品和操作日志仓储。"""

    def __init__(self, db: Session, seed_file: Path | None = None):
        self.db = db
        self.seed_file = seed_file or settings.CUSTOMER_PRODUCT_SEED_FILE

    def ensure_seeded(self) -> None:
        """本地库为空时导入 seed 数据。生产环境可替换为真实数据导入。"""
        if self.db.query(CustomerProduct).count() > 0:
            return
        if not self.seed_file.exists():
            return
        with self.seed_file.open("r", encoding="utf-8") as f:
            products = json.load(f)
        if not isinstance(products, list):
            raise ValueError(f"{self.seed_file} 的顶层结构必须是数组")

        for product in products:
            key_value = product.get("internal_sku") or product.get("part_no") or product.get("product_code")
            if not key_value:
                continue
            self.db.add(
                CustomerProduct(
                    business_key_id="internal_sku",
                    business_key_value=str(key_value),
                    product_json=_write_json_obj(product),
                    company_id=None,
                )
            )
        self.db.commit()

    def _query_scope(self, company_id: int | None):
        query = self.db.query(CustomerProduct)
        if company_id is not None:
            query = query.filter(
                or_(
                    CustomerProduct.company_id == company_id,
                    CustomerProduct.company_id.is_(None),
                )
            )
        return query

    def product_to_dict(self, record: CustomerProduct | None) -> Dict[str, Any] | None:
        if record is None:
            return None
        product = _read_json_obj(record.product_json, {})
        product["_record_id"] = record.id
        product["_business_key_id"] = record.business_key_id
        product["_business_key_value"] = record.business_key_value
        return product

    def product_to_row(self, record: CustomerProduct) -> Dict[str, Any]:
        """把配置化产品 JSON 压平成前端列表行。"""
        product = _read_json_obj(record.product_json, {})
        price_values = _price_values(product)
        lowest_price = min(price_values) if price_values else None
        tiers = product.get("price_tiers", []) or []
        prices = product.get("prices", []) or []
        sku = _first_text(
            product,
            "sku",
            "internal_sku",
            "product_code",
            "part_no",
            record.business_key_id,
        ) or record.business_key_value
        return {
            "id": record.id,
            "business_key_id": record.business_key_id,
            "business_key_value": record.business_key_value,
            "sku": sku,
            "internal_sku": _first_text(product, "internal_sku"),
            "product_code": _first_text(product, "product_code"),
            "item_no": _first_text(product, "item_no", "customer_item_no"),
            "product_name": _first_text(product, "product_name", "name", "part_name"),
            "part_no": _first_text(product, "part_no", "model", "description"),
            "material_special": _first_text(product, "material_special", "material"),
            "hardness": _first_text(product, "hardness"),
            "color": _first_text(product, "color", "colour"),
            "tax_included_price": product.get("tax_included_price", ""),
            "lowest_price": lowest_price,
            "has_price": lowest_price is not None,
            "price_tiers_count": len(tiers) if isinstance(tiers, list) else 0,
            "prices_count": len(prices) if isinstance(prices, list) else 0,
            "source_table": _first_text(product, "source_table"),
            "company_id": record.company_id,
            "created_at": record.created_at.isoformat() + "Z" if record.created_at else "",
            "updated_at": record.updated_at.isoformat() + "Z" if record.updated_at else "",
        }

    def get_product_record_by_id(
        self,
        record_id: int,
        company_id: int | None = None,
    ) -> CustomerProduct | None:
        query = self._query_scope(company_id).filter(CustomerProduct.id == record_id)
        return query.first()

    def list_product_rows(
        self,
        *,
        company_id: int | None = None,
        q: str = "",
        material_special: str = "",
        color: str = "",
        hardness: str = "",
        has_price: str = "",
        price_min: float | None = None,
        price_max: float | None = None,
        sort_by: str = "updated_at",
        sort_dir: str = "desc",
        page: int = 1,
        size: int = 20,
    ) -> Dict[str, Any]:
        """产品主数据列表。当前用 Python 过滤，真实库可替换为 SQL 查询优化。"""
        self.ensure_seeded()
        records = self._query_scope(company_id).all()
        all_rows = [self.product_to_row(record) for record in records]

        facets = {
            "materials": sorted({row["material_special"] for row in all_rows if row["material_special"]}),
            "colors": sorted({row["color"] for row in all_rows if row["color"]}),
            "hardnesses": sorted({row["hardness"] for row in all_rows if row["hardness"]}),
        }

        query_text = str(q or "").strip().lower()
        rows = all_rows
        if query_text:
            searchable = (
                "sku",
                "internal_sku",
                "product_code",
                "item_no",
                "product_name",
                "part_no",
                "material_special",
                "hardness",
                "color",
            )
            rows = [
                row
                for row in rows
                if any(query_text in str(row.get(key, "")).lower() for key in searchable)
            ]
        if material_special:
            rows = [row for row in rows if row.get("material_special") == material_special]
        if color:
            rows = [row for row in rows if row.get("color") == color]
        if hardness:
            rows = [row for row in rows if row.get("hardness") == hardness]
        if has_price == "yes":
            rows = [row for row in rows if row.get("has_price")]
        elif has_price == "no":
            rows = [row for row in rows if not row.get("has_price")]
        if price_min is not None:
            rows = [
                row
                for row in rows
                if row.get("lowest_price") is not None and row["lowest_price"] >= price_min
            ]
        if price_max is not None:
            rows = [
                row
                for row in rows
                if row.get("lowest_price") is not None and row["lowest_price"] <= price_max
            ]

        sort_key = sort_by if sort_by in {
            "sku",
            "item_no",
            "product_name",
            "material_special",
            "hardness",
            "color",
            "lowest_price",
            "updated_at",
            "created_at",
        } else "updated_at"
        reverse = sort_dir != "asc"

        if sort_key == "lowest_price":
            priced_rows = [row for row in rows if row.get("lowest_price") is not None]
            no_price_rows = [row for row in rows if row.get("lowest_price") is None]
            priced_rows = sorted(
                priced_rows,
                key=lambda row: row.get("lowest_price") or 0,
                reverse=reverse,
            )
            rows = priced_rows + no_price_rows
        else:
            rows = sorted(
                rows,
                key=lambda row: str(row.get(sort_key) or "").lower(),
                reverse=reverse,
            )
        total = len(rows)
        page = max(1, int(page or 1))
        size = max(1, min(200, int(size or 20)))
        start = (page - 1) * size
        paged = rows[start:start + size]
        pages = (total + size - 1) // size if total else 1
        return {
            "rows": paged,
            "total": total,
            "page": page,
            "size": size,
            "pages": pages,
            "facets": facets,
        }

    def product_logs(
        self,
        *,
        product: Dict[str, Any],
        limit: int = 20,
    ) -> Iterable[CustomerOperationLog]:
        values = {
            product.get("business_key_value"),
            product.get("sku"),
            product.get("internal_sku"),
            product.get("product_code"),
            product.get("part_no"),
            product.get("item_no"),
        }
        keys = [str(value) for value in values if not _is_missing(value)]
        if not keys:
            return []
        return (
            self.db.query(CustomerOperationLog)
            .filter(CustomerOperationLog.business_key_value.in_(keys))
            .order_by(CustomerOperationLog.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_by_business_key(
        self,
        business_key_id: str,
        business_key_value: str,
        company_id: int | None = None,
    ) -> CustomerProduct | None:
        """按主键查询，兼容 SKU、Part NO.、产品编号和别名。"""
        value = str(business_key_value or "").strip()
        if not value:
            return None

        exact = (
            self._query_scope(company_id)
            .filter(
                CustomerProduct.business_key_id == business_key_id,
                CustomerProduct.business_key_value == value,
            )
            .order_by(CustomerProduct.company_id.desc().nullslast(), CustomerProduct.id.desc())
            .first()
        )
        if exact:
            return exact

        for record in self._query_scope(company_id).all():
            product = _read_json_obj(record.product_json, {})
            if value in _lookup_values(product, business_key_id):
                return record
        return None

    def create_product(
        self,
        product_data: Dict[str, Any],
        business_key_id: str,
        company_id: int | None = None,
    ) -> CustomerProduct:
        key_value = str(product_data.get(business_key_id) or "").strip()
        record = CustomerProduct(
            business_key_id=business_key_id,
            business_key_value=key_value,
            product_json=_write_json_obj(product_data),
            company_id=company_id,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_product(
        self,
        record: CustomerProduct,
        product_data: Dict[str, Any],
        business_key_id: str | None = None,
    ) -> CustomerProduct:
        if business_key_id:
            record.business_key_id = business_key_id
            record.business_key_value = str(product_data.get(business_key_id) or "").strip()
        record.product_json = _write_json_obj(product_data)
        record.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(record)
        return record

    def update_product_price(
        self,
        record: CustomerProduct,
        price_field_id: str,
        new_price: float,
        *,
        moq: int | None = None,
    ) -> CustomerProduct:
        """更新主价格，若存在匹配 MOQ 阶梯价则优先更新阶梯价。"""
        product = _read_json_obj(record.product_json, {})
        updated_tier = False
        for tier in product.get("price_tiers", []) or []:
            if moq is not None and tier.get("moq") == moq:
                tier[price_field_id] = new_price
                updated_tier = True
                break
        if not updated_tier:
            product[price_field_id] = new_price
        if moq is None or product.get("moq") == moq:
            product[price_field_id] = new_price
        return self.update_product(record, product)

    def append_operation_log(
        self,
        *,
        username: str,
        company_id: int | None,
        job_id: str | None,
        operation_type: str,
        business_key_id: str | None,
        business_key_value: str | None,
        raw_input: str,
        parsed_fields: Dict[str, Any],
        process_result: Dict[str, Any],
        old_value: Any = None,
        new_value: Any = None,
    ) -> CustomerOperationLog:
        log = CustomerOperationLog(
            username=username,
            company_id=company_id,
            job_id=job_id,
            operation_type=operation_type,
            business_key_id=business_key_id,
            business_key_value=business_key_value,
            raw_input=raw_input,
            parsed_fields_json=_write_json_obj(parsed_fields),
            process_result_json=_write_json_obj(process_result),
            old_value_json=_write_json_obj(old_value) if old_value is not None else None,
            new_value_json=_write_json_obj(new_value) if new_value is not None else None,
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def recent_logs(self, username: str | None = None, limit: int = 20) -> Iterable[CustomerOperationLog]:
        query = self.db.query(CustomerOperationLog)
        if username:
            query = query.filter(CustomerOperationLog.username == username)
        return query.order_by(CustomerOperationLog.created_at.desc()).limit(limit).all()
