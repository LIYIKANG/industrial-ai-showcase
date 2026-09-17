"""FastAPI 应用入口：REST 接口 + 静态前端托管。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import backtest, compare, config, data, pricing, solver, store
from .models import CompareRequest, ParamsRequest, PricingRequest, SolveRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("blending")

app = FastAPI(
    title="明胶配料求解系统",
    version="1.0.0",
    description="按客户规格从半成品库存中求解配料方案",
)


# ---------------------------------------------------------------- 元数据


@app.middleware("http")
async def refresh_demo_assets(request, call_next):
    response = await call_next(request)
    if request.url.path == '/' or request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


@app.get("/api/health")
def health():
    try:
        df = data.get_inventory()
        return {"ok": True, "批次数": df.height, "源文件": str(config.SOURCE_XLSX)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.get("/api/params")
def get_params():
    """参数定义 + 当前上下限 + 求解设置。前端三个页面都依赖它。"""
    cfg = store.load()
    return {
        "params": config.PARAMS,
        "limits": cfg["limits"],
        "settings": cfg["settings"],
        "grades": list(config.GRADE_ORDER),
        "targetable": config.TARGETABLE_KEYS,
        "default_tol": config.DEFAULT_TOL,
        "default_dir": config.DEFAULT_DIR,
        "directions": config.DIRECTIONS,
        "material_prefs": config.MATERIAL_PREFS,
        "target_col": config.TARGET_COL,
        "weight_col": config.WEIGHT_COL,
        "id_col": config.ID_COL,
    }


@app.put("/api/params")
def put_params(req: ParamsRequest):
    try:
        limits = (
            {k: v.model_dump(exclude_unset=True) for k, v in req.limits.items()}
            if req.limits
            else None
        )
        cfg = store.save(limits=limits, settings=req.settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, **cfg}


@app.post("/api/params/reset")
def reset_params():
    return {"ok": True, **store.reset()}


# ---------------------------------------------------------------- 库存


@app.get("/api/inventory")
def get_inventory(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    sort_by: str | None = None,
    desc: bool = False,
    grade: str | None = None,
    keyword: str | None = None,
    bloom_min: float | None = None,
    bloom_max: float | None = None,
    weight_min: float | None = None,
    weight_max: float | None = None,
):
    filters: dict = {}
    if bloom_min is not None or bloom_max is not None:
        filters[config.TARGET_COL] = (bloom_min, bloom_max)
    if weight_min is not None or weight_max is not None:
        filters[config.WEIGHT_COL] = (weight_min, weight_max)
    try:
        return data.query(
            page=page,
            size=size,
            sort_by=sort_by,
            desc=desc,
            filters=filters,
            grade=grade,
            keyword=keyword,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/inventory/stats")
def inventory_stats():
    try:
        return data.stats()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/dashboard")
def dashboard():
    """数据看板的全部聚合数据。界线取自当前保存的参数设定。"""
    st = store.load()["settings"]
    return data.dashboard(
        float(st.get("high_bloom_from") or 275.0),
        float(st.get("low_bloom_to") or 120.0),
    )


@app.get("/api/inventory/bloom-bands")
def bloom_bands(high_from: float = 275.0, low_to: float = 120.0):
    """按给定界线统计高/中/低冻力料的批次数与吨位，供参数设定页即时预览。"""
    if low_to >= high_from:
        raise HTTPException(status_code=400, detail="低冻力料上界必须小于高冻力料下界")
    return data.bloom_bands(high_from, low_to)


@app.post("/api/inventory/reload")
def reload_inventory():
    """源 Excel 更新后重新导入，绕过 parquet 缓存。"""
    df = data.get_inventory(refresh=True)
    return {"ok": True, "批次数": df.height}


# ---------------------------------------------------------------- 求解


@app.post("/api/blend")
def blend(req: SolveRequest):
    cfg = store.load()
    # 冻力留空时用全局默认容差，其余指标用各自在 config.DEFAULT_TOL 里的值
    # （模型层已填好，这里只覆盖冻力这一项）
    bloom_tol = float(cfg["settings"].get("bloom_tolerance") or 5.0)

    orders = []
    for n, o in enumerate(req.orders, 1):
        targets = {}
        for key, t in o.targets.items():
            tol = t.tolerance
            if key == config.TARGET_COL and o.tolerance is None and t.tolerance is None:
                tol = bloom_tol
            targets[key] = (float(t.value), float(tol), t.direction)
        orders.append(
            {
                "name": o.name or f"订单{n}",
                "weight": o.weight,
                "targets": targets,
                "material_pref": o.material_pref,
            }
        )

    names = [o["name"] for o in orders]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="单号重复，请使用不同的单号")

    try:
        return solver.solve(orders, settings=req.settings)
    except solver.Infeasible as e:
        return JSONResponse(
            {"status": "INFEASIBLE", "reason": e.reason, "detail": e.detail},
            status_code=200,
        )
    except Exception as e:
        log.exception("求解异常")
        raise HTTPException(status_code=500, detail=f"求解异常：{e}") from e


# ---------------------------------------------------------------- 定价


@app.get("/api/pricing")
def get_pricing():
    return {**pricing.load(), "placeholder": True,
            "note": "默认价格为占位值，绝对金额仅供相对比较，外发前请换成实际价格"}


@app.put("/api/pricing")
def put_pricing(req: PricingRequest):
    try:
        return {"ok": True, **pricing.save(
            [p.model_dump() for p in req.points], req.currency)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/pricing/reset")
def reset_pricing():
    return {"ok": True, **pricing.reset()}


# ---------------------------------------------------------------- 方案对比


@app.get("/api/compare/methods")
def compare_methods():
    """可参赛的方案清单。对照组单独标出。"""
    return {
        "baseline": {"key": config.BASELINE_KEY, "label": config.BASELINE_LABEL,
                     "desc": "按冻力接近度依次取料凑量，加权平均校验 —— 模拟人工试算"},
        "objectives": [
            {"key": "min_batch", "label": "批次最少", "desc": "省投料、清洗、转运工时"},
            {"key": "min_cost", "label": "料本最低", "desc": "按定价曲线最小化取用成本"},
            {"key": "min_redundancy", "label": "冗余最小", "desc": "交付指标尽量贴着客户要求，不白送品质"},
            {"key": "max_digest", "label": "最大消化难用料", "desc": "优先吃掉两端难出货的料"},
        ],
    }


@app.post("/api/compare")
def compare_api(req: CompareRequest):
    cfg = store.load()
    bloom_tol = float(cfg["settings"].get("bloom_tolerance") or 5.0)

    orders = []
    for n, o in enumerate(req.orders, 1):
        targets = {}
        for key, t in o.targets.items():
            tol = t.tolerance
            if key == config.TARGET_COL and o.tolerance is None and t.tolerance is None:
                tol = bloom_tol
            targets[key] = (float(t.value), float(tol), t.direction)
        orders.append({"name": o.name or f"订单{n}", "weight": o.weight,
                       "targets": targets, "material_pref": o.material_pref})

    names = [o["name"] for o in orders]
    if len(set(names)) != len(names):
        raise HTTPException(status_code=400, detail="单号重复，请使用不同的单号")

    try:
        return compare.run(orders, req.methods, settings=req.settings,
                           with_pareto=req.with_pareto)
    except solver.Infeasible as e:
        return JSONResponse({"status": "INFEASIBLE", "reason": e.reason,
                             "detail": e.detail}, status_code=200)
    except Exception as e:
        log.exception("对比异常")
        raise HTTPException(status_code=500, detail=f"对比异常：{e}") from e


# ---------------------------------------------------------------- 历史回溯


@app.get("/api/backtest")
def backtest_api(
    mode: str = Query(default="pool", pattern="^(pool|strict)$"),
    refresh: bool = False,
):
    """2025 年人工实际配法 vs 算法重配。首次调用约 20 秒，之后走缓存。"""
    try:
        return backtest.get(mode, refresh=refresh)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        log.exception("回溯异常")
        raise HTTPException(status_code=500, detail=f"回溯异常：{e}") from e


# ---------------------------------------------------------------- 前端


app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")
