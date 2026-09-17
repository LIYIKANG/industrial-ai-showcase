"""Excel / CSV parsing helpers for the chemical ATP layer.

This module complements :mod:`backend.core.file_parser` (which only
returns the textual view of a single file).  The helpers here turn the
five master sheets (``materials`` / ``tanks`` / ``inventory`` /
``inbound`` / ``open_orders`` / ``priorities``) into the structured
``list[dict]`` payloads the ATP service expects.

The functions tolerate the common Excel quirks (UTF-8 BOM, gbk, trailing
whitespace, missing columns) and are used by both the ``/api/atp/import``
route and the unit tests.
"""

from __future__ import annotations

import io
from typing import Any

import pandas as pd

EXPECTED_MATERIAL_COLUMNS = (
    "id",
    "name",
    "category",
    "unit",
    "special_control_level",
    "supply_risk",
    "lead_time_bucket",
    "lead_time_days",
    "price_sensitive",
    "safety_stock",
    "supplier_id",
    "parent_material_id",
)

EXPECTED_TANK_COLUMNS = ("id", "name", "capacity", "unit", "material_id")

EXPECTED_INVENTORY_COLUMNS = ("material_id", "tank_id", "batch", "quantity", "expiry_date")

EXPECTED_INBOUND_COLUMNS = ("id", "material_id", "quantity", "expected_date", "supplier")

EXPECTED_ORDER_COLUMNS = (
    "id",
    "customer_id",
    "customer_tier",
    "material_id",
    "quantity",
    "creation_date",
    "due_date",
    "unit",
)

EXPECTED_PRIORITY_COLUMNS = ("customer_tier", "material_category", "weight")

EXPECTED_BOM_COLUMNS = (
    "parent_material_id",
    "child_material_id",
    "quantity_per_unit",
    "unit",
    "yield_ratio",
    "scrap_rate",
)

EXPECTED_SUBSTITUTE_COLUMNS = ("material_id", "substitute_id", "priority")


def _decode_csv_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _read_table(filename: str, data: bytes) -> pd.DataFrame:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xls")):
        xls = pd.ExcelFile(io.BytesIO(data))
        sheet = xls.sheet_names[0]
        return pd.read_excel(xls, sheet_name=sheet)
    if name.endswith(".csv"):
        return pd.read_csv(io.StringIO(_decode_csv_bytes(data)))
    raise ValueError(f"unsupported file type: {filename}")


def _coerce_table(
    df: pd.DataFrame,
    expected: tuple[str, ...],
    *,
    strict_id: bool = True,
) -> list[dict[str, Any]]:
    df = df.copy()
    for column in expected:
        if column not in df.columns:
            df[column] = None
    df = df[list(expected)]
    df.columns = [str(c).strip() for c in df.columns]
    rows: list[dict[str, Any]] = []
    for _, raw in df.iterrows():
        item: dict[str, Any] = {}
        for column in expected:
            value = raw.get(column)
            if pd.isna(value):
                value = None
            if isinstance(value, str):
                value = value.strip()
            item[column] = value
        if strict_id and not item.get("id") and "id" in expected:
            continue
        rows.append(item)
    return rows


def parse_materials(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    rows = _coerce_table(df, EXPECTED_MATERIAL_COLUMNS, strict_id=True)
    for row in rows:
        try:
            val = row.get("special_control_level"); row["special_control_level"] = int(float(val)) if val not in (None, "") else 2
        except (TypeError, ValueError):
            row["special_control_level"] = 2
    return rows


def parse_tanks(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    return _coerce_table(df, EXPECTED_TANK_COLUMNS, strict_id=True)


def parse_inventory(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    rows = _coerce_table(df, EXPECTED_INVENTORY_COLUMNS, strict_id=False)
    return [row for row in rows if row.get("material_id")]


def parse_inbound(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    return _coerce_table(df, EXPECTED_INBOUND_COLUMNS, strict_id=False)


def parse_open_orders(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    return _coerce_table(df, EXPECTED_ORDER_COLUMNS, strict_id=False)


def parse_priorities(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    return _coerce_table(df, EXPECTED_PRIORITY_COLUMNS, strict_id=False)


def parse_bom(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    rows = _coerce_table(df, EXPECTED_BOM_COLUMNS, strict_id=False)
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        parent = str(row.get("parent_material_id") or "").strip()
        child = str(row.get("child_material_id") or "").strip()
        if not parent or not child:
            continue
        try:
            qpu = float(row.get("quantity_per_unit") or 0)
        except (TypeError, ValueError):
            qpu = 0.0
        if qpu <= 0:
            continue
        try:
            yr = float(row.get("yield_ratio")) if row.get("yield_ratio") not in (None, "") else None
        except (TypeError, ValueError):
            yr = None
        try:
            sr = float(row.get("scrap_rate")) if row.get("scrap_rate") not in (None, "") else 0.0
        except (TypeError, ValueError):
            sr = 0.0
        cleaned.append({
            "parent_material_id": parent,
            "child_material_id": child,
            "quantity_per_unit": qpu,
            "unit": str(row.get("unit") or "kg"),
            "yield_ratio": yr,
            "scrap_rate": sr,
        })
    return cleaned


def parse_substitutes(filename: str, data: bytes) -> list[dict[str, Any]]:
    df = _read_table(filename, data)
    rows = _coerce_table(df, EXPECTED_SUBSTITUTE_COLUMNS, strict_id=False)
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        mid = str(row.get("material_id") or "").strip()
        sid = str(row.get("substitute_id") or "").strip()
        if not mid or not sid:
            continue
        try:
            prio = int(row.get("priority") or 0)
        except (TypeError, ValueError):
            prio = 0
        cleaned.append({"material_id": mid, "substitute_id": sid, "priority": prio})
    return cleaned


PARSERS = {
    "materials": parse_materials,
    "tanks": parse_tanks,
    "inventory": parse_inventory,
    "inbound": parse_inbound,
    "open_orders": parse_open_orders,
    "priorities": parse_priorities,
    "bom": parse_bom,
    "substitutes": parse_substitutes,
}


def parse_any(kind: str, filename: str, data: bytes) -> list[dict[str, Any]]:
    parser = PARSERS.get(kind)
    if not parser:
        raise ValueError(f"unknown dataset kind: {kind}")
    return parser(filename, data)
