"""方案对比：同一客户需求下，跑多个求解方案并统一打分。

对比的公平前提是**约束完全一致** —— 所有方案共用同一套加权平均约束、同一份
客户规格、同一个候选池，只有目标函数不同。加权平均本身不是可选项，它是物理事实。

四项评估口径（见 docs/08）：
    成本       Σ 取用量 × 单价(该批冻力)，单价来自可编辑的定价曲线
    指标冗余   交付指标超出客户要求的部分；冻力那项折算成金额
    库存消化   动用了多少「难用料」（两端料）—— 无库龄数据时的替代口径
    计算时间   实测墙钟时间
"""

from __future__ import annotations

import logging
import time

import numpy as np

from . import baseline, config, pricing, solver

log = logging.getLogger(__name__)

# 帕累托前沿：批次数从最优值起逐档放宽，每档求最低成本。
# 用 ε-约束法而不是加权扫描 —— 前者每个点的含义直白（"最多用 N 批时的最低成本"），
# 后者要解释权重系数，客户看不懂。
PARETO_STEPS = 6


def _bloom_target(orders: list[dict]) -> tuple[float, float] | None:
    """取第一张单的冻力要求，用于冗余折价。多单时按加权平均。"""
    tot = num = 0.0
    for o in orders:
        t = o["targets"].get(config.TARGET_COL)
        if t:
            tot += t[0] * o["weight"]
            num += o["weight"]
    return (tot / num, num) if num else None


def score(result: dict, orders: list[dict], price_fn, digest_lo: float, digest_hi: float) -> dict:
    """把一个求解结果打成可横向比较的分数。"""
    picks = [p for o in result["订单"] for p in o["用料"]]
    qty = np.array([p["取用kg"] for p in picks]) if picks else np.zeros(0)
    bloom = np.array([p.get(config.TARGET_COL, np.nan) for p in picks]) if picks else np.zeros(0)

    cost = float((qty * price_fn(bloom)).sum()) if picks else 0.0
    total_kg = float(qty.sum())

    # 难用料消化：两端料单独很难达标，必须靠混配出货
    hard = (bloom <= digest_lo) | (bloom >= digest_hi) if picks else np.zeros(0, dtype=bool)
    digest_kg = float(qty[hard].sum()) if picks else 0.0

    # 指标冗余：逐单逐指标算「超出要求的部分」，用占容差归一
    redundancy = []
    over_total = 0.0
    for o in result["订单"]:
        for a in o["达成"]:
            if a["类型"] != "target":
                continue
            direction, val, tol = a["方向"], a["目标"], a["容差"] or 1.0
            act = a["实际"]
            if direction == "min":
                over = max(0.0, act - val)
            elif direction == "max":
                over = max(0.0, val - act)
            else:
                over = abs(act - val)
            over_total += over / tol
            redundancy.append({
                "单号": o["单号"], "参数": a["参数"], "方向": direction,
                "要求": val, "实际": act,
                "冗余": round(over, 4), "占容差%": round(over / tol * 100, 1),
            })

    # 冻力冗余折价：客户只要 210 而交付 213，那 3 个 Bloom 是白送的
    give_away = 0.0
    bt = _bloom_target(orders)
    if bt and picks:
        req, wsum = bt
        act = float((qty * bloom).sum() / total_kg) if total_kg else req
        if act > req:
            give_away = float((price_fn(act) - price_fn(req)) * wsum)

    return {
        "料本": round(cost, 2),
        "单位成本": round(cost / total_kg, 3) if total_kg else 0.0,
        "冻力冗余折价": round(give_away, 2),
        "冗余合计占容差": round(over_total, 3),
        "冗余明细": redundancy,
        "批次数": result["总批次数"],
        "消化难用料kg": round(digest_kg, 1),
        "消化占比%": round(digest_kg / total_kg * 100, 1) if total_kg else 0.0,
        "配料量kg": round(total_kg, 2),
        "耗时秒": result["耗时秒"],
        "全部达标": result["全部达标"],
    }


def inventory_impact(result: dict, pool, high_from: float, low_to: float) -> dict:
    """配料前后，低/中/高三段库存的吨位变化。回答「啃掉了哪一段」。"""
    b = pool[config.TARGET_COL].to_numpy().astype(float)
    w = pool[config.WEIGHT_COL].to_numpy().astype(float)
    ids = pool[config.ID_COL].to_numpy()

    taken = {}
    for o in result["订单"]:
        for p in o["用料"]:
            taken[p["数据编号"]] = taken.get(p["数据编号"], 0.0) + p["取用kg"]
    used = np.array([taken.get(int(i), 0.0) for i in ids])

    segs = {"低冻力料": b <= low_to,
            "中间料": (b > low_to) & (b < high_from),
            "高冻力料": b >= high_from}
    out = {}
    for name, m in segs.items():
        before = float(w[m].sum()) / 1000
        consumed = float(used[m].sum()) / 1000
        out[name] = {
            "配料前吨": round(before, 2),
            "消耗吨": round(consumed, 3),
            "配料后吨": round(before - consumed, 2),
            "消耗占比%": round(consumed / before * 100, 2) if before else 0.0,
        }
    return out


def run(
    orders: list[dict],
    methods: list[str],
    limits: dict | None = None,
    settings: dict | None = None,
    with_pareto: bool = True,
) -> dict:
    """跑一组方案并统一打分。单个方案失败不影响其余 —— 贪心配不出来本身就是结论。"""
    from . import store

    cfg = store.load()
    limits = limits if limits is not None else cfg["limits"]
    st = {**cfg["settings"], **(settings or {})}

    price_cfg = pricing.load()
    price_fn = pricing.curve(price_cfg)

    tgt_keys = solver._target_keys(orders)
    pool = solver.build_pool(limits, st.get("worst_grade"), tgt_keys)
    bs = pool[config.TARGET_COL].drop_nulls()
    digest_lo = float(bs.quantile(config.HARD_LOW_Q))
    digest_hi = float(bs.quantile(config.HARD_HIGH_Q))
    high_from = float(st.get("high_bloom_from") or 275.0)
    low_to = float(st.get("low_bloom_to") or 120.0)

    results = []
    for m in methods:
        label = (config.BASELINE_LABEL if m == config.BASELINE_KEY
                 else config.OBJECTIVES.get(m, m))
        try:
            t0 = time.time()
            if m == config.BASELINE_KEY:
                r = baseline.greedy(orders, limits, settings)
            else:
                r = solver.solve(orders, limits, settings, objective=m)
            wall = time.time() - t0
            s = score(r, orders, price_fn, digest_lo, digest_hi)
            s["耗时秒"] = round(wall, 4)   # 统一用墙钟，含建模开销才公平
            results.append({
                "方案": m, "名称": label, "状态": "OK",
                "对照组": m == config.BASELINE_KEY,
                "评分": s,
                "库存影响": inventory_impact(r, pool, high_from, low_to),
                "warnings": r.get("warnings", []),
                "订单": r["订单"],
            })
        except solver.Infeasible as e:
            results.append({"方案": m, "名称": label, "状态": "INFEASIBLE",
                            "对照组": m == config.BASELINE_KEY, "reason": e.reason})
        except Exception as e:  # 单个方案炸掉不该拖垮整次对比
            log.exception("方案 %s 求解异常", m)
            results.append({"方案": m, "名称": label, "状态": "ERROR",
                            "对照组": m == config.BASELINE_KEY, "reason": str(e)})

    pareto = _pareto(orders, limits, settings, price_fn) if with_pareto else []

    return {
        "方案": results,
        "帕累托": pareto,
        "定价": price_cfg,
        "难用料阈值": {"低": round(digest_lo, 1), "高": round(digest_hi, 1)},
        "分段界线": {"高": high_from, "低": low_to},
        "口径说明": [
            "成本基于可编辑的定价曲线，当前为占位价格，绝对金额仅供相对比较",
            "库存消化用「难用料（两端料）」替代口径 —— 源表的月份列已损坏，无库龄数据",
            "计算时间为墙钟时间；贪心毫秒级、MILP 秒级，对业务无实质差别",
        ],
    }


def _pareto(orders, limits, settings, price_fn) -> list[dict]:
    """成本 ↔ 批次数的权衡前沿。

    ε-约束法：先求批次数最优值 k*，再把 max_batches 从 k* 逐档放宽，
    每档求最低成本。每个点的含义对业务方直白 —— 「最多用 N 批时，料本最低多少」。
    """
    st = dict(settings or {})
    try:
        base = solver.solve(orders, limits, {**st, "time_limit": 10}, objective="min_batch")
    except solver.Infeasible:
        return []
    k0 = base["总批次数"]

    pts = []
    for k in range(k0, k0 + PARETO_STEPS):
        try:
            r = solver.solve(orders, limits,
                             {**st, "max_batches": k, "time_limit": 8},
                             objective="min_cost")
        except solver.Infeasible:
            continue
        picks = [p for o in r["订单"] for p in o["用料"]]
        if not picks:
            continue
        qty = np.array([p["取用kg"] for p in picks])
        bloom = np.array([p.get(config.TARGET_COL, np.nan) for p in picks])
        pts.append({
            "批次上限": k,
            "实际批次": r["总批次数"],
            "料本": round(float((qty * price_fn(bloom)).sum()), 2),
            "冻力": round(float((qty * bloom).sum() / qty.sum()), 3),
            "达标": r["全部达标"],
        })

    # 同样批次数下只留最便宜的一点，并剔除被支配的点（批次更多却更贵）
    best: dict[int, dict] = {}
    for p in pts:
        k = p["实际批次"]
        if k not in best or p["料本"] < best[k]["料本"]:
            best[k] = p
    out = sorted(best.values(), key=lambda p: p["实际批次"])
    front, cheapest = [], float("inf")
    for p in out:
        if p["料本"] < cheapest:
            cheapest = p["料本"]
            front.append(p)
    return front
