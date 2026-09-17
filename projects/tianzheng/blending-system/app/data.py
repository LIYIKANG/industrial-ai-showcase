"""库存数据加载与清洗。

源表有四个必须绕开的坑（详见 docs/03-算法与数据.md）：
    1. 表头在第 2 行 —— 第 1 行是合并的大标题 `AI测试模拟数据`
    2. 列名重复 —— 大肠发酵/沙门发酵/大肠疑似/沙门疑似 在 A~X 和 Y~AP 各出现一次
    3. 尾部假空行 —— A~X 全空，但辅助公式列对空行算出一串 0，只看核心列才能识别
    4. 混合类型 —— <10 / 2级 / 未检出 / TNTC，先整表按字符串读入再显式转换
"""

from __future__ import annotations

import logging
import threading
import warnings
from typing import Any

import polars as pl

from . import config

log = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: pl.DataFrame | None = None


# ---------------------------------------------------------------- 清洗工具


def _clean_name(name: Any, idx: int) -> str:
    """去掉表头里的换行与多余空格：'重量\\nkg' -> '重量kg'。"""
    s = "" if name is None else "".join(str(name).split())
    return s or f"col_{idx}"


def _dedup(names: list[str]) -> list[str]:
    """重名列的第 2 次出现加 __2 后缀，否则 DataFrame 建不出来。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}__{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


# ---------------------------------------------------------------- 读取


def load_from_excel(path=None, sheet=None) -> pl.DataFrame:
    """从 Excel 读取并清洗，返回核心指标表。"""
    path = path or config.SOURCE_XLSX
    sheet = sheet or config.SOURCE_SHEET
    if not path.exists():
        raise FileNotFoundError(f"找不到源数据文件：{path}")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        raw = pl.read_excel(
            path,
            sheet_name=sheet,
            engine="calamine",
            has_header=False,
            # 全部按字符串读入，保留 <10 / 未检出 / #REF! 的原始文本，
            # 交给下面的显式转换处理，避免类型推断把整列吃成 null
            read_options={"dtypes": "string"},
        )

    header = _dedup([_clean_name(v, i) for i, v in enumerate(raw.row(config.HEADER_ROW_INDEX))])
    df = raw.slice(config.HEADER_ROW_INDEX + 1).rename(dict(zip(raw.columns, header)))

    missing = [k for k in config.PARAM_KEYS if k not in df.columns]
    if missing:
        raise ValueError(f"源表缺少必需列：{missing}\n实际列名：{df.columns}")

    keep = [config.ID_COL] + config.PARAM_KEYS
    df = df.select([c for c in keep if c in df.columns])

    # 剔除空行：只看核心列。辅助公式列会对空行算出 0，一起看的话一行都删不掉。
    core_blank = pl.all_horizontal(
        pl.col(c).str.strip_chars().replace("", None).is_null() for c in config.PARAM_KEYS
    )
    df = df.filter(~core_blank)

    # 显式类型转换：strict=False 让转不了的值变 null 而不是中断
    df = df.with_columns(
        [pl.col(config.ID_COL).str.strip_chars().cast(pl.Int64, strict=False)]
        + [
            pl.col(c).str.strip_chars().cast(pl.Float64, strict=False)
            for c in config.NUMERIC_KEYS
        ]
        + [pl.col(config.GRADE_COL).str.strip_chars()]
    )

    # 水不溶物等级 -> 数值，便于比较与排序
    df = df.with_columns(
        pl.col(config.GRADE_COL)
        .replace_strict(config.GRADE_ORDER, default=None, return_dtype=pl.Int64)
        .alias("水不溶物等级")
    )

    # 可用批次：重量与冻力必须有值，否则无法参与配料
    df = df.filter(
        pl.col(config.WEIGHT_COL).is_not_null() & (pl.col(config.WEIGHT_COL) > 0)
    )

    log.info("已加载库存 %d 批，合计 %.1f kg", df.height, df[config.WEIGHT_COL].sum())
    return df


def get_inventory(refresh: bool = False) -> pl.DataFrame:
    """取库存表。首次调用读 Excel 并写 parquet 缓存，之后直接读缓存。"""
    global _cache
    with _lock:
        if _cache is not None and not refresh:
            return _cache

        if config.PARQUET_CACHE.exists() and not refresh:
            try:
                _cache = pl.read_parquet(config.PARQUET_CACHE)
                log.info("从缓存加载库存：%s", config.PARQUET_CACHE)
                return _cache
            except Exception:  # 缓存损坏就重建，不该让整个服务起不来
                log.warning("parquet 缓存读取失败，回退到 Excel", exc_info=True)

        _cache = load_from_excel()
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _cache.write_parquet(config.PARQUET_CACHE)
        return _cache


# ---------------------------------------------------------------- 查询


def stats() -> dict:
    """库存总览：批次数、总重量、各指标分布。"""
    df = get_inventory()
    out: dict[str, Any] = {
        "批次数": df.height,
        "总重量kg": round(float(df[config.WEIGHT_COL].sum()), 1),
        "指标": [],
    }
    for p in config.PARAMS:
        col = df[p["key"]]
        if p["role"] == "grade":
            vc = col.value_counts(sort=True)
            out["指标"].append(
                {
                    **p,
                    "type": "grade",
                    "分布": [
                        {"值": r[p["key"]], "数量": r["count"]}
                        for r in vc.iter_rows(named=True)
                    ],
                    "缺失": int(col.null_count()),
                }
            )
            continue
        s = col.drop_nulls()
        out["指标"].append(
            {
                **p,
                "type": "numeric",
                "最小": round(float(s.min()), 3) if s.len() else None,
                "p5": round(float(s.quantile(0.05)), 3) if s.len() else None,
                "中位": round(float(s.median()), 3) if s.len() else None,
                "p95": round(float(s.quantile(0.95)), 3) if s.len() else None,
                "最大": round(float(s.max()), 3) if s.len() else None,
                "均值": round(float(s.mean()), 3) if s.len() else None,
                "缺失": int(col.null_count()),
            }
        )
    return out


def bloom_bands(high_from: float, low_to: float) -> dict:
    """按「高冻力料 / 中间料 / 低冻力料」三段统计批次数与吨位。

    参数设定页用它给出即时反馈：界线定在哪，会圈住多少库存 ——
    光给两个输入框，用户没法判断 275 和 260 差在哪。
    """
    df = get_inventory()
    b = pl.col(config.TARGET_COL)
    total_w = float(df[config.WEIGHT_COL].sum()) or 1.0

    def seg(f):
        s = df.filter(f)
        w = float(s[config.WEIGHT_COL].sum())
        return {"批次": s.height, "吨位": round(w / 1000, 1), "占比": round(w / total_w * 100, 1)}

    q = df[config.TARGET_COL].drop_nulls()
    return {
        "高冻力料": seg(b >= high_from),
        "中间料": seg((b < high_from) & (b > low_to)),
        "低冻力料": seg(b <= low_to),
        "分位": {f"p{int(x * 100)}": round(float(q.quantile(x)), 1)
                 for x in (0.05, 0.15, 0.25, 0.5, 0.75, 0.85, 0.95)},
        "最小": round(float(q.min()), 1),
        "最大": round(float(q.max()), 1),
    }


def dashboard(high_from: float = 275.0, low_to: float = 120.0) -> dict:
    """数据看板的全部聚合，一次算完一次返回。

    核心是「可配产能曲线」：给定目标冻力 B（只能高不能低），最多能配出多少吨。
    这是全套数据里最能指导接单的一个指标 —— 比单看库存总量有用得多。
    """
    import numpy as np

    df = get_inventory()
    b = df[config.TARGET_COL].to_numpy().astype(float)
    w = df[config.WEIGHT_COL].to_numpy().astype(float)
    total_w = float(w.sum())

    # ---- 可配产能曲线 ----
    # 冻力 ≥B 的料全部用上会产生「冻力盈余」Σwᵢ(bᵢ−B)；这份盈余能带动多少
    # 低于 B 的料做压秤，取决于每批的亏欠 (B−bⱼ)。按亏欠从小到大装，
    # 装到盈余用完为止 —— 这就是加权平均恰好等于 B 时的最大总量。
    lo_b, hi_b = float(b.min()), float(b.max())
    grid = np.linspace(lo_b, hi_b, 40)
    curve = []
    for B in grid:
        hi = b >= B
        base = float(w[hi].sum())
        surplus = float((w[hi] * (b[hi] - B)).sum())
        lw, lb = w[~hi], b[~hi]
        add = 0.0
        if lw.size and surplus > 0:
            k = np.argsort(-lb)  # 亏欠最小的先装
            lw, lb = lw[k], lb[k]
            need = (B - lb) * lw
            cum = np.cumsum(need)
            n = int(np.searchsorted(cum, surplus))
            add = float(lw[:n].sum())
            if n < lw.size:
                left = surplus - (float(cum[n - 1]) if n else 0.0)
                add += left / max(B - lb[n], 1e-9)
        curve.append({"冻力": round(float(B), 1),
                      "高端料吨": round(base / 1000, 2),
                      "可带动吨": round(add / 1000, 2),
                      "最大可配吨": round((base + add) / 1000, 2)})

    # ---- 冻力分布直方图（按吨位，不是按批数：吨位才是能不能接单的实际约束）----
    bins = np.linspace(lo_b, hi_b, 29)
    idx = np.clip(np.digitize(b, bins) - 1, 0, len(bins) - 2)
    hist = []
    for i in range(len(bins) - 1):
        m = idx == i
        hist.append({"起": round(float(bins[i]), 1), "止": round(float(bins[i + 1]), 1),
                     "批次": int(m.sum()), "吨位": round(float(w[m].sum()) / 1000, 2)})

    # ---- 各指标相对规格上下限的余量 ----
    from . import store
    limits = store.load()["limits"]
    headroom = []
    for p in config.PARAMS:
        if p["role"] != "limit" or p["key"] == config.TARGET_COL:
            continue
        lim = limits.get(p["key"]) or {}
        if not lim.get("enabled"):
            continue
        s = df[p["key"]].drop_nulls()
        if s.len() == 0:
            continue
        lo, hi = lim.get("lo"), lim.get("hi")
        if lo is None or hi is None or hi <= lo:
            continue
        p5, p95 = float(s.quantile(0.05)), float(s.quantile(0.95))
        span = hi - lo
        down = (p5 - lo) / span * 100
        up = (hi - p95) / span * 100

        # 余量只看「真正会卡壳的那一侧」。灰分、二氧化硫这类指标的下限写 0 只是形式 ——
        # 它们物理上不可能更低，报「下方余量仅 9%」纯属误导。看方向：
        #   max（越低越好）只看上方，min（越高越好）只看下方，both 两侧都看。
        direction = p.get("default_dir") or "both"
        if direction == "max":
            tight, side = up, "上限"
        elif direction == "min":
            tight, side = down, "下限"
        else:
            tight, side = (down, "下限") if down <= up else (up, "上限")

        headroom.append({
            "参数": p["key"], "名称": p["label"], "单位": p["unit"],
            "方向": direction,
            "规格下限": lo, "规格上限": hi,
            "库存最小": round(float(s.min()), 3), "库存最大": round(float(s.max()), 3),
            "p5": round(p5, 3), "p50": round(float(s.median()), 3), "p95": round(p95, 3),
            "下方余量%": round(down, 1),
            "上方余量%": round(up, 1),
            "余量%": round(tight, 1),
            "瓶颈侧": side,
            "超限批次": int(((df[p["key"]] < lo) | (df[p["key"]] > hi)).sum()),
        })
    headroom.sort(key=lambda r: r["余量%"])

    # ---- 水不溶物等级构成 ----
    grades = []
    for g in config.GRADE_ORDER:
        s = df.filter(pl.col(config.GRADE_COL) == g)
        if s.height:
            grades.append({"等级": g, "批次": s.height,
                           "吨位": round(float(s[config.WEIGHT_COL].sum()) / 1000, 2)})

    # ---- 分段 + KPI ----
    bands = bloom_bands(high_from, low_to)
    q = df[config.TARGET_COL].drop_nulls()

    return {
        "kpi": {
            "总批次": df.height,
            "总吨位": round(total_w / 1000, 1),
            "冻力中位": round(float(q.median()), 1),
            "冻力范围": [round(lo_b, 1), round(hi_b, 1)],
            "平均批重kg": round(total_w / max(df.height, 1), 1),
        },
        "曲线": curve,
        "直方图": hist,
        "余量": headroom,
        "等级": grades,
        "分段": bands,
        "界线": {"高": high_from, "低": low_to},
    }


def query(
    page: int = 1,
    size: int = 50,
    sort_by: str | None = None,
    desc: bool = False,
    filters: dict[str, tuple[float | None, float | None]] | None = None,
    grade: str | None = None,
    keyword: str | None = None,
) -> dict:
    """分页 / 排序 / 区间筛选查询。"""
    df = get_inventory()

    if filters:
        for key, (lo, hi) in filters.items():
            if key not in df.columns:
                continue
            if lo is not None:
                df = df.filter(pl.col(key) >= lo)
            if hi is not None:
                df = df.filter(pl.col(key) <= hi)

    if grade:
        df = df.filter(pl.col(config.GRADE_COL) == grade)

    if keyword:
        kw = keyword.strip()
        if kw.isdigit():
            df = df.filter(pl.col(config.ID_COL) == int(kw))

    total = df.height

    if sort_by and sort_by in df.columns:
        df = df.sort(sort_by, descending=desc, nulls_last=True)
    else:
        df = df.sort(config.ID_COL, nulls_last=True)

    size = max(1, min(size, 500))
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    rows = df.slice((page - 1) * size, size)

    return {
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
        "rows": rows.to_dicts(),
        "columns": [config.ID_COL] + config.PARAM_KEYS,
    }
