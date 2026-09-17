from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.data_generator import generate_sample_data, save_sample_data
from src.data_loader import EXPECTED_COLUMNS, read_mvr_data
from src.fouling_risk import RISK_COMPONENT_COLUMNS, add_fouling_risk, get_current_risk_drivers
from src.model_training import MODEL_OPTIONS, get_random_forest_feature_importance, train_model
from src.recommendation import explain_feature_importance, generate_recommendations


PROJECT_ROOT = Path(__file__).resolve().parent
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "sample_mvr_data.csv"

FEATURE_LABELS = {
    "feed_flow": "进料流量",
    "feed_temp": "进料温度",
    "feed_tds": "进水TDS",
    "feed_ph": "进水pH",
    "evap_temp": "蒸发温度",
    "evap_pressure": "蒸发压力/真空度",
    "compressor_freq": "压缩机频率",
    "compressor_power": "压缩机功率",
    "pump_power": "循环泵功率",
    "condensate_flow": "冷凝水流量",
    "concentrate_flow": "浓缩液流量",
    "cip_flag": "CIP标记",
    "alarm_flag": "报警标记",
}

RISK_COMPONENT_LABELS = {
    "risk_power_score": "功率/负荷偏高",
    "risk_condensate_score": "产水率下降",
    "risk_freq_score": "频率偏高",
    "risk_tds_score": "TDS偏高",
    "risk_pressure_score": "压力波动",
    "risk_cip_score": "CIP间隔",
    "risk_alarm_score": "报警信号",
}

VISUAL_COLUMNS = [
    "feed_flow",
    "feed_temp",
    "feed_tds",
    "evap_temp",
    "evap_pressure",
    "compressor_freq",
    "compressor_power",
    "pump_power",
    "condensate_flow",
    "total_power",
    "specific_energy",
    "fouling_risk_index",
]

VISUAL_LABELS = {
    **FEATURE_LABELS,
    "specific_energy": "单位蒸发电耗",
    "fouling_risk_index": "结垢风险指数",
    "total_power": "系统总功率",
}

VARIABLE_UNITS = {
    "feed_flow": "t/h",
    "feed_temp": "℃",
    "feed_tds": "mg/L",
    "feed_ph": "",
    "evap_temp": "℃",
    "evap_pressure": "kPa 或真空度",
    "compressor_freq": "Hz",
    "compressor_power": "kW",
    "pump_power": "kW",
    "condensate_flow": "t/h",
    "concentrate_flow": "t/h",
    "total_power": "kW",
    "specific_energy": "kWh/t",
    "fouling_risk_index": "0-100",
    "cip_flag": "0/1",
    "alarm_flag": "0/1",
}

FIELD_MEANINGS = {
    "feed_flow": "进入 MVR 系统的废水负荷，决定蒸发系统处理量。",
    "feed_temp": "进料温度越高，通常预热负担越低。",
    "feed_tds": "盐分或电导率代理指标，越高越容易增加浓缩难度和结垢倾向。",
    "feed_ph": "酸碱性会影响腐蚀、结晶和药剂条件。",
    "evap_temp": "蒸发器运行温度，和真空度、压缩机负荷共同影响能耗。",
    "evap_pressure": "蒸发压力或真空度，波动可能反映真空系统或换热状态变化。",
    "compressor_freq": "蒸汽压缩机频率，频率偏高通常意味着压缩做功增加。",
    "compressor_power": "MVR 主耗电设备功率，是单位电耗的核心来源。",
    "pump_power": "循环泵功率，反映循环流量、阻力和换热器状态。",
    "condensate_flow": "冷凝水产量，代表有效蒸发产出。",
    "concentrate_flow": "浓缩液流量，与浓缩倍率和排放策略有关。",
    "total_power": "MVR 系统总功率，用于计算单位蒸发电耗。",
    "specific_energy": "每蒸发 1 吨水消耗的电量，是能耗诊断核心 KPI。",
    "fouling_risk_index": "规则法构建的结垢/传热衰减风险代理指标。",
}

MODEL_EXPLANATION_ROWS = [
    {
        "模型": "Linear Regression",
        "在 Demo 里的意义": "基础对照模型",
        "可以怎么讲给客户": "先用一条线描述变量和单位电耗的关系，给 AI 诊断提供最低基准。",
        "优势": "透明、快、容易解释方向性。",
        "局限": "只能表达近似线性关系，难处理复杂耦合和阈值效应。",
    },
    {
        "模型": "Random Forest",
        "在 Demo 里的意义": "主要预测模型",
        "可以怎么讲给客户": "用多棵决策树学习复杂运行工况，既能预测单位电耗，也能输出影响因素排序。",
        "优势": "适合非线性、多变量耦合，特征重要性适合做客户演示。",
        "局限": "不是机理模型，外推到未见过的极端工况时需要谨慎。",
    },
    {
        "模型": "PLS Regression",
        "在 Demo 里的意义": "软测量参考模型",
        "可以怎么讲给客户": "把一组高度相关的过程变量压缩成少数潜变量，再估算单位电耗。",
        "优势": "在化工、过程工业软测量中常见，适合多变量强相关场景。",
        "局限": "非线性能力有限，通常需要结合工况分段或在线更新。",
    },
    {
        "模型": "JITPLS / LWPLS",
        "在 Demo 里的意义": "后续预留方向",
        "可以怎么讲给客户": "未来可根据当前工况自动寻找相似历史片段，建立局部软测量模型。",
        "优势": "适合原水成分和负荷经常变化的现场。",
        "局限": "第一版 Demo 暂不强制实现，需要更多现场数据验证。",
    },
]

METRIC_EXPLANATION_ROWS = [
    {"指标": "MAE", "含义": "平均绝对误差，表示预测值平均偏离真实单位电耗多少。", "怎么看": "越小越好，直观好讲。"},
    {"指标": "RMSE", "含义": "均方根误差，对大误差更敏感。", "怎么看": "越小越好，可用于发现模型是否偶尔偏差很大。"},
    {"指标": "R2", "含义": "模型解释数据波动的比例。", "怎么看": "越接近 1 越好；小样本或异常数据下只作参考。"},
]


st.set_page_config(
    page_title="MVR废水蒸发 AI软测量与能耗诊断 Demo",
    layout="wide",
)


def main() -> None:
    _ensure_sample_file()
    _render_style()

    st.title("MVR废水蒸发 AI软测量与能耗诊断 Demo")
    st.caption("本系统仅实现 AI诊断层 和 运行建议层，不连接 PLC/DCS，不执行真实设备控制。")

    page = st.radio(
        "页面选择",
        ["运行诊断总览", "数据认知与模型说明"],
        horizontal=True,
    )
    st.caption("建议演示顺序：先看数据认知与模型说明，再回到运行诊断总览。")

    df, issues, data_source = _load_page_data()
    df = add_fouling_risk(df)

    _render_data_messages(df, issues, data_source)
    if page == "数据认知与模型说明":
        _render_data_story_page(df)
    else:
        _render_diagnosis_page(df)


def _render_diagnosis_page(df: pd.DataFrame) -> None:
    _render_overview(df)
    _render_trends(df)
    _render_model_training(df)
    _render_energy_factor_analysis(df)
    _render_fouling_analysis(df)
    _render_recommendations(df)
    _render_data_preview(df)


def _ensure_sample_file() -> None:
    if not SAMPLE_DATA_PATH.exists():
        save_sample_data(SAMPLE_DATA_PATH, n_rows=720, seed=42)


def _load_page_data() -> tuple[pd.DataFrame, list[str], str]:
    st.subheader("1. 数据上传区")
    col_upload, col_config = st.columns([2, 1])

    with col_upload:
        uploaded_file = st.file_uploader("上传 MVR 运行数据 CSV / Excel", type=["csv", "xlsx", "xls"])

    with col_config:
        sample_rows = st.slider("模拟数据行数", min_value=120, max_value=1440, value=720, step=120)
        sample_seed = st.number_input("模拟数据随机种子", min_value=1, max_value=9999, value=42, step=1)

    if uploaded_file is not None:
        df, issues = read_mvr_data(uploaded_file, uploaded_file.name)
        return df, issues, f"上传文件：{uploaded_file.name}"

    df = generate_sample_data(n_rows=sample_rows, seed=int(sample_seed))
    df, issues = read_mvr_data(_dataframe_to_csv_buffer(df), "sample_mvr_data.csv")
    issues.insert(0, "未上传数据，系统已自动生成模拟样例数据。")
    return df, issues, "系统模拟数据"


def _dataframe_to_csv_buffer(df: pd.DataFrame):
    from io import StringIO

    buffer = StringIO()
    df.to_csv(buffer, index=False)
    buffer.seek(0)
    return buffer


def _render_data_messages(df: pd.DataFrame, issues: list[str], data_source: str) -> None:
    st.info(f"当前数据源：{data_source}，数据行数：{len(df)}。")
    for issue in issues:
        st.warning(issue)
    if len(df) < 200:
        st.warning("当前数据量较少，当前结果仅用于Demo演示。")


def _render_overview(df: pd.DataFrame) -> None:
    st.subheader("2. 运行状态总览")
    current = df.iloc[-1]
    metrics = [
        ("当前进料流量", f"{current['feed_flow']:.2f} t/h"),
        ("当前冷凝水流量", f"{current['condensate_flow']:.2f} t/h"),
        ("当前系统总功率", f"{current['total_power']:.1f} kW"),
        ("当前单位蒸发电耗", f"{current['specific_energy']:.1f} kWh/t"),
        ("当前结垢风险指数", f"{current['fouling_risk_index']:.1f} / 100"),
        ("当前风险等级", str(current["risk_level"])),
    ]
    for start in range(0, len(metrics), 3):
        metric_cols = st.columns(3)
        for col, (label, value) in zip(metric_cols, metrics[start : start + 3]):
            col.metric(label, value)


def _render_trends(df: pd.DataFrame) -> None:
    st.subheader("3. 趋势图")
    trend_cols = st.columns(2)
    charts = [
        ("specific_energy", "单位蒸发电耗趋势", "kWh/t"),
        ("total_power", "系统总功率趋势", "kW"),
        ("feed_tds", "进水TDS趋势", "mg/L"),
        ("condensate_flow", "冷凝水流量趋势", "t/h"),
        ("fouling_risk_index", "结垢风险指数趋势", "0-100"),
    ]
    for i, (col, title, unit) in enumerate(charts):
        with trend_cols[i % 2]:
            fig = px.line(df, x="timestamp", y=col, title=title)
            fig.update_layout(height=280, margin=dict(l=20, r=20, t=45, b=20), yaxis_title=unit, xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)


def _render_model_training(df: pd.DataFrame) -> None:
    st.subheader("4. 模型训练区")
    col_model, col_button = st.columns([2, 1])
    with col_model:
        selected_model = st.selectbox("选择模型", MODEL_OPTIONS, index=1)
    with col_button:
        st.write("")
        st.write("")
        train_clicked = st.button("训练模型", use_container_width=True)

    signature = (selected_model, int(pd.util.hash_pandas_object(df, index=True).sum()))
    previous_signature = st.session_state.get("model_result_signature")
    if previous_signature is not None and previous_signature != signature and not train_clicked:
        st.info("数据或模型已修改。请点击“训练模型”，生成与当前选择对应的预测结果。")
        return
    if train_clicked or "model_result" not in st.session_state:
        try:
            st.session_state["model_result"] = train_model(df, selected_model)
            st.session_state["model_result_signature"] = signature
        except Exception as exc:
            st.error(f"模型训练失败：{exc}")
            return

    result = st.session_state["model_result"]
    metrics = result["metrics"]
    metric_cols = st.columns(3)
    metric_cols[0].metric("MAE", f"{metrics['MAE']:.2f}")
    metric_cols[1].metric("RMSE", f"{metrics['RMSE']:.2f}")
    metric_cols[2].metric("R2", f"{metrics['R2']:.3f}")

    pred_df = pd.DataFrame({"真实值": result["y_test"], "预测值": result["y_pred"]})
    fig = px.scatter(pred_df, x="真实值", y="预测值", title=f"{result['model_name']} 预测效果")
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=45, b=20))
    st.plotly_chart(fig, use_container_width=True)


def _render_energy_factor_analysis(df: pd.DataFrame) -> None:
    st.subheader("5. 能耗影响因素分析")
    try:
        importance_df = get_random_forest_feature_importance(df)
    except Exception as exc:
        st.warning(f"暂无法计算特征重要性：{exc}")
        return

    top10 = importance_df.head(10).copy()
    top10["feature_label"] = top10["feature"].map(FEATURE_LABELS).fillna(top10["feature"])
    fig = px.bar(
        top10.sort_values("importance"),
        x="importance",
        y="feature_label",
        orientation="h",
        title="Random Forest Top 10 影响因素",
    )
    fig.update_layout(height=380, margin=dict(l=20, r=20, t=45, b=20), xaxis_title="重要性", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)

    for text in explain_feature_importance(importance_df):
        st.write(text)


def _render_fouling_analysis(df: pd.DataFrame) -> None:
    st.subheader("6. 结垢风险分析")
    current = df.iloc[-1]
    col_score, col_drivers = st.columns([1, 2])

    with col_score:
        st.metric("当前 fouling_risk_index", f"{current['fouling_risk_index']:.1f}")
        st.metric("风险等级", str(current["risk_level"]))
        st.progress(min(float(current["fouling_risk_index"]) / 100, 1.0))

    with col_drivers:
        st.write("导致风险升高的主要原因")
        for item in get_current_risk_drivers(df):
            st.markdown(f"- {item}")

    component_df = pd.DataFrame(
        {
            "风险来源": [RISK_COMPONENT_LABELS[col] for col in RISK_COMPONENT_COLUMNS],
            "评分": [float(current.get(col, 0)) for col in RISK_COMPONENT_COLUMNS],
        }
    )
    fig = px.bar(component_df, x="评分", y="风险来源", orientation="h", title="当前结垢风险拆解")
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=45, b=20), xaxis_title="风险贡献分", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)


def _render_recommendations(df: pd.DataFrame) -> None:
    st.subheader("7. AI运行建议")
    for rec in generate_recommendations(df):
        st.markdown(f"- {rec}")


def _render_data_preview(df: pd.DataFrame) -> None:
    with st.expander("查看当前数据字段与样例"):
        st.write("标准字段")
        st.code("\n".join(EXPECTED_COLUMNS), language="text")
        st.dataframe(df.tail(20), use_container_width=True)


def _render_data_story_page(df: pd.DataFrame) -> None:
    st.subheader("2. 数据认知驾驶舱")
    st.write("这一页用于把 MVR 运行数据讲清楚：先看整体负荷和能耗画像，再看变量之间怎样共同影响单位蒸发电耗。")

    _render_data_profile_cards(df)
    _render_condition_heatmap(df)
    _render_distribution_and_relationship(df)
    _render_correlation_heatmap(df)
    _render_field_dictionary(df)
    _render_model_explanation()


def _render_data_profile_cards(df: pd.DataFrame) -> None:
    sample_hours = _median_sample_hours(df)
    timestamps = pd.to_datetime(df["timestamp"], errors="coerce")
    time_span_hours = 0.0
    if timestamps.notna().sum() >= 2:
        time_span_hours = (timestamps.max() - timestamps.min()).total_seconds() / 3600

    total_condensate = float(df["condensate_flow"].sum() * sample_hours)
    total_electricity = float(df["total_power"].sum() * sample_hours)
    avg_energy = float(df["specific_energy"].mean())
    high_risk_ratio = float((df["fouling_risk_index"] > 60).mean() * 100)

    metrics = [
        ("数据点数", f"{len(df):,}"),
        ("时间跨度", _format_hours(time_span_hours)),
        ("典型采样间隔", _format_hours(sample_hours)),
        ("估算累计产水", f"{total_condensate:,.0f} t"),
        ("估算累计电量", f"{total_electricity:,.0f} kWh"),
        ("平均单位电耗", f"{avg_energy:.1f} kWh/t"),
    ]
    for start in range(0, len(metrics), 3):
        metric_cols = st.columns(3)
        for col, (label, value) in zip(metric_cols, metrics[start : start + 3]):
            col.metric(label, value)

    st.caption(f"风险指数超过 60 的样本占比约 {high_risk_ratio:.1f}%。流量为 t/h，累计量按典型采样间隔估算。")


def _render_condition_heatmap(df: pd.DataFrame) -> None:
    st.subheader("3. 工况热力图")
    st.write("颜色越靠右侧暖色，表示该变量在当前数据窗口中越接近自身高位；适合快速发现高 TDS、高频率、高电耗同时出现的时段。")

    key_cols = [
        "feed_flow",
        "feed_tds",
        "compressor_freq",
        "compressor_power",
        "condensate_flow",
        "specific_energy",
        "fouling_risk_index",
    ]
    available_cols = [col for col in key_cols if col in df.columns]
    window_df = df.tail(min(len(df), 168)).copy()
    normalized = pd.DataFrame(index=window_df.index)

    for col in available_cols:
        series = pd.to_numeric(window_df[col], errors="coerce")
        low = series.min()
        high = series.max()
        if pd.isna(low) or pd.isna(high) or high == low:
            normalized[col] = 0.0
        else:
            normalized[col] = (series - low) / (high - low)

    time_labels = pd.to_datetime(window_df["timestamp"], errors="coerce").dt.strftime("%m-%d %H:%M").tolist()
    y_labels = [VISUAL_LABELS.get(col, col) for col in available_cols]
    fig = px.imshow(
        normalized[available_cols].T,
        x=time_labels,
        y=y_labels,
        zmin=0,
        zmax=1,
        aspect="auto",
        color_continuous_scale=["#22c55e", "#facc15", "#ef4444"],
        title="最近工况归一化热力图",
    )
    fig.update_layout(height=380, margin=dict(l=20, r=20, t=45, b=20), xaxis_title="", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True)


def _render_distribution_and_relationship(df: pd.DataFrame) -> None:
    st.subheader("4. 变量分布与能耗关系")
    available = [col for col in VISUAL_COLUMNS if col in df.columns]
    left, right = st.columns(2)

    with left:
        dist_var = st.selectbox(
            "选择变量查看分布",
            available,
            index=available.index("specific_energy") if "specific_energy" in available else 0,
            format_func=_variable_label,
        )
        fig = px.histogram(
            df,
            x=dist_var,
            nbins=35,
            marginal="box",
            title=f"{_variable_label(dist_var)} 分布",
        )
        fig.update_layout(height=360, margin=dict(l=20, r=20, t=45, b=20), xaxis_title=_variable_label(dist_var))
        st.plotly_chart(fig, use_container_width=True)

    with right:
        x_default = "compressor_freq" if "compressor_freq" in available else available[0]
        x_var = st.selectbox(
            "选择横轴变量观察单位电耗",
            available,
            index=available.index(x_default),
            format_func=_variable_label,
        )
        fig = px.scatter(
            df,
            x=x_var,
            y="specific_energy",
            color="fouling_risk_index",
            size="total_power",
            hover_data=["timestamp", "feed_tds", "condensate_flow", "compressor_power"],
            color_continuous_scale="Turbo",
            title=f"{_variable_label(x_var)} 与单位蒸发电耗",
        )
        fig.update_layout(
            height=360,
            margin=dict(l=20, r=20, t=45, b=20),
            xaxis_title=_variable_label(x_var),
            yaxis_title="单位蒸发电耗 (kWh/t)",
        )
        st.plotly_chart(fig, use_container_width=True)


def _render_correlation_heatmap(df: pd.DataFrame) -> None:
    st.subheader("5. 变量相关性")
    corr_cols = [
        "feed_flow",
        "feed_tds",
        "evap_pressure",
        "compressor_freq",
        "compressor_power",
        "pump_power",
        "condensate_flow",
        "total_power",
        "specific_energy",
        "fouling_risk_index",
    ]
    corr_cols = [col for col in corr_cols if col in df.columns]
    corr_df = df[corr_cols].apply(pd.to_numeric, errors="coerce").corr()
    corr_df.index = [VISUAL_LABELS.get(col, col) for col in corr_df.index]
    corr_df.columns = [VISUAL_LABELS.get(col, col) for col in corr_df.columns]

    fig = px.imshow(
        corr_df,
        text_auto=".2f",
        zmin=-1,
        zmax=1,
        color_continuous_scale="RdBu_r",
        title="关键变量相关性热力图",
    )
    fig.update_layout(height=620, margin=dict(l=20, r=20, t=45, b=20))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("相关性不等于因果关系，但很适合帮助客户快速理解哪些变量经常一起变化。")


def _render_field_dictionary(df: pd.DataFrame) -> None:
    st.subheader("6. 数据字段解释")
    rows = []
    for col in EXPECTED_COLUMNS + ["specific_energy", "fouling_risk_index"]:
        if col not in df.columns and col not in {"specific_energy", "fouling_risk_index"}:
            continue
        rows.append(
            {
                "字段": col,
                "中文名称": VISUAL_LABELS.get(col, FEATURE_LABELS.get(col, col)),
                "单位": VARIABLE_UNITS.get(col, ""),
                "现场含义": FIELD_MEANINGS.get(col, "用于补充运行状态或诊断判断。"),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_model_explanation() -> None:
    st.subheader("7. 模型为什么存在")
    st.write("这个 Demo 里不是为了堆模型，而是让不同模型承担不同解释角色：基准、主预测、软测量参考和未来在线升级方向。")
    st.dataframe(pd.DataFrame(MODEL_EXPLANATION_ROWS), use_container_width=True, hide_index=True)

    st.write("一句话演示口径")
    st.markdown("- Linear Regression 是标尺，告诉客户最简单的方法能做到什么程度。")
    st.markdown("- Random Forest 是主诊断模型，用于预测单位电耗并解释主要影响因素。")
    st.markdown("- PLS Regression 是过程工业软测量参考，适合讲“多变量相关时如何估算看不见的 KPI”。")
    st.markdown("- JITPLS / LWPLS 是后续现场化方向，用于适应原水成分和负荷不断变化的工况。")

    st.write("评价指标怎么讲")
    st.dataframe(pd.DataFrame(METRIC_EXPLANATION_ROWS), use_container_width=True, hide_index=True)
    st.info("软测量的核心价值：不新增昂贵仪表，也不直接控制设备，而是用已有运行数据估算关键 KPI、发现异常趋势，并把建议交给人工确认。")


def _variable_label(col: str) -> str:
    label = VISUAL_LABELS.get(col, FEATURE_LABELS.get(col, col))
    unit = VARIABLE_UNITS.get(col, "")
    return f"{label} ({unit})" if unit else label


def _median_sample_hours(df: pd.DataFrame) -> float:
    timestamps = pd.to_datetime(df["timestamp"], errors="coerce").sort_values()
    diffs = timestamps.diff().dt.total_seconds().dropna() / 3600
    diffs = diffs[diffs > 0]
    if diffs.empty:
        return 1.0
    return float(diffs.median())


def _format_hours(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def _render_style() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 2rem; padding-bottom: 2rem;}
        div[data-testid="stMetric"] {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            padding: 14px 16px;
            border-radius: 8px;
            color: #0f172a !important;
        }
        div[data-testid="stMetric"] * {
            color: #0f172a !important;
        }
        div[data-testid="stMetricLabel"] p {
            color: #475569 !important;
        }
        div[data-testid="stMetricValue"] {
            color: #0f172a !important;
            font-weight: 700;
        }
        h1, h2, h3 {letter-spacing: 0;}
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
