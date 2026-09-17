"""配料求解器：多订单、多目标指标、共享库存的混合整数线性规划（MILP）。

# 为什么是线性规划而不是搜索

加权平均约束本来是分式的（分母含未知数）：

    Σ xᵢ·vᵢⱼ / Σ xᵢ  ≥  tⱼ

**移项**即可线性化，且不需要假设总量固定：

    Σ xᵢ·(vᵢⱼ − tⱼ)  ≥  0          ← 线性，对任意总量都精确成立

早期版本写成 `Σ xᵢ·vᵢⱼ ≥ tⱼ·W`（用名义订单量 W 代替实际总量）。可拆批时
Σxᵢ ≡ W，两种写法等价；但整批取料后总量只能落在 W 附近的窗口里，
`≥ tⱼ·W` 推出的是 avg ≥ tⱼ·W/Σx —— 总量超过 W 时 avg 可以低于 tⱼ，
**约束不再保证达标**。现在统一用移项形式。

所以整个问题落在 MILP 的范畴内，可以求全局最优，不需要遍历组合或启发式搜索。
再引入 0/1 变量 yᵢ 表示「该批是否启用」，就能控制批次数、最小取用量、整批取料。

# 整批取料（whole_batch，默认开启）

车间现实是拆批会留下要重新化验、贴标、入库的零头，所以一批料用了就得用完。
建模上只需要一条等式：

    xᵢ = capᵢ · yᵢ            取了就取满，没有中间状态

代价是**总量无法精确命中订单量** —— 批重是 63~934kg 的任意实数，凑出恰好
3000kg 是子集和问题，实际几乎无解。所以总量约束放成 W·(1 ± weight_tol_pct)，
并用 QTY_WEIGHT 那一项把实配量拉回名义订单量附近（否则「批次数最少」会让
求解器一路贴着窗口下沿走，3000kg 的单只配 2852kg）。

# 两类指标约束

    目标值 (target)  逐单指定，形如「PH 5.8 ± 0.2」，落在 [值−容差, 值+容差]
    上下限 (limit)   全局设定，形如「水分 8 ~ 14%」，落在 [下限, 上限]

同一指标可以同时有两者（取交集），互不冲突。客户规格单上写明的项填成目标值，
其余只要不超标的项留给上下限即可。

# 多订单

K 张订单共享同一批库存，变量是 x[i,k]（第 i 批分给第 k 单多少）。
关键约束是 Σ_k x[i,k] ≤ capᵢ —— 没有它，两张单会同时把同一批料算进自己的解。

# 目标函数

    minimize   BIG · Σ y   +   t

第一项让批次数最少（减少投料、清洗、转运工作量），BIG 保证它绝对优先；
第二项 t 是**所有订单、所有目标指标**偏差的最大归一化值，用来在批次数相同的
若干解中挑最居中的 —— 否则求解器会随手给一个贴着容差边界的解。
"""

from __future__ import annotations

import logging
import time

import numpy as np
import polars as pl
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import coo_matrix

from . import config, data

log = logging.getLogger(__name__)

# 批次数相对于居中度的优先级权重。t ∈ [0,1]，所以 BIG 只要大于 1 就能保证
# 「少用一批」永远优于「更居中」；不能取得太大 —— 否则 MILP 的相对间隙
# （mip_rel_gap × 目标值）会大到把 t 整个吞掉，求解器就不再区分居中程度了。
BIG = 100.0

# 近邻集里某项限制的达标比例低于此值时，补充「矫正料」进候选集（见 3.5 节注释）
SATISFY_FRAC = 0.10

# 取料偏好的权重与偏差上限。三项目标的优先级必须是：
#
#     批次数 (BIG=100/批)  ≫  取料偏好 (≤ 30×2=60)  ≫  居中 t (≤1)
#
# 偏好必须压过居中：「保留低冻力料」意味着宁可配到 290.5 也不去动 289 的料，
# 而居中项想把结果精确拉到 290.0 —— 两者天然冲突，谁大谁说了算。
# 偏差按容差（而不是整个冻力跨度）归一化，否则 1 个 Bloom 的偏差被 255 的
# 跨度一除就只剩 0.004，直接被居中项淹没 —— 这个坑和 BIG 那个同源。
# 再对归一化偏差封顶，防止极端料的惩罚膨胀到反过来压过批次数。
PREF_WEIGHT = 30.0
PREF_DEV_CAP = 2.0

# 可选目标函数的权重。**用户明确选定的目标必须是主目标**，否则会被批次数项压死：
#
#   消化难用料要求低+高配对取料，批次数必然上升 3~4 批 = +300~400 惩罚，
#   而奖励若只有 40，求解器永远不会去做 —— 表现就是「选了 max_digest 却消化 0kg」，
#   四个目标函数全部塌缩成 min_batch，对比页看不出任何差异。
#
# 所以选定目标时的优先级是：
#
#   选定目标 (≤5000)  ≫  批次数 (100/批)  ≫  居中 t (≤1)
#
# 目标项统一归一到「总量 ∈ [0,1]」再乘权重，量纲可控。
# objective="min_batch" 时该项恒为 0，退回原来的批次数优先，行为不变。
OBJ_WEIGHT = 5000.0

# 实配总量偏离订单量的惩罚。整批取料下总量只能落在 ±weight_tol_pct 的窗口里，
# 而任何「越少越好」的目标都会把这个窗口当成套利空间：
#
#   · 批次数最少 → 一路贴窗口下沿，3000kg 的单只配 2852kg（−4.9%）
#   · 料本最低   → 更狠，4000kg 的单只配 3800kg，料本「省」5% 全靠少发 200kg，
#                  单位成本 54.0 vs 54.1 几乎一样 —— 对比记分卡直接失去意义
#
# 所以交付量必须是**最高优先级**：它是合同义务，不是可优化项。各目标只能在
# 「量已经配对」的前提下再去优化。
#
#   贴近订单量 (≤20000)  ≫  选定目标 (≤5000)  ≫  批次数 (100/批)  ≫  居中 t (≤1)
#
# d 按**窗口宽度**归一化（d ∈ [0,1]）而不是按 W，否则容差一改权重量级就飘。
QTY_WEIGHT = 20000.0


def interval(val: float, tol: float, direction: str) -> tuple[float, float]:
    """把「目标值 + 容差 + 方向」翻译成实际的可接受区间。

    对应客户规格单上的符号：冻力写「≥210」就是 min，水分写「≤12」就是 max。
    """
    if direction == "min":
        return val, val + tol
    if direction == "max":
        return val - tol, val
    return val - tol, val + tol


class Infeasible(Exception):
    """预检就能判定无解，附带可读的原因。"""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


def _target_keys(orders: list[dict]) -> list[str]:
    """所有订单用到的目标指标并集，保持 config 中的声明顺序。"""
    used = {k for o in orders for k in o["targets"]}
    return [k for k in config.TARGETABLE_KEYS if k in used]


# ---------------------------------------------------------------- 候选池


def build_pool(limits: dict, worst_grade: str | None, need_cols: list[str]) -> pl.DataFrame:
    """构造候选池：剔除关键指标缺失、等级不达标的批次。"""
    df = data.get_inventory()

    need = [config.WEIGHT_COL, *need_cols]
    need += [k for k, v in limits.items() if v.get("enabled") and k in df.columns]
    need = [c for c in dict.fromkeys(need) if c in df.columns]
    df = df.filter(pl.all_horizontal([pl.col(c).is_not_null() for c in need]))

    if worst_grade and worst_grade in config.GRADE_ORDER:
        cap_grade = config.GRADE_ORDER[worst_grade]
        # 等级缺失的批次一并放行，避免因为一个没化验的字段把料全筛掉
        df = df.filter(
            pl.col("水不溶物等级").is_null() | (pl.col("水不溶物等级") <= cap_grade)
        )

    return df


# ---------------------------------------------------------------- 预检


def precheck(pool: pl.DataFrame, orders: list[dict], limits: dict,
              w_tol: float = 0.0) -> None:
    """在建模之前挡掉必然无解的情况，给出可执行的原因。

    加权平均永远落在参与批次的极值之间，所以目标只要超出库存的 [min, max]
    区间就再怎么配也配不出来 —— 这类问题必须直接告诉用户，而不是让求解器
    跑几十秒后回一句 infeasible。
    """
    if pool.height == 0:
        raise Infeasible("候选池为空：所有批次都因指标缺失或等级不达标被排除")

    total_need = sum(o["weight"] for o in orders) * (1 - w_tol)
    total_have = float(pool[config.WEIGHT_COL].sum())
    if total_have < total_need:
        raise Infeasible(
            f"库存总量不足：可用 {total_have:,.1f} kg，订单合计需要 {total_need:,.1f} kg",
            {"可用": total_have, "需求": total_need},
        )

    # 整批取料 + 小单：连最小的一批都装不进总量窗口，必然无解。
    # 不先挡掉的话求解器会跑满时限再回一句 infeasible，用户还以为是规格太严。
    if w_tol > 0:
        min_cap = float(pool[config.WEIGHT_COL].min())
        for o in orders:
            ceiling = o["weight"] * (1 + w_tol)
            if ceiling < min_cap:
                raise Infeasible(
                    f"订单「{o['name']}」需求 {o['weight']:,.1f} kg（上限 {ceiling:,.1f} kg）"
                    f"小于库存中最小的一批 {min_cap:,.1f} kg —— 整批取料下无法成单。"
                    f"请提高总量容差、加大订单量，或关闭「整批取料」允许拆批",
                    {"订单": o["name"], "最小批重": min_cap, "总量上限": ceiling},
                )

    label = {p["key"]: p["label"] for p in config.PARAMS}

    for o in orders:
        for key, (val, tol, direction) in o["targets"].items():
            s = pool[key].drop_nulls()
            if s.len() == 0:
                raise Infeasible(f"候选池中 {label.get(key, key)} 全部缺失，无法作为目标")
            lo, hi = float(s.min()), float(s.max())
            want_lo, want_hi = interval(val, tol, direction)
            if want_hi < lo or want_lo > hi:
                sym = {"min": "≥", "max": "≤", "both": "±"}[direction]
                desc = f"{sym}{val}" if direction != "both" else f"{val}±{tol}"
                raise Infeasible(
                    f"订单「{o['name']}」{label.get(key, key)}要求 {desc} "
                    f"（{want_lo:.3f} ~ {want_hi:.3f}）超出库存可达区间 [{lo:.3f}, {hi:.3f}]",
                    {"订单": o["name"], "参数": key, "库存最小": lo, "库存最大": hi},
                )

    for key, lim in limits.items():
        if not lim.get("enabled") or key not in pool.columns:
            continue
        s = pool[key].drop_nulls()
        if s.len() == 0:
            continue
        lo, hi = float(s.min()), float(s.max())
        want_lo, want_hi = lim.get("lo"), lim.get("hi")
        if want_lo is not None and want_lo > hi:
            raise Infeasible(
                f"{label.get(key, key)} 下限 {want_lo} 高于库存最大值 {hi:.3f}，无法达成",
                {"参数": key, "库存最小": lo, "库存最大": hi},
            )
        if want_hi is not None and want_hi < lo:
            raise Infeasible(
                f"{label.get(key, key)} 上限 {want_hi} 低于库存最小值 {lo:.3f}，无法达成",
                {"参数": key, "库存最小": lo, "库存最大": hi},
            )

    # 目标与全局上下限打架时，交集为空必然无解 —— 单看任何一边都发现不了
    for o in orders:
        for key, (val, tol, direction) in o["targets"].items():
            lim = limits.get(key) or {}
            if not lim.get("enabled"):
                continue
            want_lo, want_hi = interval(val, tol, direction)
            lo, hi = lim.get("lo"), lim.get("hi")
            if lo is not None and want_hi < lo:
                raise Infeasible(
                    f"订单「{o['name']}」{label.get(key, key)}目标区间 "
                    f"({want_lo:.3f} ~ {want_hi:.3f}) 与参数设定的下限 {lo} 冲突",
                    {"订单": o["name"], "参数": key},
                )
            if hi is not None and want_lo > hi:
                raise Infeasible(
                    f"订单「{o['name']}」{label.get(key, key)}目标区间 "
                    f"({want_lo:.3f} ~ {want_hi:.3f}) 与参数设定的上限 {hi} 冲突",
                    {"订单": o["name"], "参数": key},
                )


# ---------------------------------------------------------------- LP 定位

def _objective_unit_coef(
    objective: str,
    orders: list[dict],
    vals: dict[str, np.ndarray],
    price_fn,
    digest_mask: np.ndarray | None,
) -> np.ndarray | None:
    """目标函数在「每公斤」尺度上的系数，供 LP 定位使用。

    与 _objective_terms 同源，区别是不做订单归一化 —— LP 是逐单跑的，
    只需要相对大小正确。多订单时取第一单的目标做代表（LP 只是提示，不是最终解）。
    """
    if objective == "min_batch":
        return None
    if objective == "min_cost":
        return np.asarray(price_fn(vals[config.TARGET_COL]), dtype=float)
    if objective == "max_digest":
        if digest_mask is None:
            return None
        return np.where(digest_mask, 0.0, 1.0)
    if objective == "min_redundancy":
        o = orders[0]
        coef = np.zeros(len(next(iter(vals.values()))))
        for key, (val, tol, direction) in o["targets"].items():
            if direction == "both":
                continue
            sign = 1.0 if direction == "min" else -1.0
            coef += sign * (vals[key] - val) / tol
        return coef
    return None




def _lp_support(
    orders: list[dict],
    vals: dict[str, np.ndarray],
    cap: np.ndarray,
    limits: dict,
    active: list[str],
    obj_coef: np.ndarray,
) -> list[np.ndarray]:
    """用 LP 松弛在**全库存**上定位「这个目标真正需要哪几批料」。

    去掉 0/1 变量后问题退化成纯线性规划，3784 批全池只要 ~24 ms。
    而线性规划的基本解至多有「约束个数」个非零分量 —— 实测支撑集只有 10 批，
    正好是成本最优需要的那几批（便宜的低端料 + 把加权平均拉回目标的高端料）。

    把这个支撑集塞进候选集，MILP 再在小集合上精算批次数与最小取用量。

    为什么必须这么做：目标函数的取值遍布整个库存时，任何**邻域式预筛**都会
    系统性地丢掉最优解。实测把低端料打到 3 折后，默认预筛下 min_cost 一点低端料
    都不用（料本 162,000），而全池最优是 147,867 —— 差 8.7%，全被预筛吃掉了。
    此前尝试过「注入最便宜的料」「注入最极端的料」等启发式，每加一个目标就要
    重写一次规则，且都补不全。LP 松弛是这类问题的标准答案，一次解决所有目标。
    """
    out: list[np.ndarray] = []
    n = len(cap)
    for o in orders:
        W = float(o["weight"])
        A_ub: list[np.ndarray] = []
        b_ub: list[float] = []

        for key, (val, tol, direction) in o["targets"].items():
            v = vals[key]
            lo, hi = interval(val, tol, direction)
            A_ub.append(-v); b_ub.append(-lo * W)
            A_ub.append(v);  b_ub.append(hi * W)
        for key in active:
            v = vals[key]
            lo, hi = limits[key].get("lo"), limits[key].get("hi")
            if lo is not None:
                A_ub.append(-v); b_ub.append(-lo * W)
            if hi is not None:
                A_ub.append(v);  b_ub.append(hi * W)

        try:
            res = linprog(
                obj_coef,
                A_ub=np.array(A_ub) if A_ub else None,
                b_ub=np.array(b_ub) if b_ub else None,
                A_eq=np.ones((1, n)), b_eq=np.array([W]),
                bounds=list(zip(np.zeros(n), cap)),
                method="highs",
            )
        except Exception:  # LP 只是提示，失败不该拖垮主流程
            log.warning("LP 定位失败，退回纯邻域预筛", exc_info=True)
            out.append(np.zeros(0, dtype=int)); continue

        out.append(np.flatnonzero(res.x > 1e-6) if res.x is not None
                   else np.zeros(0, dtype=int))
    return out


# ---------------------------------------------------------------- 候选预筛


def _candidates(
    orders: list[dict],
    vals: dict[str, np.ndarray],
    limits: dict,
    active: list[str],
    n_cand: int,
    cap: np.ndarray,
    digest_mask: np.ndarray | None = None,
    lp_hint: list[np.ndarray] | None = None,
) -> list[np.ndarray]:
    """每单挑一批候选批次参与建模。

    不预筛的话 3878 批 × K 单的 0/1 变量会让求解时间从秒级涨到分钟级。
    主体按「到本单各项目标的归一化距离」取最近的 N 批，再补两类料：

      1. 跨越目标两侧的料 —— 只取最近的会出现全部偏向一侧的情况，
         那样加权平均怎么配都过不去，属于假无解。
      2. 限制项的矫正料 —— 近邻集里某项限制达标比例过低时，补入本身满足
         该限制、且离目标最近的若干批。实测 PH 收紧到 [5.0,5.2] 时，冻力
         最接近 210 的 200 批里只有 1 批达标（全库存有 46 批），不补必然误报无解。
    """
    n_side = max(8, n_cand // 20)
    n_fix = max(15, n_cand // 10)
    total = len(next(iter(vals.values())))

    out: list[np.ndarray] = []
    for oi, o in enumerate(orders):
        # 多目标归一化距离：各项偏差先除以自己的容差，量纲才可比。
        # 距离保持对称（不看方向）—— 单边约束下低于目标的料依然有用，
        # 它是压秤的「垫料」，靠高值料把加权平均拉上去。
        dist = np.zeros(total)
        for key, (val, tol, _dir) in o["targets"].items():
            dist += ((vals[key] - val) / tol) ** 2
        order = np.argsort(dist)
        near = order[: min(n_cand, total)]

        # 候选集必须装得下这张单，否则会出现「全库存 1928 吨，配 150 吨却报无解」——
        # 无解的其实是预筛出来的那 200 批，不是库存。按需要的量往下扩，留 3 倍余量
        # 供混配调节（只用刚好够的量意味着几乎没有配比自由度）。
        need = float(o["weight"]) * 3.0
        if cap[near].sum() < need:
            cum = np.cumsum(cap[order])
            enough = int(np.searchsorted(cum, need)) + 1
            near = order[: min(max(enough, n_cand), total)]

        extra: list[np.ndarray] = []

        # (1) 保证每项目标的两侧都有料可用
        for key, (val, tol, _dir) in o["targets"].items():
            v = vals[key]
            for side in (v < val, v > val):
                idx = np.flatnonzero(side)
                if idx.size == 0:
                    continue
                extra.append(idx[np.argsort(np.abs(v[idx] - val))[:n_side]])

        # (2) 限制项的矫正料
        for key in active:
            lo, hi = limits[key].get("lo"), limits[key].get("hi")
            v = vals[key]
            ok = np.ones(total, dtype=bool)
            if lo is not None:
                ok &= v >= lo
            if hi is not None:
                ok &= v <= hi
            if ok[near].mean() >= SATISFY_FRAC:
                continue
            ok_idx = np.flatnonzero(ok)
            if ok_idx.size == 0:
                continue
            extra.append(ok_idx[np.argsort(dist[ok_idx])[:n_fix]])

        # (3) 目标是「消化难用料」时，必须把难用料放进候选集 ——
        #     它们离目标很远，按距离预筛必然被全部剔除，目标函数就选不到了。
        #     实测不补这一步，max_digest 消化量恒为 0。
        #
        #     注意必须**两端各取一半**：只按距离排序的话，名额会被离目标更近的
        #     那一端全部占满（本例中 100 个名额全给了高端料），没有另一端的料
        #     压秤，加权平均立刻顶到容差边界，消化量被死死卡住。
        if digest_mask is not None and digest_mask.any():
            main = config.TARGET_COL if config.TARGET_COL in o["targets"] else None
            if main:
                mv = vals[main]
                mt = o["targets"][main][0]
                half = max(n_cand // 4, 20)
                for side in (digest_mask & (mv < mt), digest_mask & (mv > mt)):
                    idx = np.flatnonzero(side)
                    if idx.size:
                        extra.append(idx[np.argsort(dist[idx])[:half]])
            else:
                hard = np.flatnonzero(digest_mask)
                extra.append(hard[np.argsort(dist[hard])[: n_cand // 2]])

        # (4) 目标是「料本最低」时，把**便宜料**放进候选集。
        #     和 (3) 同源：便宜料往往落在冻力两端（尤其呆滞料被打折时），
        #     按距离预筛必然筛掉，求解器看不见就选不了 —— 表现是「把低端料打到
        #     3 折，min_cost 依然一点不用」，而手算明明更便宜。
        #     同样必须两端各取一半：只取一端就没有料把加权平均拉回目标。
        # (4) LP 松弛的支撑集 —— 见 _lp_support 的说明。
        #     这一条取代了此前按「最便宜 / 最极端」注入的一堆启发式：目标函数的
        #     取值遍布整个库存时，任何邻域式预筛都会系统性丢掉最优解，而
        #     打补丁的方式每加一个目标就要重写一次注入规则。
        if lp_hint is not None and len(lp_hint) > oi and lp_hint[oi].size:
            extra.append(lp_hint[oi])

        out.append(np.unique(np.concatenate([near, *extra])) if extra else near)
    return out


# ---------------------------------------------------------------- 求解


def _objective_terms(
    objective: str,
    orders: list[dict],
    pairs: list[tuple[int, int]],
    by_order: dict[int, list[int]],
    vals: dict[str, np.ndarray],
    price_fn,
    digest_mask: np.ndarray | None,
) -> np.ndarray:
    """按选定目标构造 x 变量的成本系数。

    全部是**常数系数 × x**，所以目标函数保持线性，不增加求解难度 ——
    这也是「同一套约束、不同目标」能公平对比的前提。

    系数统一归一到「每公斤的相对代价」量级再乘 OBJ_WEIGHT，这样它与批次数项
    （BIG=100/批）的相对关系可控，不会重演「次级目标被数值淹没」那两次坑
    （见 docs/03 §3.3）。
    """
    c = np.zeros(len(pairs))
    if objective == "min_batch":
        return c  # 只靠 BIG·Σy，无需 x 项

    for k, o in enumerate(orders):
        W = float(o["weight"])
        ps = by_order[k]
        if not ps:
            continue

        if objective == "min_cost":
            p = price_fn(vals[config.TARGET_COL])
            lo, hi = float(p.min()), float(p.max())
            rng = (hi - lo) or 1.0
            for q in ps:
                c[q] += OBJ_WEIGHT * (p[pairs[q][1]] - lo) / rng / W

        elif objective == "min_redundancy":
            # 冗余 = 交付指标超出客户要求的部分。方向决定「超出」在哪一侧：
            #   ≥ 方向（冻力）配得越高越冗余 → 系数 +v
            #   ≤ 方向（水分）配得越低越冗余 → 系数 −v
            #   ± 方向两侧都算，线性目标推不了两个方向，交给 t 项处理
            for key, (val, tol, direction) in o["targets"].items():
                if direction == "both":
                    continue
                v = vals[key]
                sign = 1.0 if direction == "min" else -1.0
                for q in ps:
                    c[q] += OBJ_WEIGHT * sign * (v[pairs[q][1]] - val) / tol / W

        elif objective == "max_digest":
            # 「最大化消化难用料」等价于「最小化取用非难用料」。写成后者是为了让
            # 目标函数保持非负 —— MILP 的相对收敛间隙在目标值跨零时行为不稳。
            if digest_mask is None:
                continue
            for q in ps:
                if not digest_mask[pairs[q][1]]:
                    c[q] += OBJ_WEIGHT / W

    return c


def digest_mask_for(pool: pl.DataFrame) -> np.ndarray:
    """「难用料」标记：冻力落在两端分位之外的批次。

    这类料单独很难达标、必须靠混配才能出货。没有库龄数据时，用它替代
    真正的呆滞料口径 —— 是替代品，不是库存周转率。
    """
    b = pool[config.TARGET_COL].to_numpy().astype(float)
    s = pool[config.TARGET_COL].drop_nulls()
    if s.len() == 0:
        return np.zeros(len(b), dtype=bool)
    lo = float(s.quantile(config.HARD_LOW_Q))
    hi = float(s.quantile(config.HARD_HIGH_Q))
    return (b <= lo) | (b >= hi)


def solve(
    orders: list[dict],
    limits: dict | None = None,
    settings: dict | None = None,
    objective: str = "min_batch",
) -> dict:
    """求解配料方案。

    orders    : [{"name": 单号, "weight": 需求量kg, "targets": {指标: (目标值, 容差, 方向)}}]
    limits    : {参数名: {"lo":, "hi":, "enabled":}}
    settings  : min_take / max_batches / candidates / time_limit / worst_grade
    objective : min_batch / min_cost / min_redundancy / max_digest
    """
    from . import store

    cfg = store.load()
    limits = limits if limits is not None else cfg["limits"]
    st = {**cfg["settings"], **(settings or {})}

    if not orders:
        raise Infeasible("没有输入任何订单")

    min_take = float(st.get("min_take") or 0)
    max_batches = int(st.get("max_batches") or 0)
    whole_batch = bool(st.get("whole_batch", True))
    # 可拆批时总量能精确命中，容差归零；整批取料才需要窗口
    w_tol = (float(st.get("weight_tol_pct") or 0) / 100.0) if whole_batch else 0.0
    n_cand = max(20, int(st.get("candidates") or 200))
    time_limit = float(st.get("time_limit") or 15)

    tgt_keys = _target_keys(orders)
    pool = build_pool(limits, st.get("worst_grade"), tgt_keys)
    precheck(pool, orders, limits, w_tol)

    cap_all = pool[config.WEIGHT_COL].to_numpy().astype(float)
    ids_all = pool[config.ID_COL].to_numpy()
    active = [k for k, v in limits.items() if v.get("enabled") and k in pool.columns]

    # 目标列与限制列的取值，统一放一份，后面建模和结果组装都用它
    vals: dict[str, np.ndarray] = {
        k: pool[k].to_numpy().astype(float) for k in dict.fromkeys([*tgt_keys, *active])
    }

    K = len(orders)
    # 只有「消化难用料」这个目标需要把两端料塞进候选集；其余目标塞进去只会
    # 无谓放大问题规模。
    dmask = digest_mask_for(pool) if objective == "max_digest" else None

    # 非默认目标时先用 LP 松弛在全池定位（~24ms），把支撑集喂给候选预筛
    lp_hint = None
    if objective != "min_batch":
        from . import pricing as _p
        coef = _objective_unit_coef(objective, orders, vals, _p.curve(),
                                    dmask if dmask is not None else digest_mask_for(pool))
        if coef is not None:
            lp_hint = _lp_support(orders, vals, cap_all, limits, active, coef)

    cand = _candidates(orders, vals, limits, active, n_cand, cap_all, dmask, lp_hint)

    pairs = [(k, int(i)) for k in range(K) for i in cand[k]]
    P = len(pairs)
    if P == 0:
        raise Infeasible("预筛后没有可用候选批次")

    used_batches = sorted({i for _, i in pairs})

    NV = 2 * P + 2  # x(P) | y(P) | t | d
    T = 2 * P       # t：最大归一化指标偏差
    D = 2 * P + 1   # d：归一化总量偏差（仅整批取料时生效）

    rows: list[int] = []
    cols: list[int] = []
    mvals: list[float] = []
    lb: list[float] = []
    ub: list[float] = []
    r = 0

    def add_row(entries, low, high):
        nonlocal r
        for c, v in entries:
            rows.append(r)
            cols.append(c)
            mvals.append(v)
        lb.append(low)
        ub.append(high)
        r += 1

    by_order: dict[int, list[int]] = {k: [] for k in range(K)}
    for p, (k, _i) in enumerate(pairs):
        by_order[k].append(p)

    for k, o in enumerate(orders):
        W = float(o["weight"])
        ps = by_order[k]

        # (A) 订单总量。整批取料时总量不可能精确等于 W —— 批重是 63~934kg 的
        #     任意实数，凑出恰好 3000kg 是子集和问题，实际几乎无解。所以放成区间。
        add_row([(p, 1.0) for p in ps], W * (1 - w_tol), W * (1 + w_tol))

        # d ≥ |Σx − W| / (W·w_tol)，即「用掉了多少总量容差」，范围 [0,1]
        if w_tol > 0:
            g = 1.0 / (W * w_tol)
            add_row([(p, g) for p in ps] + [(D, -1.0)], -np.inf, 1.0 / w_tol)
            add_row([(p, g) for p in ps] + [(D, 1.0)], 1.0 / w_tol, np.inf)

        # (B) 每项目标，按方向建约束。
        #
        #     写成**移项形式** Σx·(v−val) 而不是 Σx·v ≥ val·W：
        #     后者用的是名义订单量 W，一旦实配总量 T ≠ W（整批取料必然如此），
        #     Σx·v ≥ val·W 推出的是 avg ≥ val·W/T，T>W 时 avg 可以低于 val ——
        #     约束就不再保证达标了。移项形式
        #
        #         Σx·(v−val) ≥ 0  ⟺  Σx·v / Σx ≥ val
        #
        #     对任意总量都精确成立，且仍然是线性的。
        #
        #     t 是「用掉了多少容差」，进目标函数后驱动结果贴近目标值。
        #     t 那一侧的 RHS 仍用 W 近似（精确写法 t·tol·Σx 是双线性的，
        #     MILP 处理不了）；总量偏差被 (A) 限在 ±w_tol 内，所以这个近似
        #     只影响居中程度，不影响达标判定。
        for key, (val, tol, direction) in o["targets"].items():
            v = vals[key]
            coef = [(p, v[pairs[p][1]] - val) for p in ps]
            if direction == "min":
                add_row(coef, 0.0, np.inf)                              # avg ≥ val
                add_row(coef + [(T, -tol * W)], -np.inf, 0.0)           # avg ≤ val + t·tol
            elif direction == "max":
                add_row(coef, -np.inf, 0.0)                             # avg ≤ val
                add_row(coef + [(T, tol * W)], 0.0, np.inf)            # avg ≥ val − t·tol
            else:
                add_row(coef + [(T, -tol * W)], -np.inf, 0.0)
                add_row(coef + [(T, tol * W)], 0.0, np.inf)

        # (C) 全局上下限，同样用移项形式，理由见 (B)
        for key in active:
            v = vals[key]
            lo, hi = limits[key].get("lo"), limits[key].get("hi")
            if lo is not None:
                add_row([(p, v[pairs[p][1]] - lo) for p in ps], 0.0, np.inf)
            if hi is not None:
                add_row([(p, v[pairs[p][1]] - hi) for p in ps], -np.inf, 0.0)

        # (G) 每单批次数上限
        if max_batches > 0:
            add_row([(P + p, 1.0) for p in ps], 0, max_batches)

    # (D) 库存不可超发 —— 多订单共享库存的关键约束
    per_batch: dict[int, list[int]] = {}
    for p, (_k, i) in enumerate(pairs):
        per_batch.setdefault(i, []).append(p)
    for i, ps in per_batch.items():
        add_row([(p, 1.0) for p in ps], -np.inf, cap_all[i])

    for p, (_k, i) in enumerate(pairs):
        if whole_batch:
            # 整批取料：x = cap·y，取了就得取完，没有中间状态。
            # 一条等式就把「可拆批」切换成「整批」，(B)(C) 全部不用改 ——
            # 它们只认 x，不管 x 是怎么来的。
            add_row([(p, 1.0), (P + p, -cap_all[i])], 0.0, 0.0)
        else:
            # (E)(F) 可拆批：x ≤ cap·y，并受单批最小取用量约束
            add_row([(p, 1.0), (P + p, -cap_all[i])], -np.inf, 0.0)
            if min_take > 0:
                add_row([(p, 1.0), (P + p, -min_take)], 0.0, np.inf)

    A = coo_matrix((mvals, (rows, cols)), shape=(r, NV)).tocsr()

    cost = np.zeros(NV)
    cost[P : 2 * P] = BIG
    cost[T] = 1.0
    cost[D] = QTY_WEIGHT if w_tol > 0 else 0.0

    # -------- 可选目标函数 --------
    # 约束完全不变，只换目标 —— 这是「同一套规格、不同算法」能公平对比的前提。
    if objective != "min_batch":
        from . import pricing

        cost[:P] += _objective_terms(
            objective, orders, pairs, by_order, vals,
            pricing.curve(), dmask if dmask is not None else digest_mask_for(pool),
        )

    # -------- 取料偏好 --------
    # 加权平均被订单总量钉死了，所以「配出来多少冻力」是定的；能选的是**用哪一端的料**。
    # 目标 210 既可以用 205+215 这样的中间料凑，也可以用 60+315 这样的两头料凑 ——
    # 后者会啃掉稀缺的高冻力库存。
    #
    #   save_high 保留高冻力料：惩罚取用高于目标的料，逼求解器用贴近目标的中间料
    #   save_low  保留低冻力料：惩罚取用低于目标的料，结果几乎只用目标值以上的料
    #
    # 系数是常数（批次的冻力已知），所以仍然是线性目标，不增加求解难度。
    bloom = vals.get(config.TARGET_COL)
    if bloom is not None:
        # 「高/低冻力料」的绝对界线来自参数设定页，是库存政策而不是订单属性 ——
        # 配 210 的单时，215 的料并不稀缺，真正该保护的是 275 以上的。
        high_from = float(st.get("high_bloom_from") or 0.0)
        low_to = float(st.get("low_bloom_to") or 0.0)

        for k, o in enumerate(orders):
            pref = o.get("material_pref", "balanced")
            if pref == "balanced" or config.TARGET_COL not in o["targets"]:
                continue
            val, tol, _dir = o["targets"][config.TARGET_COL]
            W = float(o["weight"])

            # 界线要和本单目标取合：目标本身已经落在保护区里时（比如目标 290、
            # 保护线 275），那段料非用不可，只能退化成「尽量贴着目标取」。
            if pref == "save_high":
                threshold = max(high_from, val)
            else:
                threshold = min(low_to, val) if low_to > 0 else val

            for p in by_order[k]:
                b = bloom[pairs[p][1]]
                dev = (b - threshold) if pref == "save_high" else (threshold - b)
                if dev <= 0:
                    continue  # 不在保护区内的料，随便用
                dev_norm = min(dev / tol, PREF_DEV_CAP)
                cost[p] += PREF_WEIGHT * dev_norm / W

    integrality = np.zeros(NV)
    integrality[P : 2 * P] = 1

    x_hi = np.array([cap_all[i] for _k, i in pairs], dtype=float)

    def run(t_max: float):
        return milp(
            c=cost,
            constraints=[LinearConstraint(A, np.array(lb), np.array(ub))],
            integrality=integrality,
            bounds=Bounds(np.zeros(NV), np.r_[x_hi, np.ones(P), t_max, 1.0]),
            options={"time_limit": time_limit, "mip_rel_gap": 1e-6},
        )

    t0 = time.time()
    res = run(1.0)
    violated = False
    if res.x is None:
        # 容差内无解时放开 t，给出「最接近能做到的」方案并明确标注超差，
        # 比只回一句 infeasible 有用得多。
        log.info("容差内无解，放开容差重试")
        res = run(50.0)
        violated = True
    elapsed = time.time() - t0

    if res.x is None:
        # 整批取料是「凑数字」问题（子集和），无解往往不是料不够而是**凑不出来**，
        # 尤其小单：既要重量落进总量窗口，又要指标落进容差。通用提示在这种情况下
        # 帮不上忙，所以逐单算出实际可用的批次数，把杠杆指清楚。
        if whole_batch:
            hint = []
            wcol = pool[config.WEIGHT_COL]
            for o in orders:
                W = float(o["weight"])
                lo_w, hi_w = W * (1 - w_tol), W * (1 + w_tol)
                m = (wcol >= lo_w) & (wcol <= hi_w)
                for key, (val, tol, direction) in o["targets"].items():
                    t_lo, t_hi = interval(val, tol, direction)
                    m = m & (pool[key] >= t_lo) & (pool[key] <= t_hi)
                n_single = int(m.sum())
                need_batches = max(1, round(W / float(wcol.median())))
                hint.append(
                    f"订单「{o['name']}」总量窗口 {lo_w:,.0f}~{hi_w:,.0f} kg，"
                    f"约需 {need_batches} 批；单批就能满足全部指标的有 {n_single} 批"
                )
            raise Infeasible(
                "整批取料下凑不出可行组合。整批取料要求每批全用，"
                "既要总重量落进窗口、又要各指标落进容差，小单尤其难凑。\n"
                + "\n".join(hint)
                + "\n可调的杠杆：提高「总量容差%」、放宽指标容差、"
                "加大订单量，或关闭「整批取料」允许拆批",
                {"整批取料": True, "总量容差%": w_tol * 100},
            )
        raise Infeasible(
            "求解失败：当前目标与上下限组合下不存在可行方案，"
            "请放宽容差、放宽参数区间或降低订单量"
        )

    x = res.x[:P]
    timed_out = getattr(res, "status", None) == 1

    # -------- 组装结果 --------
    order_results = []
    all_used: set[int] = set()
    for k, o in enumerate(orders):
        ps = [p for p in by_order[k] if x[p] > 1e-6]
        tot = float(sum(x[p] for p in ps)) or 1.0

        show_cols = list(dict.fromkeys([*o["targets"], *active]))
        picks = []
        for p in sorted(ps, key=lambda q: -x[q]):
            i = pairs[p][1]
            all_used.add(i)
            picks.append(
                {
                    "数据编号": int(ids_all[i]),
                    "取用kg": round(float(x[p]), 2),
                    "库存kg": round(float(cap_all[i]), 1),
                    "占比": round(float(x[p]) / tot * 100, 2),
                    **{c: round(float(vals[c][i]), 3) for c in show_cols},
                }
            )

        achieved = []
        for key in show_cols:
            act = float(sum(x[p] * vals[key][pairs[p][1]] for p in ps) / tot)
            lim = limits.get(key) or {}
            g_lo = lim.get("lo") if lim.get("enabled") else None
            g_hi = lim.get("hi") if lim.get("enabled") else None

            if key in o["targets"]:
                val, tol, direction = o["targets"][key]
                want_lo, want_hi = interval(val, tol, direction)
                # 单边约束下「用掉多少容差」只算允许的那一侧
                if direction == "min":
                    used = max(0.0, act - val)
                elif direction == "max":
                    used = max(0.0, val - act)
                else:
                    used = abs(act - val)
                achieved.append(
                    {
                        "参数": key,
                        "类型": "target",
                        "方向": direction,
                        "目标": val,
                        "容差": tol,
                        "实际": round(act, 4),
                        "偏差": round(act - val, 4),
                        "下限": round(want_lo, 4),
                        "上限": round(want_hi, 4),
                        "达标": want_lo - 1e-6 <= act <= want_hi + 1e-6,
                        "占容差": round(used / tol, 3) if tol else 0.0,
                    }
                )
            else:
                ok = (g_lo is None or act >= g_lo - 1e-6) and (
                    g_hi is None or act <= g_hi + 1e-6
                )
                achieved.append(
                    {
                        "参数": key,
                        "类型": "limit",
                        "方向": None,
                        "目标": None,
                        "容差": None,
                        "实际": round(act, 4),
                        "偏差": None,
                        "下限": g_lo,
                        "上限": g_hi,
                        "达标": ok,
                        "占容差": None,
                    }
                )

        order_results.append(
            {
                "单号": o["name"],
                "取料偏好": o.get("material_pref", "balanced"),
                "需求量kg": o["weight"],
                "实配量kg": round(tot, 2),
                "批次数": len(picks),
                "目标数": len(o["targets"]),
                "全部达标": all(a["达标"] for a in achieved),
                "用料": picks,
                "达成": achieved,
            }
        )

    # 被多张订单同时取用的批次，车间需要留意分料顺序
    shared = []
    for i, ps in per_batch.items():
        ks = [pairs[p][0] for p in ps if x[p] > 1e-6]
        if len(ks) > 1:
            shared.append(
                {
                    "数据编号": int(ids_all[i]),
                    "库存kg": round(float(cap_all[i]), 1),
                    "被订单取用": [orders[k]["name"] for k in ks],
                    "合计取用kg": round(float(sum(x[p] for p in ps if x[p] > 1e-6)), 2),
                }
            )

    return {
        "status": "OK",
        "objective": objective,
        "warnings": (
            (["容差内无可行解，以下为最接近的方案，标红项已超差"] if violated else [])
            + (
                [
                    f"达到求解时限 {time_limit:g}s，返回的是已找到的最优方案。"
                    "实测好解通常在前几秒就已确定，剩余时间花在证明其最优性上，"
                    "延长时限一般不会改变结果。"
                ]
                if timed_out
                else []
            )
        ),
        "耗时秒": round(elapsed, 2),
        "候选池": pool.height,
        "参与候选": len(used_batches),
        "整批取料": whole_batch,
        "总量容差%": round(w_tol * 100, 2),
        "订单数": K,
        "总批次数": len(all_used),
        "总配料量kg": round(float(x.sum()), 2),
        "全部达标": all(o["全部达标"] for o in order_results),
        "共用批次": shared,
        "订单": order_results,
    }
