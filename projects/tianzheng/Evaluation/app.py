"""明胶分子量复配计算与 AI 辅助决策 Demo。

启动方式：
    streamlit run app.py
"""

from __future__ import annotations

import html
import math
from typing import Any

import pandas as pd
import streamlit as st

from calculator import (
    calculate_blend_mw,
    calculate_gpc_molecular_weight,
    estimate_mw_by_viscosity,
)


PAGE_TITLE = "明胶分子量复配计算与AI辅助决策Demo"
DEFAULT_REFERENCE_MW = 80000.0

GPC_COLUMNS = ["分子数量 Ni", "分子量 Mi (Da)", "峰面积 area"]
BLEND_COLUMNS = ["批次名称", "胶液类型", "重量 kg", "固含量 %", "当前 Mw"]


def initialize_session_state() -> None:
    """初始化页面所需的会话数据。"""
    if "gpc_result" not in st.session_state:
        st.session_state.gpc_result = None
    if "gpc_error" not in st.session_state:
        st.session_state.gpc_error = None


def is_missing(value: Any) -> bool:
    """判断表格值是否为空。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def format_number(value: float | None, decimals: int = 2) -> str:
    """将计算结果格式化为适合客户演示的文本。"""
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:,.{decimals}f}"


def render_question_cards(questions: list[str]) -> None:
    """在右侧显示规则引擎生成的问题和建议。"""
    if not questions:
        st.success("当前输入信息较完整，可继续调整参数并观察结果。")
        return

    for index, question in enumerate(questions, start=1):
        st.info(f"**{index}.** {question}")


def render_ai_inference_card(
    arrow: str,
    status: str,
    summary: str,
    suggestion: str,
) -> None:
    """在计算指标旁显示带绿色方向箭头的规则式 AI 推断卡。"""
    st.markdown(
        f"""
        <div class="ai-inference-card">
            <div class="ai-inference-heading">
                <span class="ai-direction-arrow">{html.escape(arrow)}</span>
                <span>
                    <span class="ai-inference-kicker">AI 推断</span>
                    <span class="ai-inference-status">{html.escape(status)}</span>
                </span>
            </div>
            <div class="ai-inference-summary">{html.escape(summary)}</div>
            <div class="ai-inference-suggestion">
                <strong>建议：</strong>{html.escape(suggestion)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def compare_with_target(
    current_mw: float,
    target_mw: float | None,
) -> tuple[str, str, str]:
    """将当前 Mw 与参考目标比较，返回箭头、状态和说明。"""
    if target_mw is None or target_mw <= 0:
        return (
            "→",
            "尚无目标基准",
            "当前未设置参考目标 Mw，暂时无法判断结果偏高或偏低。",
        )

    deviation = current_mw - target_mw
    deviation_rate = deviation / target_mw * 100
    if math.isclose(deviation, 0.0, abs_tol=1e-9):
        return "→", "与目标一致", "当前 Mw 与参考目标一致。"
    if deviation > 0:
        return (
            "↗",
            "相对目标偏高",
            f"当前 Mw 比参考目标高 {deviation:,.2f}，偏差率为 {deviation_rate:.2f}%。",
        )
    return (
        "↘",
        "相对目标偏低",
        f"当前 Mw 比参考目标低 {abs(deviation):,.2f}，偏差率为 {deviation_rate:.2f}%。",
    )


def get_common_questions() -> list[str]:
    """返回各模式通用的工艺确认问题。"""
    return [
        "目标成品是药用明胶、食品明胶还是工业明胶？",
        "是否需要同时考虑冻力、黏度、pH、水分等质量指标？",
    ]


def render_gpc_input() -> tuple[dict[str, float] | None, list[str], float | None]:
    """渲染 GPC 输入区，并在点击按钮后计算结果。"""
    st.subheader("GPC 数据输入")
    reference_mw = st.number_input(
        "参考目标 Mw",
        min_value=0.0,
        value=DEFAULT_REFERENCE_MW,
        step=1000.0,
        format="%.2f",
        help="默认按 80,000 判断，可根据产品标准手动修改。",
        key="gpc_reference_mw",
    )
    st.caption("可直接编辑表格，并通过最后一行继续添加组分。")

    default_data = pd.DataFrame(
        [
            {"分子数量 Ni": 100.0, "分子量 Mi (Da)": 10000.0, "峰面积 area": 30.0},
            {"分子数量 Ni": 10.0, "分子量 Mi (Da)": 100000.0, "峰面积 area": 70.0},
        ],
        columns=GPC_COLUMNS,
    )

    edited_data = st.data_editor(
        default_data,
        key="gpc_data_editor",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "分子数量 Ni": st.column_config.NumberColumn(
                "分子数量 Ni",
                min_value=0.0,
                format="%.4f",
                required=True,
            ),
            "分子量 Mi (Da)": st.column_config.NumberColumn(
                "分子量 Mi (Da)",
                min_value=0.0,
                format="%.2f",
                required=True,
            ),
            "峰面积 area": st.column_config.NumberColumn(
                "峰面积 area",
                min_value=0.0,
                format="%.4f",
                required=True,
            ),
        },
    )

    input_signature = edited_data.to_json(orient="split")
    if st.session_state.get("gpc_input_signature") not in (None, input_signature):
        st.session_state.gpc_result = None
        st.info("表格数据已更改，请点击“计算 GPC 分子量”更新结果。")
    if st.button("计算 GPC 分子量", type="primary", width="stretch"):
        try:
            valid_rows = edited_data.dropna(how="all")
            if valid_rows.empty:
                raise ValueError("请至少输入一条 GPC 数据")
            if valid_rows[GPC_COLUMNS].isna().any().any():
                raise ValueError("GPC 数据存在未填写的单元格，请补充完整")

            calculation_data = [
                {
                    "N": row["分子数量 Ni"],
                    "M": row["分子量 Mi (Da)"],
                    "area": row["峰面积 area"],
                }
                for _, row in valid_rows.iterrows()
            ]
            st.session_state.gpc_result = calculate_gpc_molecular_weight(
                calculation_data
            )
            st.session_state.gpc_error = None
            st.session_state.gpc_input_signature = input_signature
        except (TypeError, ValueError, KeyError) as exc:
            st.session_state.gpc_result = None
            st.session_state.gpc_error = str(exc)

    if st.session_state.gpc_error:
        st.error(st.session_state.gpc_error)

    questions = get_common_questions()
    valid_rows = edited_data.dropna(how="all")
    if valid_rows.empty:
        questions.insert(0, "当前没有 GPC 数据，是否需要使用黏度法进行中控估算？")
    elif valid_rows[GPC_COLUMNS].isna().any().any():
        questions.insert(0, "GPC 数据尚未填写完整，请确认 Ni、Mi 和峰面积。")
    if reference_mw <= 0:
        questions.insert(0, "是否需要输入参考目标 Mw，以判断检测结果偏高或偏低？")

    return (
        st.session_state.gpc_result,
        questions,
        reference_mw if reference_mw > 0 else None,
    )


def render_viscosity_input() -> tuple[float | None, list[str], float | None]:
    """渲染黏度法参数，并实时估算 Mw。"""
    st.subheader("黏度法参数")
    intrinsic_viscosity = st.number_input(
        "特性黏数 [η]",
        min_value=0.0,
        value=1.0,
        step=0.01,
        format="%.6f",
        help="请使用与 K 参数相匹配的特性黏数单位。",
    )
    coefficient_k = st.number_input(
        "Mark-Houwink 常数 K",
        min_value=0.000000001,
        value=1.7e-5,
        step=1e-6,
        format="%.8f",
    )
    exponent_a = st.number_input(
        "Mark-Houwink 指数 a",
        min_value=0.000001,
        value=0.86,
        step=0.01,
        format="%.4f",
    )
    reference_mw = st.number_input(
        "参考目标 Mw",
        min_value=0.0,
        value=DEFAULT_REFERENCE_MW,
        step=1000.0,
        format="%.2f",
        help="默认按 80,000 判断，可根据产品标准手动修改。",
        key="viscosity_reference_mw",
    )

    estimated_mw: float | None = None
    try:
        estimated_mw = estimate_mw_by_viscosity(
            intrinsic_viscosity,
            K=coefficient_k,
            a=exponent_a,
        )
    except (TypeError, ValueError) as exc:
        st.error(str(exc))

    questions = get_common_questions()
    questions.insert(0, "当前是否有 GPC 检测数据，可用于校验黏度法估算结果？")
    questions.insert(1, "K 和 a 是否来自当前明胶品类及检测温度下的标定数据？")
    if reference_mw <= 0:
        questions.insert(0, "是否需要输入参考目标 Mw，以判断估算结果偏高或偏低？")

    return estimated_mw, questions, reference_mw if reference_mw > 0 else None


def render_blend_input() -> tuple[dict[str, Any], list[str], pd.DataFrame]:
    """渲染复配参数，并根据表格调整实时计算 Mw_mix。"""
    st.subheader("复配参数")

    target_mw = st.number_input(
        "目标 Mw",
        min_value=0.0,
        value=80000.0,
        step=1000.0,
        format="%.2f",
        help="输入 0 表示尚未设置目标 Mw。",
    )
    tolerance_rate = st.number_input(
        "允许偏差范围（±%）",
        min_value=0.0,
        max_value=100.0,
        value=5.0,
        step=0.5,
        format="%.2f",
    )
    use_solid_content = st.checkbox(
        "按固含量折算有效干基重量",
        value=True,
        help="勾选后使用 Wi_eff = Wi × Si 计算。",
    )

    st.caption("修改重量或固含量后，复配结果会自动刷新。")
    default_batches = pd.DataFrame(
        [
            {
                "批次名称": "高分子胶",
                "胶液类型": "高分子",
                "重量 kg": 500.0,
                "固含量 %": 100.0,
                "当前 Mw": 130000.0,
            },
            {
                "批次名称": "低分子胶",
                "胶液类型": "低分子",
                "重量 kg": 1500.0,
                "固含量 %": 100.0,
                "当前 Mw": 40000.0,
            },
        ],
        columns=BLEND_COLUMNS,
    )

    edited_batches = st.data_editor(
        default_batches,
        key="blend_data_editor",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "批次名称": st.column_config.TextColumn(
                "批次名称",
                required=True,
            ),
            "胶液类型": st.column_config.SelectboxColumn(
                "胶液类型",
                options=["高分子", "中分子", "低分子"],
                required=True,
            ),
            "重量 kg": st.column_config.NumberColumn(
                "重量 kg",
                min_value=0.0,
                format="%.2f",
                required=True,
            ),
            "固含量 %": st.column_config.NumberColumn(
                "固含量 %",
                min_value=0.0,
                max_value=100.0,
                format="%.2f",
                required=use_solid_content,
            ),
            "当前 Mw": st.column_config.NumberColumn(
                "当前 Mw",
                min_value=0.0,
                format="%.2f",
                required=True,
            ),
        },
    )

    result: dict[str, Any] = {
        "target_mw": target_mw if target_mw > 0 else None,
        "current_mw": None,
        "deviation": None,
        "deviation_rate": None,
        "status": "待计算",
        "tolerance_rate": tolerance_rate,
        "use_solid_content": use_solid_content,
    }
    questions = get_common_questions()
    valid_rows = edited_batches.dropna(how="all").copy()

    if target_mw <= 0:
        questions.insert(0, "请先输入目标 Mw。")

    if valid_rows.empty:
        st.warning("请至少输入一个复配批次。")
        questions.insert(0, "请录入当前可用于复配的胶液批次。")
        return result, questions, valid_rows

    required_columns = ["批次名称", "胶液类型", "重量 kg", "当前 Mw"]
    if use_solid_content:
        required_columns.append("固含量 %")

    if valid_rows[required_columns].isna().any().any():
        st.warning("批次信息尚未填写完整，补充后将自动计算。")
        if use_solid_content and valid_rows["固含量 %"].isna().any():
            questions.insert(0, "请确认各批胶液固含量是否一致，并补充缺失数据。")
        return result, questions, valid_rows

    try:
        batches = []
        for _, row in valid_rows.iterrows():
            batch = {
                "name": str(row["批次名称"]).strip(),
                "weight": row["重量 kg"],
                "Mw": row["当前 Mw"],
            }
            if use_solid_content:
                batch["solid_content"] = row["固含量 %"] / 100
            batches.append(batch)

        current_mw = calculate_blend_mw(
            batches,
            use_solid_content=use_solid_content,
        )
        result["current_mw"] = current_mw

        if target_mw > 0:
            deviation = current_mw - target_mw
            deviation_rate = deviation / target_mw * 100
            result["deviation"] = deviation
            result["deviation_rate"] = deviation_rate

            if abs(deviation_rate) <= tolerance_rate:
                result["status"] = "合格"
                questions.insert(0, "当前 Mw 已在允许偏差范围内，是否需要进一步核对其他质量指标？")
            elif deviation > 0:
                result["status"] = "偏高"
                questions.insert(
                    0,
                    "当前复配结果偏高，建议增加低分子胶比例或降低高分子胶比例。",
                )
            else:
                result["status"] = "偏低"
                questions.insert(0, "当前复配结果偏低，建议增加高分子胶比例。")

        if use_solid_content:
            solid_contents = valid_rows["固含量 %"].dropna()
            if solid_contents.nunique() > 1:
                questions.append("各批胶液固含量不同，当前结果已按有效干基重量修正。")
            else:
                questions.append("当前各批固含量一致，是否需要确认检测时间和取样代表性？")
        else:
            questions.append("当前未启用固含量修正，请确认各批胶液固含量是否一致。")

    except (TypeError, ValueError, KeyError) as exc:
        st.error(str(exc))

    return result, questions, valid_rows


def render_formula_panel(
    calculation_mode: str,
    *,
    use_solid_content: bool = True,
) -> None:
    """使用标准数学形式展示当前模式的计算公式。"""
    with st.container(border=True):
        st.markdown("#### 标准计算公式")

        if calculation_mode == "GPC分子量计算":
            st.latex(
                r"""
                M_n=\frac{\sum_i N_iM_i}{\sum_i N_i}
                """
            )
            st.latex(
                r"""
                M_w=\frac{\sum_i N_iM_i^2}{\sum_i N_iM_i}
                """
            )
            st.latex(
                r"""
                M_z=\frac{\sum_i N_iM_i^3}{\sum_i N_iM_i^2}
                """
            )
            st.latex(
                r"""
                PDI=\frac{M_w}{M_n}
                """
            )
            st.latex(
                r"""
                R_{\mathrm{small}}
                =
                \frac{\sum_{M_i<10000} A_i}{\sum_i A_i}
                \times 100\%
                """
            )
            st.caption(
                "Ni：第 i 个组分的分子数量；Mi：分子量；Ai：GPC 峰面积；"
                "Rsmall：小于 10000 Da 的峰面积占比。"
            )

        elif calculation_mode == "黏度法估算Mw":
            st.latex(
                r"""
                [\eta]=K M_w^a
                """
            )
            st.latex(
                r"""
                M_w=\left(\frac{[\eta]}{K}\right)^{\frac{1}{a}}
                """
            )
            st.caption(
                "[η]：特性黏数；K、a：Mark–Houwink 参数；Mw：估算重均分子量。"
            )

        elif use_solid_content:
            st.latex(
                r"""
                W_{i,\mathrm{eff}}=W_iS_i
                """
            )
            st.latex(
                r"""
                M_{w,\mathrm{mix}}
                =
                \frac{\sum_i W_{i,\mathrm{eff}}M_{w,i}}
                {\sum_i W_{i,\mathrm{eff}}}
                """
            )
            st.latex(
                r"""
                M_{w,\mathrm{mix}}
                =
                \frac{\sum_i W_iS_iM_{w,i}}
                {\sum_i W_iS_i}
                """
            )
            st.caption(
                "Wi：第 i 批胶液重量；Si：固含量（30% 按 0.30 计算）；"
                "Wi,eff：有效干基重量；Mw,i：该批次重均分子量。"
            )

        else:
            st.latex(
                r"""
                M_{w,\mathrm{mix}}
                =
                \frac{\sum_i W_iM_{w,i}}{\sum_i W_i}
                """
            )
            st.caption(
                "Wi：第 i 批胶液重量；Mw,i：该批次重均分子量。"
                "当前公式不进行固含量修正。"
            )

        if calculation_mode == "复配Mw计算":
            st.latex(
                r"""
                \Delta M_w=M_{w,\mathrm{mix}}-M_{w,\mathrm{target}}
                """
            )
            st.latex(
                r"""
                \Delta R=
                \frac{\Delta M_w}{M_{w,\mathrm{target}}}\times100\%
                """
            )
            st.caption("ΔMw：绝对偏差；ΔR：相对偏差率。")


def render_gpc_results(
    result: dict[str, float] | None,
    reference_mw: float | None,
) -> None:
    """在中列显示 GPC 计算结果。"""
    st.subheader("计算结果")
    render_formula_panel("GPC分子量计算")
    if result is None:
        st.info("请在左侧录入数据并点击“计算 GPC 分子量”。")
        return

    first_row = st.columns(2)
    first_row[0].metric("数均分子量 Mn", format_number(result["Mn"]))
    first_row[1].metric("重均分子量 Mw", format_number(result["Mw"]))

    second_row = st.columns(2)
    second_row[0].metric("Z 均分子量 Mz", format_number(result["Mz"]))
    second_row[1].metric("分散系数 PDI", format_number(result["PDI"], 4))

    st.metric(
        "小分子占比（M < 10000 Da）",
        f"{result['small_molecule_ratio']:.2f}%",
    )

    arrow, status, summary = compare_with_target(result["Mw"], reference_mw)
    if reference_mw is None:
        suggestion = (
            f"当前 PDI 为 {result['PDI']:.4f}、小分子占比为 "
            f"{result['small_molecule_ratio']:.2f}%。建议补充产品目标或历史合格批次，"
            "再判断分布宽度和小分子比例是否符合工艺要求。"
        )
    elif result["Mw"] > reference_mw:
        suggestion = (
            "可检查高分子组分占比是否过高；如需复配调整，可适度增加低分子胶比例。"
        )
    elif result["Mw"] < reference_mw:
        suggestion = "可检查降解程度及低分子峰面积，并评估增加高分子胶比例。"
    else:
        suggestion = "Mw 已达到参考目标，建议继续核对 PDI 和小分子占比。"
    render_ai_inference_card(arrow, status, summary, suggestion)
    st.caption("AI 推断为规则式辅助提示，最终判定应以企业质量标准为准。")


def render_viscosity_results(
    estimated_mw: float | None,
    reference_mw: float | None,
) -> None:
    """在中列显示黏度法估算结果。"""
    st.subheader("估算结果")
    render_formula_panel("黏度法估算Mw")
    if estimated_mw is None:
        st.info("请在左侧输入有效参数。")
        return

    st.metric("黏度法估算 Mw", format_number(estimated_mw))

    arrow, status, summary = compare_with_target(estimated_mw, reference_mw)
    if reference_mw is None:
        suggestion = "输入参考目标 Mw 后可判断方向；当前结果建议结合 GPC 检测进行校验。"
    elif estimated_mw > reference_mw:
        suggestion = "估算结果偏高，可复核特性黏数、K、a，并评估降低高分子胶比例。"
    elif estimated_mw < reference_mw:
        suggestion = "估算结果偏低，可复核原液降解情况，并评估增加高分子胶比例。"
    else:
        suggestion = "估算值达到参考目标，建议用 GPC 数据确认实际分子量分布。"
    render_ai_inference_card(arrow, status, summary, suggestion)
    st.caption("黏度法属于工艺估算，AI 推断不替代 GPC 检测与质量判定。")


def calculate_addition_suggestion(
    result: dict[str, Any],
    batch_data: pd.DataFrame,
) -> str | None:
    """估算仅追加一个现有批次时，接近目标所需的胶液重量。"""
    current_mw = result["current_mw"]
    target_mw = result["target_mw"]
    if (
        current_mw is None
        or target_mw is None
        or math.isclose(current_mw, target_mw, abs_tol=1e-9)
        or batch_data.empty
    ):
        return None

    rows = batch_data.dropna(
        subset=["批次名称", "重量 kg", "当前 Mw"]
    ).copy()
    if rows.empty:
        return None

    if result["use_solid_content"]:
        rows = rows.dropna(subset=["固含量 %"])
        rows = rows[rows["固含量 %"] > 0]
        if rows.empty:
            return None
        rows["有效重量"] = rows["重量 kg"] * rows["固含量 %"] / 100
    else:
        rows["有效重量"] = rows["重量 kg"]

    total_effective_weight = float(rows["有效重量"].sum())
    if total_effective_weight <= 0:
        return None

    if current_mw > target_mw:
        candidates = rows[rows["当前 Mw"] < target_mw]
        if candidates.empty:
            return None
        candidate = candidates.loc[candidates["当前 Mw"].idxmin()]
    else:
        candidates = rows[rows["当前 Mw"] > target_mw]
        if candidates.empty:
            return None
        candidate = candidates.loc[candidates["当前 Mw"].idxmax()]

    candidate_mw = float(candidate["当前 Mw"])
    denominator = candidate_mw - target_mw
    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        return None

    # 由 (当前Mw × 当前干基重量 + 新增Mw × x) / (当前干基重量 + x)
    # = 目标Mw，反推需要追加的有效干基重量 x。
    required_effective_weight = (
        total_effective_weight * (target_mw - current_mw) / denominator
    )
    if required_effective_weight <= 0:
        return None

    if result["use_solid_content"]:
        solid_fraction = float(candidate["固含量 %"]) / 100
        required_solution_weight = required_effective_weight / solid_fraction
        weight_text = (
            f"{required_solution_weight:,.1f} kg 胶液"
            f"（约 {required_effective_weight:,.1f} kg 干基）"
        )
    else:
        weight_text = f"{required_effective_weight:,.1f} kg"

    batch_name = str(candidate["批次名称"]).strip()
    return (
        f"若其他批次保持不变，仅追加“{batch_name}”，理论上约需增加 {weight_text}"
        "可接近目标 Mw。"
    )


def render_blend_results(
    result: dict[str, Any],
    batch_data: pd.DataFrame,
) -> None:
    """在中列显示复配计算结果和状态。"""
    st.subheader("复配结果")
    render_formula_panel(
        "复配Mw计算",
        use_solid_content=result["use_solid_content"],
    )

    target_mw = result["target_mw"]
    current_mw = result["current_mw"]
    deviation = result["deviation"]
    deviation_rate = result["deviation_rate"]
    status = result["status"]

    first_row = st.columns(2)
    first_row[0].metric("目标 Mw", format_number(target_mw))
    first_row[1].metric("当前 Mw_mix", format_number(current_mw))

    second_row = st.columns(2)
    second_row[0].metric(
        "偏差",
        format_number(deviation),
        delta=format_number(deviation) if deviation is not None else None,
        delta_color="inverse",
    )
    second_row[1].metric(
        "偏差率",
        f"{deviation_rate:.2f}%" if deviation_rate is not None else "—",
        delta=f"{deviation_rate:.2f}%" if deviation_rate is not None else None,
        delta_color="inverse",
    )

    if status == "合格":
        st.success(
            f"判定：合格（允许偏差 ±{result['tolerance_rate']:.2f}%）"
        )
    elif status == "偏高":
        st.error("判定：当前 Mw 偏高")
    elif status == "偏低":
        st.warning("判定：当前 Mw 偏低")
    else:
        st.info("判定：请完善参数后计算")

    if current_mw is not None:
        arrow, inference_status, summary = compare_with_target(
            current_mw,
            target_mw,
        )
        addition_suggestion = calculate_addition_suggestion(result, batch_data)
        if status == "合格":
            suggestion = (
                "当前结果已进入允许范围，建议锁定配方，并复核冻力、黏度、pH 和水分。"
            )
        elif status == "偏高":
            suggestion = (
                addition_suggestion
                or "建议增加 Mw 低于目标值的低分子胶比例，或降低高分子胶比例。"
            )
        elif status == "偏低":
            suggestion = (
                addition_suggestion
                or "建议增加 Mw 高于目标值的高分子胶比例。"
            )
        else:
            suggestion = "请先输入目标 Mw，系统才能生成调配方向建议。"
        render_ai_inference_card(arrow, inference_status, summary, suggestion)
        st.caption("建议量为单变量理论估算，实际投料前需结合库存、固含量和工艺约束复核。")

def main() -> None:
    """Streamlit 页面入口。"""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="🧪",
        layout="wide",
    )
    initialize_session_state()

    st.markdown(
        """
        <style>
        /* Embedded workspaces need room for complete numerical results. */
        @media (max-width: 1100px) {
            [data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
            }
            [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
                flex: 1 1 340px !important;
                width: auto !important;
                min-width: min(100%, 340px) !important;
            }
            [data-testid="stMetricValue"] {
                font-size: clamp(1.4rem, 3.5vw, 2rem);
            }
        }
        .ai-inference-card {
            margin-top: 0.9rem;
            margin-bottom: 0.6rem;
            padding: 1rem 1.05rem;
            border: 1px solid rgba(22, 163, 74, 0.28);
            border-left: 5px solid #16a34a;
            border-radius: 0.8rem;
            background: rgba(22, 163, 74, 0.06);
        }
        .ai-inference-heading {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 0.7rem;
        }
        .ai-direction-arrow {
            color: #16a34a;
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
        }
        .ai-inference-kicker {
            display: block;
            color: #16a34a;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.08em;
        }
        .ai-inference-status {
            display: block;
            font-size: 1.05rem;
            font-weight: 700;
        }
        .ai-inference-summary {
            margin-bottom: 0.55rem;
            line-height: 1.55;
        }
        .ai-inference-suggestion {
            padding-top: 0.55rem;
            border-top: 1px solid rgba(22, 163, 74, 0.2);
            line-height: 1.55;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title(PAGE_TITLE)
    st.caption("公式计算 · 参数调整 · 实时反馈 · 规则式工艺提问")
    st.divider()

    left_column, middle_column, right_column = st.columns(
        [1.25, 1.0, 0.9],
        gap="large",
    )

    with left_column:
        st.header("参数输入")
        calculation_mode = st.selectbox(
            "选择计算模式",
            ["GPC分子量计算", "黏度法估算Mw", "复配Mw计算"],
        )

        if calculation_mode == "GPC分子量计算":
            calculation_result, questions, reference_mw = render_gpc_input()
        elif calculation_mode == "黏度法估算Mw":
            calculation_result, questions, reference_mw = render_viscosity_input()
        else:
            calculation_result, questions, batch_data = render_blend_input()

    with middle_column:
        st.header("结果展示")
        if calculation_mode == "GPC分子量计算":
            render_gpc_results(calculation_result, reference_mw)
        elif calculation_mode == "黏度法估算Mw":
            render_viscosity_results(calculation_result, reference_mw)
        else:
            render_blend_results(calculation_result, batch_data)

    with right_column:
        st.header("AI 提问助手")
        st.caption("当前为规则引擎模式，暂未接入大模型。")
        render_question_cards(questions)

        with st.expander("后续可扩展方向"):
            st.write(
                "可结合浸灰时间、提胶温度、pH、黏度、冻力、固含量、"
                "历史批次 Mw 和成品质量判定，训练工艺预测模型。"
            )


if __name__ == "__main__":
    main()
