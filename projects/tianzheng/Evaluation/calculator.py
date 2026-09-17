"""明胶分子量计算模块。

本模块实现：
1. GPC 数均、重均、Z 均分子量及分散系数计算；
2. 基于 Mark-Houwink 方程的黏度法 Mw 估算；
3. 多批胶液复配后的 Mw 计算。
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping, Sequence


def _validate_number(
    value: Any,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
) -> float:
    """校验并转换有限实数，返回 float。"""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} 必须是数值")

    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 必须是有限数值，不能为 NaN 或无穷大")

    if minimum is not None:
        below_minimum = (
            number < minimum if minimum_inclusive else number <= minimum
        )
        if below_minimum:
            operator = "大于等于" if minimum_inclusive else "大于"
            raise ValueError(f"{field_name} 必须{operator} {minimum}")

    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} 必须小于等于 {maximum}")

    return number


def _validate_records(
    records: Sequence[Mapping[str, Any]],
    argument_name: str,
) -> None:
    """校验记录列表是否为非空序列。"""
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise TypeError(f"{argument_name} 必须是由字典组成的列表或序列")
    if not records:
        raise ValueError(f"{argument_name} 不能为空")

    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise TypeError(f"{argument_name} 第 {index} 项必须是字典")


def calculate_gpc_molecular_weight(
    data: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """计算 GPC 分子量指标和小分子峰面积占比。

    每条数据必须包含：
    - N：该组分的分子数量或相对数量；
    - M：该组分的分子量，单位 Da；
    - area：该组分的 GPC 峰面积。

    小分子定义为分子量严格小于 10000 Da。
    """
    _validate_records(data, "data")

    sum_n = 0.0
    sum_n_m = 0.0
    sum_n_m2 = 0.0
    sum_n_m3 = 0.0
    total_area = 0.0
    small_molecule_area = 0.0

    for index, item in enumerate(data, start=1):
        missing_fields = {"N", "M", "area"} - item.keys()
        if missing_fields:
            missing = "、".join(sorted(missing_fields))
            raise KeyError(f"data 第 {index} 项缺少字段：{missing}")

        n = _validate_number(item["N"], f"data[{index - 1}].N", minimum=0)
        molecular_weight = _validate_number(
            item["M"],
            f"data[{index - 1}].M",
            minimum=0,
        )
        area = _validate_number(
            item["area"],
            f"data[{index - 1}].area",
            minimum=0,
        )

        sum_n += n
        sum_n_m += n * molecular_weight
        sum_n_m2 += n * molecular_weight**2
        sum_n_m3 += n * molecular_weight**3
        total_area += area

        if molecular_weight < 10000:
            small_molecule_area += area

    # Mn = Σ(Ni × Mi) / ΣNi
    if sum_n == 0:
        raise ValueError("无法计算 Mn：ΣNi 不能为 0")
    mn = sum_n_m / sum_n

    # Mw = Σ(Ni × Mi²) / Σ(Ni × Mi)
    if sum_n_m == 0:
        raise ValueError("无法计算 Mw：Σ(Ni × Mi) 不能为 0")
    mw = sum_n_m2 / sum_n_m

    # Mz = Σ(Ni × Mi³) / Σ(Ni × Mi²)
    if sum_n_m2 == 0:
        raise ValueError("无法计算 Mz：Σ(Ni × Mi²) 不能为 0")
    mz = sum_n_m3 / sum_n_m2

    # PDI = Mw / Mn
    if mn == 0:
        raise ValueError("无法计算 PDI：Mn 不能为 0")
    pdi = mw / mn

    # 小分子占比 = M < 10000 Da 的峰面积之和 / 总峰面积 × 100%
    if total_area == 0:
        raise ValueError("无法计算小分子占比：所有峰面积之和不能为 0")
    small_molecule_ratio = small_molecule_area / total_area * 100

    return {
        "Mn": mn,
        "Mw": mw,
        "Mz": mz,
        "PDI": pdi,
        "small_molecule_ratio": small_molecule_ratio,
    }


def estimate_mw_by_viscosity(
    intrinsic_viscosity: Real,
    K: Real = 1.7e-5,
    a: Real = 0.86,
) -> float:
    """根据特性黏度估算重均分子量 Mw。

    Mark-Houwink 方程：
        [η] = K × Mw^a
        Mw = ([η] / K) ^ (1 / a)
    """
    viscosity = _validate_number(
        intrinsic_viscosity,
        "intrinsic_viscosity",
        minimum=0,
    )
    coefficient_k = _validate_number(
        K,
        "K",
        minimum=0,
        minimum_inclusive=False,
    )
    exponent_a = _validate_number(
        a,
        "a",
        minimum=0,
        minimum_inclusive=False,
    )

    return (viscosity / coefficient_k) ** (1 / exponent_a)


def calculate_blend_mw(
    batches: Sequence[Mapping[str, Any]],
    use_solid_content: bool = True,
) -> float:
    """计算多批胶液复配后的重均分子量 Mw_mix。

    当 use_solid_content=True：
        Wi_eff = Wi × Si
        Mw_mix = Σ(Wi_eff × Mwi) / ΣWi_eff

    当 use_solid_content=False：
        Mw_mix = Σ(Wi × Mwi) / ΣWi
    """
    _validate_records(batches, "batches")
    if not isinstance(use_solid_content, bool):
        raise TypeError("use_solid_content 必须是布尔值")

    total_effective_weight = 0.0
    weighted_mw_sum = 0.0

    for index, batch in enumerate(batches, start=1):
        required_fields = {"weight", "Mw"}
        if use_solid_content:
            required_fields.add("solid_content")

        missing_fields = required_fields - batch.keys()
        if missing_fields:
            missing = "、".join(sorted(missing_fields))
            raise KeyError(f"batches 第 {index} 项缺少字段：{missing}")

        batch_name = batch.get("name", f"批次{index}")
        weight = _validate_number(
            batch["weight"],
            f"{batch_name}.weight",
            minimum=0,
        )
        molecular_weight = _validate_number(
            batch["Mw"],
            f"{batch_name}.Mw",
            minimum=0,
        )

        if use_solid_content:
            solid_content = _validate_number(
                batch["solid_content"],
                f"{batch_name}.solid_content",
                minimum=0,
                maximum=1,
            )
            # 有效干基重量：Wi_eff = Wi × Si
            effective_weight = weight * solid_content
        else:
            effective_weight = weight

        total_effective_weight += effective_weight
        weighted_mw_sum += effective_weight * molecular_weight

    if total_effective_weight == 0:
        denominator_name = "有效干基重量之和" if use_solid_content else "重量之和"
        raise ValueError(f"无法计算复配 Mw：{denominator_name}不能为 0")

    # Mw_mix = Σ(有效重量 × Mwi) / Σ有效重量
    return weighted_mw_sum / total_effective_weight


if __name__ == "__main__":
    # 测试数据：高分子胶 500 kg、低分子胶 1500 kg。
    test_batches = [
        {
            "name": "高分子胶",
            "weight": 500,
            "solid_content": 1.0,
            "Mw": 130000,
        },
        {
            "name": "低分子胶",
            "weight": 1500,
            "solid_content": 1.0,
            "Mw": 40000,
        },
    ]

    blend_mw = calculate_blend_mw(test_batches)
    print(f"复配后 Mw_mix = {blend_mw:.2f}")
    print(f"测试是否通过：{math.isclose(blend_mw, 62500.0)}")
