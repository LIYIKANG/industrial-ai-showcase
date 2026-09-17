from __future__ import annotations

import numpy as np
import pandas as pd

from .data_loader import calculate_specific_energy


RISK_COMPONENT_COLUMNS = [
    "risk_power_score",
    "risk_condensate_score",
    "risk_freq_score",
    "risk_tds_score",
    "risk_pressure_score",
    "risk_cip_score",
    "risk_alarm_score",
]


def risk_level(score: float) -> str:
    if score <= 30:
        return "低风险"
    if score <= 60:
        return "中风险"
    if score <= 80:
        return "中高风险"
    return "高风险"


def add_fouling_risk(df: pd.DataFrame) -> pd.DataFrame:
    """Build a rule-based, explainable fouling and heat-transfer decay risk index."""
    result = calculate_specific_energy(df)
    result = result.copy().sort_values("timestamp").reset_index(drop=True)

    feed_flow = pd.to_numeric(result["feed_flow"], errors="coerce").clip(lower=1e-6)
    compressor_power = pd.to_numeric(result["compressor_power"], errors="coerce")
    condensate_flow = pd.to_numeric(result["condensate_flow"], errors="coerce")
    compressor_freq = pd.to_numeric(result["compressor_freq"], errors="coerce")
    feed_tds = pd.to_numeric(result["feed_tds"], errors="coerce")
    evap_pressure = pd.to_numeric(result["evap_pressure"], errors="coerce")
    alarm_flag = pd.to_numeric(result["alarm_flag"], errors="coerce").fillna(0)

    power_intensity = (compressor_power / feed_flow).replace([np.inf, -np.inf], np.nan)
    condensate_ratio = (condensate_flow / feed_flow).replace([np.inf, -np.inf], np.nan)

    power_baseline = power_intensity.expanding(min_periods=12).median().fillna(power_intensity.median())
    ratio_baseline = condensate_ratio.expanding(min_periods=12).median().fillna(condensate_ratio.median())

    power_ratio = power_intensity / power_baseline.replace(0, np.nan)
    condensate_drop = 1 - condensate_ratio / ratio_baseline.replace(0, np.nan)

    pressure_std = evap_pressure.rolling(window=24, min_periods=4).std().fillna(0)
    pressure_low = max(float(pressure_std.quantile(0.50)), 0.4)
    pressure_high = max(float(pressure_std.quantile(0.90)), pressure_low + 0.3)

    hours_since_cip = _hours_since_last_cip(result)

    result["risk_power_score"] = _scale(power_ratio, 1.05, 1.35) * 22
    result["risk_condensate_score"] = _scale(condensate_drop, 0.04, 0.22) * 20
    result["risk_freq_score"] = _scale(compressor_freq, 45, 58) * 15
    result["risk_tds_score"] = _scale(feed_tds, 80000, 150000) * 15
    result["risk_pressure_score"] = _scale(pressure_std, pressure_low, pressure_high) * 10
    result["risk_cip_score"] = _scale(hours_since_cip / 24, 5, 14) * 13
    result["risk_alarm_score"] = alarm_flag.clip(0, 1) * 5

    raw_score = result[RISK_COMPONENT_COLUMNS].sum(axis=1)
    result["hours_since_cip"] = hours_since_cip.round(1)
    result["pressure_volatility_24h"] = pressure_std.round(3)
    result["fouling_risk_index"] = raw_score.rolling(window=3, min_periods=1).mean().clip(0, 100).round(1)
    result["risk_level"] = result["fouling_risk_index"].apply(risk_level)
    return result


def get_current_risk_drivers(df: pd.DataFrame) -> list[str]:
    result = add_fouling_risk(df) if "fouling_risk_index" not in df.columns else df.copy()
    current = result.iloc[-1]
    drivers: list[str] = []

    if current.get("risk_power_score", 0) >= 8:
        drivers.append("同等进料流量下，压缩机功率负荷偏高，可能提示传热效率下降。")
    if current.get("risk_condensate_score", 0) >= 7:
        drivers.append("冷凝水/进料比下降，蒸发产水能力相对变弱。")
    if current.get("risk_freq_score", 0) >= 6:
        drivers.append("压缩机频率处于较高区间，系统可能在用更高压缩负荷维持蒸发。")
    if current.get("risk_tds_score", 0) >= 6:
        drivers.append("进水 TDS 偏高，浓缩侧结晶或结垢倾向上升。")
    if current.get("risk_pressure_score", 0) >= 5:
        drivers.append("蒸发压力或真空度波动较大，建议关注真空系统和换热稳定性。")
    if current.get("risk_cip_score", 0) >= 5:
        drivers.append(f"距离上次 CIP 约 {current.get('hours_since_cip', 0) / 24:.1f} 天，建议复核清洗周期。")
    if current.get("risk_alarm_score", 0) > 0:
        drivers.append("当前或近期存在报警信号，应结合现场报警记录复核。")

    if not drivers:
        drivers.append("当前结垢风险驱动因素不突出，建议继续观察单位电耗和冷凝水流量趋势。")
    return drivers


def _scale(values: pd.Series | np.ndarray | float, low: float, high: float) -> pd.Series:
    scaled = (pd.Series(values) - low) / (high - low)
    return scaled.clip(0, 1).fillna(0)


def _hours_since_last_cip(df: pd.DataFrame) -> pd.Series:
    timestamps = pd.to_datetime(df["timestamp"], errors="coerce")
    cip_flag = pd.to_numeric(df["cip_flag"], errors="coerce").fillna(0).astype(int)
    hours: list[float] = []
    current_hours = 0.0

    for i in range(len(df)):
        if i > 0 and pd.notna(timestamps.iloc[i]) and pd.notna(timestamps.iloc[i - 1]):
            delta_hours = (timestamps.iloc[i] - timestamps.iloc[i - 1]).total_seconds() / 3600
            current_hours += max(delta_hours, 0)
        elif i > 0:
            current_hours += 1

        if cip_flag.iloc[i] == 1:
            current_hours = 0.0
        hours.append(current_hours)

    return pd.Series(hours, index=df.index)

