"""API 请求 / 响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import config


class TargetIn(BaseModel):
    """一项目标指标：目标值 + 允许偏差 + 约束方向。

    方向对应客户规格单上的符号：
        min  只能高不能低 → [目标, 目标+容差]
        max  只能低不能高 → [目标−容差, 目标]
        both 双向         → [目标−容差, 目标+容差]
    """

    value: float = Field(..., description="目标值")
    tolerance: float | None = Field(default=None, ge=0, description="允许偏差，留空用该指标的默认容差")
    direction: Literal["min", "both", "max"] | None = Field(
        default=None, description="约束方向，留空用该指标的默认方向"
    )


class OrderIn(BaseModel):
    """一张配料订单。

    `targets` 是主入口，形如 {"冻力Bloomg": {"value": 210, "tolerance": 5},
    "PH值": {"value": 5.8}}。为兼容早期只支持冻力的调用方，仍接受顶层的
    `bloom` / `tolerance` 字段，会被合并进 targets。
    """

    name: str = Field(default="", max_length=64, description="单号或备注，留空自动编号")
    weight: float = Field(..., gt=0, le=1_000_000, description="需求量 kg")
    targets: dict[str, TargetIn] = Field(default_factory=dict, description="目标指标")
    material_pref: Literal["balanced", "save_high", "save_low"] = Field(
        default="balanced", description="取料偏好：同样达标时优先动用哪一端的库存"
    )

    # ---- 兼容旧格式 ----
    bloom: float | None = Field(default=None, gt=0, le=1000, description="（旧）目标冻力")
    tolerance: float | None = Field(default=None, ge=0, le=200, description="（旧）冻力容差")

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _merge_and_check(self):
        if self.bloom is not None and config.TARGET_COL not in self.targets:
            self.targets[config.TARGET_COL] = TargetIn(
                value=self.bloom, tolerance=self.tolerance
            )

        if not self.targets:
            raise ValueError("每张订单至少要指定一项目标指标")

        unknown = [k for k in self.targets if k not in config.TARGETABLE_KEYS]
        if unknown:
            raise ValueError(
                f"不支持作为目标的指标：{unknown}。可选：{config.TARGETABLE_KEYS}"
            )

        for key, t in self.targets.items():
            if t.tolerance is None:
                t.tolerance = config.DEFAULT_TOL.get(key)
            if not t.tolerance or t.tolerance <= 0:
                # 容差为 0 会让约束退化成等式，几乎必然无解 —— 给一个极小的正值兜底
                t.tolerance = 1e-6
            if t.direction is None:
                t.direction = config.DEFAULT_DIR.get(key, "both")
        return self


class SolveRequest(BaseModel):
    orders: list[OrderIn] = Field(..., min_length=1, max_length=20)
    settings: dict | None = Field(default=None, description="临时覆盖求解设置，不落盘")


class LimitIn(BaseModel):
    lo: float | None = None
    hi: float | None = None
    enabled: bool = True


class ParamsRequest(BaseModel):
    limits: dict[str, LimitIn] | None = None
    settings: dict | None = None


class PricePoint(BaseModel):
    bloom: float = Field(..., ge=0, le=1000)
    price: float = Field(..., ge=0, le=100_000)


class PricingRequest(BaseModel):
    points: list[PricePoint] = Field(..., min_length=2, max_length=20)
    currency: str | None = Field(default=None, max_length=16)


class CompareRequest(BaseModel):
    orders: list[OrderIn] = Field(..., min_length=1, max_length=10)
    methods: list[str] = Field(..., min_length=1, max_length=6)
    settings: dict | None = None
    with_pareto: bool = True

    @field_validator("methods")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        ok = set(config.OBJECTIVES) | {config.BASELINE_KEY}
        bad = [m for m in v if m not in ok]
        if bad:
            raise ValueError(f"未知方案：{bad}。可选：{sorted(ok)}")
        return list(dict.fromkeys(v))
