from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cross_decomposition import PLSRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .data_loader import MODEL_FEATURES
from .feature_engineering import prepare_model_data


MODEL_OPTIONS = ["Linear Regression", "Random Forest", "PLS Regression"]


def _build_model(model_name: str, n_components: int = 3) -> Pipeline:
    if model_name == "Linear Regression":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("model", LinearRegression()),
            ]
        )

    if model_name == "PLS Regression":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                ("model", PLSRegression(n_components=n_components)),
            ]
        )

    if model_name == "Random Forest":
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=240,
                        max_depth=9,
                        min_samples_leaf=3,
                        random_state=42,
                        n_jobs=-1,
                    ),
                ),
            ]
        )

    raise ValueError(f"不支持的模型类型：{model_name}")


def train_model(
    df: pd.DataFrame,
    model_name: str = "Random Forest",
    test_size: float = 0.25,
    random_state: int = 42,
) -> dict:
    X, y = prepare_model_data(df, MODEL_FEATURES)
    if len(X) < 12:
        raise ValueError("有效数据少于 12 行，暂不建议训练模型。")

    actual_test_size = min(max(test_size, 0.2), 0.4)
    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=actual_test_size,
        random_state=random_state,
        shuffle=True,
    )

    n_components = max(1, min(3, X_train.shape[1], len(X_train) - 1))
    model = _build_model(model_name, n_components=n_components)
    model.fit(X_train, y_train)

    y_pred = np.asarray(model.predict(X_test)).reshape(-1)
    metrics = {
        "MAE": float(mean_absolute_error(y_test, y_pred)),
        "RMSE": float(np.sqrt(mean_squared_error(y_test, y_pred))),
        "R2": float(r2_score(y_test, y_pred)) if len(y_test) > 1 else np.nan,
    }

    return {
        "model_name": model_name,
        "model": model,
        "metrics": metrics,
        "feature_cols": MODEL_FEATURES,
        "y_test": y_test.reset_index(drop=True),
        "y_pred": pd.Series(y_pred, name="prediction"),
    }


def get_random_forest_feature_importance(df: pd.DataFrame) -> pd.DataFrame:
    X, y = prepare_model_data(df, MODEL_FEATURES)
    if len(X) < 12:
        raise ValueError("有效数据少于 12 行，无法计算特征重要性。")

    model = _build_model("Random Forest")
    model.fit(X, y)
    rf_model = model.named_steps["model"]
    importance_df = pd.DataFrame(
        {
            "feature": MODEL_FEATURES,
            "importance": rf_model.feature_importances_,
        }
    )
    return importance_df.sort_values("importance", ascending=False).reset_index(drop=True)

