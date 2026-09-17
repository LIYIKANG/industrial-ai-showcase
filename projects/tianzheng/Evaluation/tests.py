"""calculator.py 的基础公式测试。

运行方式：
    python3 tests.py
"""

import math

from calculator import calculate_blend_mw, calculate_gpc_molecular_weight


def test_gpc_calculation() -> None:
    """案例1：验证 GPC 的 Mn、Mw 和 PDI。"""
    data = [
        {"N": 100, "M": 10000, "area": 30},
        {"N": 10, "M": 100000, "area": 70},
    ]

    result = calculate_gpc_molecular_weight(data)

    assert math.isclose(result["Mn"], 18181.81818, rel_tol=1e-6)
    assert math.isclose(result["Mw"], 55000.0, rel_tol=1e-9)
    assert math.isclose(result["PDI"], 3.025, rel_tol=1e-9)


def test_blend_calculation() -> None:
    """案例2：验证按胶液重量计算复配 Mw。"""
    batches = [
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

    result = calculate_blend_mw(batches, use_solid_content=False)

    assert math.isclose(result, 62500.0, rel_tol=1e-9)


def test_blend_with_solid_content() -> None:
    """案例3：验证使用 Wi_eff = Wi × Si 进行固含量修正。"""
    batches = [
        {
            "name": "批次A",
            "weight": 1000,
            "solid_content": 0.30,
            "Mw": 100000,
        },
        {
            "name": "批次B",
            "weight": 1000,
            "solid_content": 0.20,
            "Mw": 50000,
        },
    ]

    # 批次A有效干基重量：1000 × 0.30 = 300 kg
    # 批次B有效干基重量：1000 × 0.20 = 200 kg
    # Mw_mix = (300 × 100000 + 200 × 50000) / (300 + 200)
    expected_mw = 80000.0
    result = calculate_blend_mw(batches, use_solid_content=True)

    assert math.isclose(result, expected_mw, rel_tol=1e-9)


if __name__ == "__main__":
    test_gpc_calculation()
    print("案例1通过：GPC 计算结果正确")

    test_blend_calculation()
    print("案例2通过：基础复配 Mw = 62500")

    test_blend_with_solid_content()
    print("案例3通过：固含量修正后 Mw = 80000")

    print("全部测试通过！")
