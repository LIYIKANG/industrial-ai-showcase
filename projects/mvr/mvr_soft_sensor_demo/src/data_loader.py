from __future__ import annotations

from pathlib import Path
from typing import IO

import numpy as np
import pandas as pd


EXPECTED_COLUMNS = [
    "timestamp",
    "feed_flow",
    "feed_temp",
    "feed_tds",
    "feed_ph",
    "evap_temp",
    "evap_pressure",
    "compressor_freq",
    "compressor_power",
    "pump_power",
    "condensate_flow",
    "concentrate_flow",
    "total_power",
    "cip_flag",
    "alarm_flag",
]

MODEL_FEATURES = [
    "feed_flow",
    "feed_temp",
    "feed_tds",
    "feed_ph",
    "evap_temp",
    "evap_pressure",
    "compressor_freq",
    "compressor_power",
    "pump_power",
    "condensate_flow",
    "concentrate_flow",
    "cip_flag",
    "alarm_flag",
]

NUMERIC_COLUMNS = [col for col in EXPECTED_COLUMNS if col != "timestamp"]

DEFAULT_VALUES = {
    "feed_flow": 10.0,
    "feed_temp": 32.0,
    "feed_tds": 70000.0,
    "feed_ph": 7.0,
    "evap_temp": 78.0,
    "evap_pressure": -72.0,
    "compressor_freq": 42.0,
    "compressor_power": 360.0,
    "pump_power": 70.0,
    "condensate_flow": 8.5,
    "concentrate_flow": 1.5,
    "total_power": 430.0,
    "cip_flag": 0,
    "alarm_flag": 0,
}

COLUMN_ALIASES = {
    "time": "timestamp",
    "date": "timestamp",
    "datetime": "timestamp",
    "feedflow": "feed_flow",
    "feed_flow_tph": "feed_flow",
    "feed temperature": "feed_temp",
    "feedtemp": "feed_temp",
    "tds": "feed_tds",
    "conductivity": "feed_tds",
    "ph": "feed_ph",
    "evaptemperature": "evap_temp",
    "evap_temp_c": "evap_temp",
    "pressure": "evap_pressure",
    "vacuum": "evap_pressure",
    "freq": "compressor_freq",
    "frequency": "compressor_freq",
    "compressorfrequency": "compressor_freq",
    "compressorpower": "compressor_power",
    "pump": "pump_power",
    "pumppower": "pump_power",
    "condensate": "condensate_flow",
    "condensateflow": "condensate_flow",
    "concentrate": "concentrate_flow",
    "concentrateflow": "concentrate_flow",
    "power": "total_power",
    "totalpower": "total_power",
    "cip": "cip_flag",
    "alarm": "alarm_flag",
}


def _canonicalize_column_name(name: str) -> str:
    raw = str(name).strip()
    lower = raw.lower().replace(" ", "_").replace("-", "_")
    compact = lower.replace("_", "")
    if lower in EXPECTED_COLUMNS:
        return lower
    if lower in COLUMN_ALIASES:
        return COLUMN_ALIASES[lower]
    if compact in COLUMN_ALIASES:
        return COLUMN_ALIASES[compact]
    return raw


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    renamed = {_col: _canonicalize_column_name(_col) for _col in df.columns}
    return df.rename(columns=renamed)


def read_mvr_data(source: str | Path | IO[bytes], file_name: str | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Read CSV or Excel data and return a prepared MVR dataframe plus user-facing issues."""
    suffix = ""
    if file_name:
        suffix = Path(file_name).suffix.lower()
    elif isinstance(source, (str, Path)):
        suffix = Path(source).suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        raw_df = pd.read_excel(source)
    else:
        raw_df = pd.read_csv(source)

    return prepare_mvr_data(raw_df)


def prepare_mvr_data(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    issues: list[str] = []
    if raw_df.empty:
        raise ValueError("上传数据为空，请检查文件内容。")

    df = standardize_columns(raw_df.copy())
    missing_columns = [col for col in EXPECTED_COLUMNS if col not in df.columns]

    if "timestamp" not in df.columns:
        end_time = pd.Timestamp.now().floor("h")
        df["timestamp"] = pd.date_range(end=end_time, periods=len(df), freq="h")
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        if df["timestamp"].isna().all():
            end_time = pd.Timestamp.now().floor("h")
            df["timestamp"] = pd.date_range(end=end_time, periods=len(df), freq="h")
            issues.append("timestamp 无法解析，已按小时序列自动生成。")
        else:
            df["timestamp"] = df["timestamp"].ffill().bfill()

    for col in df.columns:
        if col != "timestamp":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    _add_missing_process_columns(df)
    _fill_numeric_gaps(df)
    df = calculate_specific_energy(df)

    invalid_condensate = (pd.to_numeric(df["condensate_flow"], errors="coerce") <= 0).sum()
    if invalid_condensate > 0:
        issues.append("部分 condensate_flow 小于或等于 0，单位电耗计算已使用缺失值保护。")

    if missing_columns:
        issues.append("上传数据缺少字段，系统已按经验默认值或可推导关系补齐：" + "、".join(missing_columns))

    df = df.sort_values("timestamp").reset_index(drop=True)
    return df, issues


def _add_missing_process_columns(df: pd.DataFrame) -> None:
    if "feed_flow" not in df.columns:
        if {"condensate_flow", "concentrate_flow"}.issubset(df.columns):
            df["feed_flow"] = df["condensate_flow"] + df["concentrate_flow"]
        elif "condensate_flow" in df.columns:
            df["feed_flow"] = df["condensate_flow"] / 0.85
        else:
            df["feed_flow"] = DEFAULT_VALUES["feed_flow"]

    if "condensate_flow" not in df.columns:
        df["condensate_flow"] = df["feed_flow"] * 0.85

    if "concentrate_flow" not in df.columns:
        df["concentrate_flow"] = np.maximum(df["feed_flow"] - df["condensate_flow"], 0.3)

    if "pump_power" not in df.columns:
        if "total_power" in df.columns:
            df["pump_power"] = df["total_power"] * 0.15
        else:
            df["pump_power"] = 30 + 3.5 * df["feed_flow"]

    if "compressor_power" not in df.columns:
        if "total_power" in df.columns:
            df["compressor_power"] = df["total_power"] * 0.85
        else:
            df["compressor_power"] = DEFAULT_VALUES["compressor_power"]

    if "total_power" not in df.columns:
        df["total_power"] = df["compressor_power"] + df["pump_power"]

    for col, default in DEFAULT_VALUES.items():
        if col not in df.columns:
            df[col] = default


def _fill_numeric_gaps(df: pd.DataFrame) -> None:
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        if df[col].isna().all():
            df[col] = DEFAULT_VALUES[col]
            continue
        median_value = df[col].median()
        df[col] = df[col].interpolate(limit_direction="both").ffill().bfill().fillna(median_value)

    df["cip_flag"] = df["cip_flag"].fillna(0).round().clip(0, 1).astype(int)
    df["alarm_flag"] = df["alarm_flag"].fillna(0).round().clip(0, 1).astype(int)


def calculate_specific_energy(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    total_power = pd.to_numeric(result["total_power"], errors="coerce")
    condensate_flow = pd.to_numeric(result["condensate_flow"], errors="coerce")
    safe_condensate = condensate_flow.where(condensate_flow > 0)
    result["specific_energy"] = (total_power / safe_condensate).replace([np.inf, -np.inf], np.nan)
    if result["specific_energy"].notna().any():
        result["specific_energy"] = result["specific_energy"].fillna(result["specific_energy"].median())
    return result

