from __future__ import annotations

import pandas as pd

from .fouling_risk import add_fouling_risk


def generate_recommendations(df: pd.DataFrame, max_items: int = 7) -> list[str]:
    result = add_fouling_risk(df) if "fouling_risk_index" not in df.columns else df.copy()
    current = result.iloc[-1]
    history = result.tail(min(len(result), 168))
    recommendations: list[str] = []

    current_energy = float(current["specific_energy"])
    energy_mean = float(history["specific_energy"].mean())
    energy_std = float(history["specific_energy"].std() or 0)

    if current_energy > energy_mean + max(3.0, 0.6 * energy_std):
        recommendations.append("AI辅助建议：当前单位蒸发电耗高于近期均值，建议检查压缩机运行状态、蒸发压力和换热器压差。")

    if float(current["compressor_freq"]) >= max(50.0, float(history["compressor_freq"].quantile(0.75))):
        recommendations.append("AI辅助建议：当前压缩机频率偏高，建议检查是否存在过度压缩、真空波动或蒸发压力异常。")

    if float(current["feed_tds"]) >= max(100000.0, float(history["feed_tds"].quantile(0.80))):
        recommendations.append("AI辅助建议：当前进水 TDS 偏高，建议降低进料负荷、加强前处理或关注浓缩倍率设置。")

    if float(current["condensate_flow"]) < 0.9 * float(history["condensate_flow"].median()):
        recommendations.append("AI辅助建议：当前冷凝水流量低于近期中位水平，可能存在传热效率下降、负荷不足或结垢风险。")

    if float(current["fouling_risk_index"]) > 60:
        recommendations.append("AI辅助建议：当前结垢风险为中高及以上，建议关注 CIP 清洗周期，并结合换热器温差、压差和产水率复核。")
    elif float(current["fouling_risk_index"]) > 30:
        recommendations.append("AI辅助建议：当前结垢风险为中等，建议持续跟踪单位电耗、冷凝水流量和压缩机功率变化。")

    if float(current.get("hours_since_cip", 0)) > 24 * 10:
        recommendations.append("AI辅助建议：距离上次 CIP 时间较长，建议核对清洗计划，不建议将本 Demo 结果直接作为清洗指令。")

    if int(current.get("alarm_flag", 0)) == 1:
        recommendations.append("AI辅助建议：当前存在报警标记，建议优先查看 DCS/PLC 报警记录和现场巡检记录。")

    if not recommendations:
        recommendations.append("AI辅助建议：当前运行状态相对平稳，建议维持观察，并继续积累数据用于后续模型校准。")

    recommendations.append("AI辅助建议：以上内容仅用于 Demo 演示和人工决策参考，不作为自动控制指令。")
    return recommendations[:max_items]


def explain_feature_importance(importance_df: pd.DataFrame, top_n: int = 3) -> list[str]:
    if importance_df.empty:
        return ["暂无可解释的特征重要性结果。"]

    top_features = importance_df.head(top_n)["feature"].tolist()
    joined = "、".join(top_features)
    explanations = [f"Random Forest 结果显示，当前样本中影响单位蒸发电耗的主要变量包括：{joined}。"]

    feature_messages = {
        "compressor_freq": "压缩机频率通常直接反映压缩做功能力，频率升高往往会推高单位电耗。",
        "compressor_power": "压缩机功率是 MVR 系统电耗的核心来源，功率上升会显著影响单位蒸发电耗。",
        "condensate_flow": "冷凝水流量代表有效蒸发产量，产量下降时单位电耗通常升高。",
        "feed_tds": "进水 TDS 偏高会提高浓缩难度，并可能增加结晶和结垢风险。",
        "feed_flow": "进料流量改变会影响系统负荷点，过高或过低都可能降低运行效率。",
        "evap_pressure": "蒸发压力或真空度波动会影响沸点和压缩机负荷。",
        "pump_power": "循环泵功率变化可能反映循环负荷、管路阻力或换热器阻力变化。",
    }

    for feature in top_features:
        if feature in feature_messages:
            explanations.append(feature_messages[feature])
    return explanations

