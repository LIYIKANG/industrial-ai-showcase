"""贪心配料：对照组，模拟人工试算的做法。

老师傅的实际流程是：
    1. 按主指标（冻力）接近程度挑料
    2. 依次取料凑够订单量
    3. 算加权平均，校验是否达标
    4. 不达标就换掉最偏的那批，反复试

它不是优化 —— 没有目标函数，也不保证全局最优。作为对照组的价值在于：
它就是客户现在真实在做的事，对比结果才有说服力。

实测差异（见 docs/08）：
    · 简单单（冻力充足）：指标精度和 MILP 相当，但批次数明显更多
    · 多指标单：**根本收敛不了** —— 只盯冻力挑料，PH、粘度必然失控
"""

from __future__ import annotations

import time

import numpy as np
import polars as pl

from . import config, solver


def greedy(
    orders: list[dict],
    limits: dict | None = None,
    settings: dict | None = None,
    max_swaps: int = 400,
) -> dict:
    """贪心求解。接口与 solver.solve 对齐，方便对比页统一处理。"""
    from . import store

    cfg = store.load()
    limits = limits if limits is not None else cfg["limits"]
    st = {**cfg["settings"], **(settings or {})}
    min_take = float(st.get("min_take") or 0)
    whole_batch = bool(st.get("whole_batch", True))
    w_tol = (float(st.get("weight_tol_pct") or 0) / 100.0) if whole_batch else 0.0

    tgt_keys = solver._target_keys(orders)
    pool = solver.build_pool(limits, st.get("worst_grade"), tgt_keys)
    solver.precheck(pool, orders, limits, w_tol)

    cap_all = pool[config.WEIGHT_COL].to_numpy().astype(float)
    ids_all = pool[config.ID_COL].to_numpy()
    active = [k for k, v in limits.items() if v.get("enabled") and k in pool.columns]
    vals = {k: pool[k].to_numpy().astype(float)
            for k in dict.fromkeys([*tgt_keys, *active])}

    t0 = time.time()
    remain = cap_all.copy()          # 多张单共享库存，先到先得（人工也是这么排的）
    order_results = []
    all_used: set[int] = set()
    per_batch: dict[int, dict[int, float]] = {}

    for k, o in enumerate(orders):
        W = float(o["weight"])
        # 人工只盯主指标：有冻力目标就按冻力排，否则按第一项目标排
        main = config.TARGET_COL if config.TARGET_COL in o["targets"] else next(iter(o["targets"]))
        tgt = o["targets"][main][0]
        order = np.argsort(np.abs(vals[main] - tgt))

        take = np.zeros(len(cap_all))
        got = 0.0
        lo_w, hi_w = W * (1 - w_tol), W * (1 + w_tol)

        if whole_batch:
            # 整批取料：依次整批往里加，加到落进总量窗口就停。
            # 人工也是这么干的 —— 拿一桶算一桶，快到量了挑一桶合适大小的补齐。
            for i in order:
                if got >= lo_w:
                    break
                if remain[i] <= 0 or remain[i] < cap_all[i] - 1e-9:
                    continue           # 已被别的单动过的批，整批取不了
                if got + remain[i] > hi_w:
                    continue           # 加上就超窗口，跳过去找小一点的
                take[i] = remain[i]
                got += remain[i]
        else:
            for i in order:
                if got >= W - 1e-9:
                    break
                q = min(remain[i], W - got)
                if q <= 0:
                    continue
                # 人工也不会去称 0.3kg，同样受最小取用量约束
                if 0 < q < min_take and got + remain[i] < W:
                    continue
                take[i] = q
                got += q

        if got < lo_w - 1e-6:
            raise solver.Infeasible(
                f"订单「{o['name']}」贪心取料只凑到 {got:,.1f} kg，"
                f"不足下限 {lo_w:,.1f} kg"
                + ("（整批取料下凑不进总量窗口）" if whole_batch else "")
            )

        # 不达标就换料：把最偏离的一批换成能拉回来的料，反复试 —— 人工的实际动作
        for _ in range(max_swaps):
            tot = take.sum()
            bad = None
            for key, (val, tol, direction) in o["targets"].items():
                act = float((take * vals[key]).sum() / tot)
                lo, hi = solver.interval(val, tol, direction)
                if act < lo - 1e-9:
                    bad = (key, +1, act)   # 需要拉高
                    break
                if act > hi + 1e-9:
                    bad = (key, -1, act)   # 需要压低
                    break
            if bad is None:
                break

            key, need, _act = bad
            v = vals[key]
            used = np.flatnonzero(take > 1e-9)
            # 换掉往错误方向拖得最狠的那批
            drop = used[np.argmin(v[used] * need)]

            if whole_batch:
                # 整批换整批：踢掉一批，换一批能把指标拉回来、且总量仍落在窗口内的。
                # 拆批那套「移一点量过去」在整批模式下是违规动作。
                rest = tot - take[drop]
                cands = [i for i in order
                         if take[i] <= 1e-9
                         and remain[i] >= cap_all[i] - 1e-9
                         and (v[i] - v[drop]) * need > 0
                         and lo_w - 1e-6 <= rest + cap_all[i] <= hi_w + 1e-6]
                if not cands:
                    break
                add = cands[0]
                take[drop] = 0.0
                take[add] = cap_all[add]
                continue

            # 换成能往正确方向拉、且还有库存的料里最接近主指标的
            cands = [i for i in order
                     if remain[i] - take[i] > 1e-9 and (v[i] - v[drop]) * need > 0]
            if not cands:
                break
            add = cands[0]
            move = min(take[drop], remain[add] - take[add], max(W * 0.05, min_take or 1.0))
            if move <= 1e-9:
                break
            take[drop] -= move
            take[add] += move

        used = np.flatnonzero(take > 1e-6)
        for i in used:
            remain[i] -= take[i]
            all_used.add(int(i))
            per_batch.setdefault(int(i), {})[k] = float(take[i])

        tot = float(take.sum())
        show_cols = list(dict.fromkeys([*o["targets"], *active]))
        picks = [{
            "数据编号": int(ids_all[i]),
            "取用kg": round(float(take[i]), 2),
            "库存kg": round(float(cap_all[i]), 1),
            "占比": round(float(take[i]) / tot * 100, 2),
            **{c: round(float(vals[c][i]), 3) for c in show_cols},
        } for i in sorted(used, key=lambda j: -take[j])]

        achieved = []
        for key in show_cols:
            act = float((take * vals[key]).sum() / tot)
            if key in o["targets"]:
                val, tol, direction = o["targets"][key]
                want_lo, want_hi = solver.interval(val, tol, direction)
                used_tol = (max(0.0, act - val) if direction == "min"
                            else max(0.0, val - act) if direction == "max"
                            else abs(act - val))
                achieved.append({
                    "参数": key, "类型": "target", "方向": direction,
                    "目标": val, "容差": tol, "实际": round(act, 4),
                    "偏差": round(act - val, 4),
                    "下限": round(want_lo, 4), "上限": round(want_hi, 4),
                    "达标": want_lo - 1e-6 <= act <= want_hi + 1e-6,
                    "占容差": round(used_tol / tol, 3) if tol else 0.0,
                })
            else:
                lim = limits.get(key) or {}
                g_lo = lim.get("lo") if lim.get("enabled") else None
                g_hi = lim.get("hi") if lim.get("enabled") else None
                ok = (g_lo is None or act >= g_lo - 1e-6) and (g_hi is None or act <= g_hi + 1e-6)
                achieved.append({
                    "参数": key, "类型": "limit", "方向": None,
                    "目标": None, "容差": None, "实际": round(act, 4),
                    "偏差": None, "下限": g_lo, "上限": g_hi,
                    "达标": ok, "占容差": None,
                })

        order_results.append({
            "单号": o["name"], "取料偏好": "balanced",
            "需求量kg": o["weight"], "实配量kg": round(tot, 2),
            "批次数": len(picks), "目标数": len(o["targets"]),
            "全部达标": all(a["达标"] for a in achieved),
            "用料": picks, "达成": achieved,
        })

    shared = [{
        "数据编号": int(ids_all[i]),
        "库存kg": round(float(cap_all[i]), 1),
        "被订单取用": [orders[k]["name"] for k in ks],
        "合计取用kg": round(sum(ks.values()), 2),
    } for i, ks in per_batch.items() if len(ks) > 1]

    total = sum(o["实配量kg"] for o in order_results)
    return {
        "status": "OK",
        "objective": config.BASELINE_KEY,
        "warnings": ([] if all(o["全部达标"] for o in order_results)
                     else ["贪心只按主指标挑料，其余指标失控 —— 人工此时需要反复试算"]),
        "耗时秒": round(time.time() - t0, 4),
        "候选池": pool.height,
        "参与候选": pool.height,
        "订单数": len(orders),
        "总批次数": len(all_used),
        "总配料量kg": round(total, 2),
        "全部达标": all(o["全部达标"] for o in order_results),
        "共用批次": shared,
        "订单": order_results,
    }


def pool_snapshot(limits: dict, worst_grade: str | None, tgt_keys: list[str]) -> pl.DataFrame:
    """对比页要算「配料前后库存变化」，需要和求解用的是同一个候选池。"""
    return solver.build_pool(limits, worst_grade, tgt_keys)
