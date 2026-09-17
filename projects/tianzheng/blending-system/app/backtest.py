"""2025 年历史回溯：人工实际配法 vs 算法重配。

对照方式 —— 把同一个月里人工实际消耗掉的全部批次汇成一个料池，重新分配给当月
的全部配料单。**投入的料、产出的单、每张单的吨数三者完全不变**，只重新决定
「哪批料进哪张单」。所以这是一次严格的同条件对照，不存在算法多用了料的问题。

一个由此而来的恒等式（贯穿所有指标口径）：
    Σⱼ(实配ⱼ − 要求ⱼ)·Wⱼ ≡ Σᵢ 冻力ᵢ·Aᵢ − Σⱼ 要求ⱼ·Wⱼ = 常数
    ⇒ 过剩量 − 欠标量 = 常数 ⇒ **少欠一分，就少浪费一分**
月内总冻力守恒，算法能做的只有「把冻力搬到需要它的单上」，搬不出新的冻力来。

两档口径：
    pool   全月统筹 —— 料池 = 当月消耗的全部批次。理论上限。
    strict 严格时序 —— 批次 i 能进订单 j，当且仅当 i 在月初就已在库
           (入库月 < 配料月)，或人工已在 j 之前的日期用过它(即证明它那天存在)。
           当月入库的料占 21.6% 重量，这一档把算法的"预知未来"优势掐掉。
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np
import polars as pl
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from . import config, pricing

log = logging.getLogger(__name__)

ORDERS_PARQUET = config.PROJECT_ROOT / "blend_orders_2025.parquet"
COMPS_PARQUET = config.PROJECT_ROOT / "blend_components_2025.parquet"
CACHE = config.DATA_DIR / "backtest.json"

# 客户验收标准（客户提供）规格 -> (冻力≥, 粘度≥, T450≥, T620≥)
STD: dict[float, tuple[float, float, float, float]] = {
    120.0: (130.0, 3.5, 70.0, 90.0),
    140.0: (150.0, 3.6, 77.0, 93.0),
    160.0: (160.0, 3.8, 77.0, 93.0),
    200.0: (220.0, 4.8, 80.0, 90.0),
    220.0: (240.0, 4.8, 85.0, 90.0),
    240.0: (260.0, 4.8, 85.0, 90.0),
}
SPECS = ["bloom", "viscosity", "t450", "t620"]
SPEC_LABEL = {"bloom": "冻力", "viscosity": "粘度", "t450": "T450", "t620": "T620"}
BIGM = {"bloom": 320.0, "viscosity": 8.0, "t450": 100.0, "t620": 100.0}
# 水分覆盖率仅 36%、二氧化硫 4.5%，作为约束会凭空造数据，因此只做展示不进模型
GRADE_GUESS = [120.0, 140.0, 160.0, 180.0, 200.0, 220.0, 240.0]

_lock = threading.Lock()
_cache: dict | None = None


# ---------------------------------------------------------------- 数据


def _load() -> tuple[pl.DataFrame, pl.DataFrame]:
    for p in (ORDERS_PARQUET, COMPS_PARQUET):
        if not p.exists():
            raise FileNotFoundError(
                f"找不到 2025 台账解析结果：{p}\n请先在项目根目录运行 parse_2025.py 与 enrich_2025.py")
    b = pl.read_parquet(ORDERS_PARQUET)
    c = pl.read_parquet(COMPS_PARQUET)

    # 未标注规格的单：按已标注单据的实配冻力做最近邻推测
    kn = (b.filter(pl.col("target_grade").is_not_null())
            .with_columns(pl.col("target_grade").cast(pl.Float64).alias("tg")))
    cent = {g: kn.filter(pl.col("tg") == g)["calc_bloom"].mean() for g in GRADE_GUESS}
    cent = {g: v for g, v in cent.items() if v is not None}

    def guess(x):
        return None if x is None else min(cent, key=lambda g: abs(x - cent[g]))

    b = b.with_columns(
        pl.when(pl.col("target_grade").is_not_null())
          .then(pl.col("target_grade").cast(pl.Float64))
          .otherwise(pl.col("calc_bloom").map_elements(guess, return_dtype=pl.Float64))
          .alias("grade"),
        pl.when(pl.col("target_grade").is_not_null())
          .then(pl.lit("表内标注")).otherwise(pl.lit("推测")).alias("grade_src"),
    )
    return b, c


def _month_problem(b: pl.DataFrame, c: pl.DataFrame, month) -> tuple[pl.DataFrame, list[dict]]:
    """构造某月的料池与订单列表。month=None 表示无法推出日期的那一组。"""
    cm = c.filter(pl.col("blend_month").is_null() if month is None
                  else pl.col("blend_month") == month)
    bm = (b.filter(pl.col("blend_key").is_in(cm["blend_key"].unique().to_list()))
            .sort("sheet_ord", "row", "col"))

    used = cm.filter(pl.col("weight") > 0).with_columns(
        pl.coalesce([pl.col("batch_id"), pl.lit("〈无批号〉") + pl.col("blend_key")]).alias("bid"))
    pool = used.group_by("bid").agg(
        pl.col("weight").sum().alias("avail"),
        *[((pl.col(p) * pl.col("weight")).sum() / pl.col("weight").sum()).alias(p)
          for p in SPECS],
        pl.col("lot_ym").min().alias("lot_ym"),
        pl.col("blend_day").min().alias("first_day"),
    ).sort("bid")

    # 极少数批次缺指标（<0.1% 重量），用料池加权均值兜底，避免整批被排除破坏配平
    tot = float(pool["avail"].sum())
    for p in SPECS:
        s = pool.filter(pl.col(p).is_not_null())
        wm = float((s[p] * s["avail"]).sum() / s["avail"].sum()) if s.height else 0.0
        pool = pool.with_columns(pl.col(p).fill_null(wm))

    orders = []
    for r in bm.rows(named=True):
        g = r["grade"]
        if g in STD:
            req, src = dict(zip(SPECS, STD[g])), "客户标准"
        else:
            # 无客户标准：以人工实际达到的水平为要求，防止算法把差料倾倒进这些单
            req, src = {p: r[f"calc_{p}"] for p in SPECS}, "对齐人工"
        if any(v is None for v in req.values()):
            continue
        orders.append({
            "key": r["blend_key"], "id": r["blend_id"] or "(未编号)",
            "grade": g, "grade_src": r["grade_src"], "req_src": src,
            "day": r["blend_day"], "W": float(r["calc_weight"]),
            "req": req, "human": {p: r[f"calc_{p}"] for p in SPECS},
            "human_moisture": r["calc_moisture"], "n_comp": r["n_comp"],
        })
    return pool, orders


def _human_mix(c: pl.DataFrame, month) -> dict[str, list[dict]]:
    """人工的逐批投料，格式与算法配方对齐，便于前端并排展示。"""
    cm = c.filter(pl.col("blend_month").is_null() if month is None
                  else pl.col("blend_month") == month)
    out: dict[str, list[dict]] = {}
    for r in cm.sort("blend_key", "seq").rows(named=True):
        kg = r["weight"]
        if kg is None or kg < 0.05:
            continue
        out.setdefault(r["blend_key"], []).append({
            "b": r["batch_id"] or "〈无批号〉", "kg": round(float(kg), 1),
            "r": round(r["ratio"], 4) if r["ratio"] is not None else None,
            **{p: (round(float(r[p]), 2) if r[p] is not None else None) for p in SPECS},
        })
    for v in out.values():
        v.sort(key=lambda x: -x["kg"])
    return out


def _avail_mask(pool: pl.DataFrame, orders: list[dict], month, mode: str) -> np.ndarray:
    """strict 档的可用性矩阵 mask[i,j]。pool 档全 1。"""
    I, J = pool.height, len(orders)
    if mode != "strict" or month is None:
        return np.ones((I, J), bool)
    lot = pool["lot_ym"].to_numpy()          # 2501 = 2025年1月
    first = pool["first_day"].to_numpy()
    cur_ym = 2500 + month
    m = np.zeros((I, J), bool)
    for j, o in enumerate(orders):
        day = o["day"] if o["day"] is not None else 31
        instock = np.array([(lv is not None and not np.isnan(float(lv)) and float(lv) < cur_ym)
                            for lv in lot])
        proven = np.array([(fv is not None and not np.isnan(float(fv)) and float(fv) <= day)
                           for fv in first])
        m[:, j] = instock | proven
    return m


# ---------------------------------------------------------------- 求解


def _solve_month(pool, orders, mask, time_limit=90.0):
    """MILP：主目标 = 不达标吨位最小；次目标 = 相对缺口最小。"""
    I, J, P = pool.height, len(orders), len(SPECS)
    if I == 0 or J == 0:
        return None
    A = pool["avail"].to_numpy().astype(float)
    V = {p: pool[p].to_numpy().astype(float) for p in SPECS}
    W = np.array([o["W"] for o in orders], float)

    nx, ns, nz = I * J, J * P, J
    N = nx + ns + nz
    xi = lambda i, j: i * J + j          # noqa: E731
    si = lambda j, p: nx + j * P + p     # noqa: E731
    zi = lambda j: nx + ns + j           # noqa: E731

    rows, cols, vals, lo, hi = [], [], [], [], []
    r = 0

    def add(entries, l, h):
        nonlocal r
        for cix, v in entries:
            rows.append(r); cols.append(cix); vals.append(v)
        lo.append(l); hi.append(h); r += 1

    for j in range(J):                                    # 每张单拿满自己的吨数
        add([(xi(i, j), 1.0) for i in range(I) if mask[i, j]], W[j], W[j])
    for i in range(I):                                    # 每批料不能超发
        add([(xi(i, j), 1.0) for j in range(J) if mask[i, j]], -np.inf, A[i])
    for j, o in enumerate(orders):                        # 指标下限 + 缺口松弛
        for p, name in enumerate(SPECS):
            add([(xi(i, j), V[name][i]) for i in range(I) if mask[i, j]]
                + [(si(j, p), W[j])], o["req"][name] * W[j], np.inf)
    for j in range(J):                                    # 有缺口 -> z=1
        for p, name in enumerate(SPECS):
            add([(si(j, p), 1.0), (zi(j), -BIGM[name])], -np.inf, 0.0)

    Amat = coo_matrix((vals, (rows, cols)), shape=(r, N)).tocsr()

    obj = np.zeros(N)
    for j, o in enumerate(orders):
        obj[zi(j)] = o["W"]                                     # 主：不达标吨位
        for p, name in enumerate(SPECS):
            obj[si(j, p)] = 1e-3 * o["W"] / max(o["req"][name], 1e-9)   # 次：相对缺口

    ub = np.full(N, np.inf)
    for i in range(I):
        for j in range(J):
            ub[xi(i, j)] = A[i] if mask[i, j] else 0.0
    ub[nx + ns:] = 1.0
    integrality = np.zeros(N)
    integrality[nx + ns:] = 1

    t0 = time.time()
    res = milp(c=obj, constraints=LinearConstraint(Amat, lo, hi),
               integrality=integrality, bounds=Bounds(np.zeros(N), ub),
               options={"time_limit": time_limit, "mip_rel_gap": 1e-4})
    if not res.success or res.x is None:
        log.warning("月度求解失败：%s", res.message)
        return None
    return {"x": res.x[:nx].reshape(I, J), "secs": time.time() - t0,
            "status": res.message}


# ---------------------------------------------------------------- 评估


def _eval(pool, orders, X, human_mix: dict[str, list[dict]]) -> list[dict]:
    """由分配矩阵重算每张单的实际指标（不信求解器的中间量，全部复算），并带出逐批配方。"""
    V = {p: pool[p].to_numpy().astype(float) for p in SPECS}
    bids = pool["bid"].to_list()
    avail = pool["avail"].to_numpy().astype(float)
    out = []
    for j, o in enumerate(orders):
        col = X[:, j]
        w = float(col.sum())
        got = {p: float((col * V[p]).sum() / w) if w > 0 else None for p in SPECS}

        # 算法配方：按投料量降序；<0.05kg 是求解的数值毛刺，不算一批料
        mix = []
        for i in np.argsort(-col):
            kg = float(col[i])
            if kg < 0.05:
                break
            mix.append({"b": bids[i], "kg": round(kg, 1),
                        "r": round(kg / w, 4) if w else None,
                        "cap": round(float(avail[i]), 1),
                        **{p: (round(float(V[p][i]), 2)) for p in SPECS}})
        fails = [SPEC_LABEL[p] for p in SPECS if got[p] is not None
                 and got[p] < o["req"][p] - 1e-6]
        hfails = [SPEC_LABEL[p] for p in SPECS
                  if o["human"][p] is not None and o["human"][p] < o["req"][p] - 1e-6]
        out.append({
            "key": o["key"], "id": o["id"], "grade": o["grade"],
            "grade_src": o["grade_src"], "req_src": o["req_src"],
            "day": o["day"], "W": round(o["W"], 1), "n_comp": o["n_comp"],
            "req": {p: round(o["req"][p], 2) for p in SPECS},
            "human": {p: (round(o["human"][p], 2) if o["human"][p] is not None else None)
                      for p in SPECS},
            "algo": {p: (round(got[p], 2) if got[p] is not None else None) for p in SPECS},
            "human_ok": not hfails, "algo_ok": not fails,
            "human_fail": hfails, "algo_fail": fails,
            "algo_batches": len(mix),
            "human_moisture": o["human_moisture"],
            "human_mix": human_mix.get(o["key"], []),
            "algo_mix": mix,
        })
    return out


def _agg(rows: list[dict]) -> dict:
    """汇总。欠标/过剩以 bloom·吨 计（月内总冻力守恒，两者同增同减）。"""
    def side(kind, sign):
        s = 0.0
        for r in rows:
            g, q = r[kind]["bloom"], r["req"]["bloom"]
            if g is None:
                continue
            d = (g - q) * sign
            if d > 0:
                s += d * r["W"]
        return s / 1000.0

    tw = sum(r["W"] for r in rows) or 1.0
    return {
        "单数": len(rows),
        "吨位": round(tw / 1000, 1),
        "人工达标单": sum(r["human_ok"] for r in rows),
        "算法达标单": sum(r["algo_ok"] for r in rows),
        "人工达标吨": round(sum(r["W"] for r in rows if r["human_ok"]) / 1000, 1),
        "算法达标吨": round(sum(r["W"] for r in rows if r["algo_ok"]) / 1000, 1),
        "人工欠标": round(side("human", -1), 1),
        "算法欠标": round(side("algo", -1), 1),
        "人工过剩": round(side("human", 1), 1),
        "算法过剩": round(side("algo", 1), 1),
    }


def _money(bloom_ton: float) -> float:
    """把「过剩 bloom·吨」按定价曲线的平均斜率折成金额。价格是占位值。"""
    cur = pricing.load()
    pts = cur["points"]
    lo, hi = pts[0], pts[-1]
    slope = (hi["price"] - lo["price"]) / max(hi["bloom"] - lo["bloom"], 1e-9)  # 元/kg per bloom
    return bloom_ton * 1000 * slope


# ---------------------------------------------------------------- 入口


def run(mode: str = "pool", time_limit: float = 90.0) -> dict:
    b, c = _load()
    months = [m for m in sorted(
        x for x in c["blend_month"].unique().to_list() if x is not None)]
    months.append(None)   # 无法推出日期的一组单独求解

    all_rows, per_month, t0 = [], [], time.time()
    for m in months:
        pool, orders = _month_problem(b, c, m)
        if not orders or pool.height == 0:
            continue
        mask = _avail_mask(pool, orders, m, mode)
        sol = _solve_month(pool, orders, mask, time_limit)
        if sol is None:
            log.warning("%s 月求解失败，跳过", m)
            continue
        rows = _eval(pool, orders, sol["x"], _human_mix(c, m))
        all_rows.extend(rows)
        per_month.append({
            "月": m if m is not None else "未定月",
            "料池批次": pool.height,
            "料池吨": round(float(pool["avail"].sum()) / 1000, 1),
            "求解秒": round(sol["secs"], 1),
            **_agg(rows),
        })

    total = _agg(all_rows)
    std_rows = [r for r in all_rows if r["req_src"] == "客户标准"]
    label_rows = [r for r in std_rows if r["grade_src"] == "表内标注"]

    by_grade = {}
    for r in std_rows:
        by_grade.setdefault(r["grade"], []).append(r)
    grades = [{"规格": g, **_agg(v)} for g, v in sorted(by_grade.items())]

    saved = total["人工过剩"] - total["算法过剩"]
    return {
        "mode": mode,
        "生成耗时秒": round(time.time() - t0, 1),
        "总计": total,
        "仅客户标准单": _agg(std_rows),
        "仅表内标注单": _agg(label_rows),
        "按月": per_month,
        "按规格": grades,
        "明细": all_rows,
        "省下过剩bloom吨": round(saved, 1),
        "折合金额": round(_money(saved), 0),
        "币种": pricing.load()["currency"],
        "口径说明": {
            "pool": "全月统筹：料池=当月消耗的全部批次，算法可动用月内任何时点入库的料（理论上限）",
            "strict": "严格时序：批次须在月初已在库，或人工已在更早日期用过它（掐掉预知未来的优势）",
        }[mode],
    }


def get(mode: str = "pool", refresh: bool = False) -> dict:
    global _cache
    with _lock:
        if _cache is None:
            _cache = {}
        if mode in _cache and not refresh:
            return _cache[mode]
        if not refresh and CACHE.exists():
            try:
                import json
                disk = json.loads(CACHE.read_text(encoding="utf-8"))
                _cache.update(disk)
                if mode in _cache:
                    return _cache[mode]
            except Exception:
                log.warning("回溯缓存损坏，重算", exc_info=True)
        _cache[mode] = run(mode)
        try:
            import json
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            log.warning("回溯缓存写入失败", exc_info=True)
        return _cache[mode]


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = {}
    for mode in ("pool", "strict"):
        print(f"\n{'='*70}\n求解口径：{mode}\n{'='*70}")
        rep = run(mode)
        out[mode] = rep
        t = rep["总计"]
        print(f"耗时 {rep['生成耗时秒']}s | {t['单数']} 单 / {t['吨位']} 吨")
        print(f"  达标单数  人工 {t['人工达标单']:3d}  ->  算法 {t['算法达标单']:3d}"
              f"   (+{t['算法达标单']-t['人工达标单']})")
        print(f"  达标吨位  人工 {t['人工达标吨']:7.1f}  ->  算法 {t['算法达标吨']:7.1f}")
        print(f"  欠标量    人工 {t['人工欠标']:7.1f}  ->  算法 {t['算法欠标']:7.1f}  bloom·吨")
        print(f"  过剩量    人工 {t['人工过剩']:7.1f}  ->  算法 {t['算法过剩']:7.1f}  bloom·吨")
        print(f"  => 省下过剩 {rep['省下过剩bloom吨']} bloom·吨, "
              f"按占位价折合 {rep['折合金额']:,.0f} {rep['币种'].split('/')[0]}")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"\n已写入缓存 {CACHE}")
    sys.exit(0)
