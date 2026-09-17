"""冻力定价曲线：断点 + 线性插值。

为什么不用阶梯价：明胶价格随冻力连续变化，阶梯价会让「配到 212 而客户只要 210」
的冗余损失算成 0（同档内无差价），而这正是方案对比最想量化的东西。

⚠️ 默认价格是占位值，不是真实报价。绝对金额必须换成实际价格后才可外发。
"""

from __future__ import annotations

import copy
import json
import logging
import threading

import numpy as np

from . import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_FILE = config.DATA_DIR / "pricing.json"


def load() -> dict:
    """读取定价曲线。文件不存在或损坏时回落默认值。"""
    with _lock:
        if not _FILE.exists():
            return copy.deepcopy(config.DEFAULT_PRICING)
        try:
            raw = json.loads(_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("定价文件损坏，已回落默认值：%s", _FILE, exc_info=True)
            return copy.deepcopy(config.DEFAULT_PRICING)

    pts = raw.get("points") or []
    clean = []
    for p in pts:
        try:
            clean.append({"bloom": float(p["bloom"]), "price": float(p["price"])})
        except (KeyError, TypeError, ValueError):
            continue
    if len(clean) < 2:
        return copy.deepcopy(config.DEFAULT_PRICING)
    clean.sort(key=lambda x: x["bloom"])
    return {"currency": raw.get("currency") or config.DEFAULT_PRICING["currency"],
            "points": clean}


def save(points: list[dict], currency: str | None = None) -> dict:
    """写入定价曲线。至少两个断点，冻力不能重复。"""
    clean = []
    seen = set()
    for p in points or []:
        b, pr = float(p["bloom"]), float(p["price"])
        if b in seen:
            raise ValueError(f"冻力 {b} 重复")
        if pr < 0:
            raise ValueError(f"冻力 {b} 的单价不能为负")
        seen.add(b)
        clean.append({"bloom": b, "price": pr})
    if len(clean) < 2:
        raise ValueError("至少需要两个断点才能插值")
    clean.sort(key=lambda x: x["bloom"])

    cfg = {"currency": currency or load()["currency"], "points": clean}
    with _lock:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("定价曲线已保存：%d 个断点", len(clean))
    return cfg


def reset() -> dict:
    cfg = copy.deepcopy(config.DEFAULT_PRICING)
    with _lock:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def curve(cfg: dict | None = None):
    """返回一个 price(bloom) 函数，支持标量与 ndarray。两端按端点值外推。"""
    cfg = cfg or load()
    xs = np.array([p["bloom"] for p in cfg["points"]], dtype=float)
    ys = np.array([p["price"] for p in cfg["points"]], dtype=float)

    def price(bloom):
        return np.interp(bloom, xs, ys)  # np.interp 两端自动取端点值

    return price


def marginal(bloom: float, cfg: dict | None = None) -> float:
    """某个冻力处的边际单价（元/kg per Bloom），用于把冻力冗余折算成金额。"""
    price = curve(cfg)
    h = 1.0
    return float((price(bloom + h) - price(max(bloom - h, 0))) / (2 * h))
