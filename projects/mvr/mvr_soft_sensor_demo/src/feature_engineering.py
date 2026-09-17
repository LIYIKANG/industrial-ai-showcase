from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loader import MODEL_FEATURES, calculate_specific_energy


def add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add transparent diagnostic features used for charts and explanations."""
    result = calculate_specific_energy(df)
    feed_flow = pd.to_numeric(result["feed_flow"], errors="coerce").clip(lower=1e-6)
    condensate_flow = pd.to_numeric(result["condensate_flow"], errors="coerce")
    compressor_power = pd.to_numeric(result["compressor_power"], errors="coerce")

    result["condensate_ratio"] = condensate_flow / feed_flow
    result["compressor_power_per_feed"] = compressor_power / feed_flow
    result["total_power_per_feed"] = pd.to_numeric(result["total_power"], errors="coerce") / feed_flow
    result["tds_load_proxy"] = feed_flow * pd.to_numeric(result["feed_tds"], errors="coerce")
    result["evap_temp_lift"] = pd.to_numeric(result["evap_temp"], errors="coerce") - pd.to_numeric(
        result["feed_temp"], errors="coerce"
    )
    return result.replace([np.inf, -np.inf], np.nan)


def prepare_model_data(
    df: pd.DataFrame,
    feature_cols: list[str] | None = None,
    target_col: str = "specific_energy",
) -> tuple[pd.DataFrame, pd.Series]:
    feature_cols = feature_cols or MODEL_FEATURES
    prepared = calculate_specific_energy(df)

    missing_features = [col for col in feature_cols if col not in prepared.columns]
    if missing_features:
        raise ValueError("模型输入字段缺失：" + "、".join(missing_features))

    X = prepared[feature_cols].apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(prepared[target_col], errors="coerce")
    valid_mask = y.notna() & np.isfinite(y)
    return X.loc[valid_mask], y.loc[valid_mask]

