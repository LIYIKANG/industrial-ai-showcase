"""全局配置：路径、参数定义、默认上下限。

参数角色（role）决定它在求解中的作用：
    capacity : 库存量 / 订单量，不是化验指标
    target   : 用户逐单输入的目标值（当前只有冻力）
    limit    : 混配后的加权平均须落在 [下限, 上限] 区间内
    grade    : 文本等级，作为候选批次的硬过滤条件（不参与加权平均）
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------- 路径

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

DATA_DIR = BASE_DIR / "data"
WEB_DIR = BASE_DIR / "web"

# 源数据：默认取上级目录的 Excel，可用环境变量 BLEND_SOURCE_XLSX 覆盖
SOURCE_XLSX = PROJECT_ROOT / "副本AI 测试模拟数据-20260805(1).xlsx"
SOURCE_SHEET = "Sheet1"
HEADER_ROW_INDEX = 1  # 0-based：Excel 第 2 行才是表头，第 1 行是合并的大标题

PARQUET_CACHE = DATA_DIR / "inventory.parquet"
LIMITS_FILE = DATA_DIR / "limits.json"

# ---------------------------------------------------------------- 服务

HOST = "127.0.0.1"
PORT = 8848

# ---------------------------------------------------------------- 参数定义

WEIGHT_COL = "重量kg"
TARGET_COL = "冻力Bloomg"  # 默认目标指标：新建订单时预先勾选的那一项
GRADE_COL = "水不溶物（个）"
ID_COL = "数据编号"

# default_tol : 该指标作为「目标值」时的默认容差 ±，按各指标的量纲和车间可控精度设定
# default_dir : 默认的约束方向 —— 客户规格单上「≥」「≤」的那个符号
#     "min"  只能高不能低，结果落在 [目标, 目标+容差]。冻力、透过率是这类：低了就不合格
#     "max"  只能低不能高，结果落在 [目标−容差, 目标]。水分、灰分、二氧化硫是这类：超标不合格
#     "both" 双向，结果落在 [目标−容差, 目标+容差]。PH、粘度这类两头都不能偏
PARAMS: list[dict] = [
    {"key": "重量kg",         "label": "重量",       "unit": "kg",      "role": "capacity", "decimals": 1, "default_tol": None, "default_dir": None},
    {"key": "冻力Bloomg",     "label": "冻力",       "unit": "Bloom g", "role": "limit",    "decimals": 1, "default_tol": 5.0,  "default_dir": "min"},
    {"key": "水分%",          "label": "水分",       "unit": "%",       "role": "limit",    "decimals": 2, "default_tol": 0.3,  "default_dir": "max"},
    {"key": "灰分%",          "label": "灰分",       "unit": "%",       "role": "limit",    "decimals": 3, "default_tol": 0.05, "default_dir": "max"},
    {"key": "PH值",           "label": "PH值",       "unit": "",        "role": "limit",    "decimals": 2, "default_tol": 0.2,  "default_dir": "both"},
    {"key": "水不溶物（个）",  "label": "水不溶物",   "unit": "级",      "role": "grade",    "decimals": 0, "default_tol": None, "default_dir": None},
    {"key": "勃氏粘度mPa/s",  "label": "勃氏粘度",   "unit": "mPa/s",   "role": "limit",    "decimals": 2, "default_tol": 0.3,  "default_dir": "both"},
    {"key": "粘度下降%",      "label": "粘度下降",   "unit": "%",       "role": "limit",    "decimals": 2, "default_tol": 0.3,  "default_dir": "max"},
    {"key": "透过率450%",     "label": "透过率450",  "unit": "%",       "role": "limit",    "decimals": 1, "default_tol": 2.0,  "default_dir": "min"},
    {"key": "透过率620%",     "label": "透过率620",  "unit": "%",       "role": "limit",    "decimals": 1, "default_tol": 1.0,  "default_dir": "min"},
    {"key": "电导率us/cm",    "label": "电导率",     "unit": "us/cm",   "role": "limit",    "decimals": 1, "default_tol": 10.0, "default_dir": "max"},
    {"key": "二氧化硫mg/kg",  "label": "二氧化硫",   "unit": "mg/kg",   "role": "limit",    "decimals": 2, "default_tol": 1.0,  "default_dir": "max"},
]

DIRECTIONS = {
    "min":  "只能高不能低",
    "both": "双向容差",
    "max":  "只能低不能高",
}

# 取料偏好：在同样达标的方案里，优先动用哪一端的库存
MATERIAL_PREFS = {
    "balanced":  "不偏好（批次最少）",
    "save_high": "保留高冻力料",
    "save_low":  "保留低冻力料",
}

PARAM_KEYS = [p["key"] for p in PARAMS]
LIMIT_KEYS = [p["key"] for p in PARAMS if p["role"] == "limit"]
NUMERIC_KEYS = [p["key"] for p in PARAMS if p["role"] in ("capacity", "limit")]

# 可作为逐单目标值的指标。除重量（那是订单量）和水不溶物（文本等级，加权平均没有
# 物理意义）之外，其余指标都能指定目标 —— 客户规格单上写什么就填什么。
TARGETABLE_KEYS = [p["key"] for p in PARAMS if p["role"] == "limit"]
DEFAULT_TOL = {p["key"]: p["default_tol"] for p in PARAMS if p["default_tol"] is not None}
DEFAULT_DIR = {p["key"]: p["default_dir"] for p in PARAMS if p["default_dir"]}

# 水不溶物等级 → 数值（数字越小越好）
GRADE_ORDER = {"1级": 1, "2级": 2, "3级": 3, "4级": 4}

# ---------------------------------------------------------------- 默认上下限
#
# 取值参考：食用明胶国标方向 + 本批库存的实际分布，保证开箱即用可解。
# 生产环境应由工艺部门在「参数设定」页按客户规格书调整。

DEFAULT_LIMITS: dict[str, dict] = {
    "水分%":         {"lo": 8.0,   "hi": 14.0,  "enabled": True},
    "灰分%":         {"lo": 0.0,   "hi": 2.0,   "enabled": True},
    "PH值":          {"lo": 4.5,   "hi": 6.5,   "enabled": True},
    "勃氏粘度mPa/s": {"lo": 1.5,   "hi": 7.0,   "enabled": True},
    "粘度下降%":     {"lo": 0.0,   "hi": 5.0,   "enabled": True},
    "透过率450%":    {"lo": 50.0,  "hi": 100.0, "enabled": True},
    "透过率620%":    {"lo": 70.0,  "hi": 100.0, "enabled": True},
    "电导率us/cm":   {"lo": 0.0,   "hi": 200.0, "enabled": True},
    "二氧化硫mg/kg": {"lo": 0.0,   "hi": 30.0,  "enabled": True},
}

# 求解默认值
DEFAULT_SETTINGS: dict = {
    "bloom_tolerance": 5.0,   # 冻力允许偏差 ±
    "min_take": 20.0,         # 单批最小取用量 kg，避免解出无法执行的碎量
    "max_batches": 0,         # 每单最多用几批，0 = 不限
    "candidates": 200,        # 每单候选批次数（预筛），越大越优但越慢
    # 求解时限。多订单争抢同一冻力段时，好解通常几秒内就找到了，剩下的时间
    # 都花在「证明它是最优」上 —— 所以打满时限返回的方案往往和跑满一样好。
    "time_limit": 15.0,
    "worst_grade": "3级",     # 可接受的最差水不溶物等级
    # 整批取料：一批料只要被用上就必须用完，不允许拆批。
    # 车间现实 —— 拆批会留下要重新化验、贴标、入库的零头。
    # 代价是总量无法精确命中订单量，必须配合 weight_tol_pct。
    "whole_batch": True,
    # 实配总量允许的偏差 ±%。整批取料下批重是 63~934kg 的任意实数，
    # 凑出恰好 3000kg 是子集和问题，实际几乎无解，所以必须留窗口。
    "weight_tol_pct": 5.0,
    # 「高冻力料」「低冻力料」的绝对界线，供配料页的取料偏好使用。
    # 默认取本批库存的 p90 / p15：≥275 有 388 批（172 吨），≤120 有约 580 批。
    # 设 0 / 9999 则退化为「以本单目标值为界」。
    "high_bloom_from": 275.0,
    "low_bloom_to": 120.0,
}

# ---------------------------------------------------------------- 定价曲线
#
# ⚠️ 以下价格是**占位值**，不是真实报价。方案对比里的成本只有相对意义，
#    绝对金额必须换成实际价格后才可外发。页面上可直接编辑。
#
# 形式是「(冻力, 单价) 断点 + 线性插值」而不是阶梯价：明胶价格随冻力连续变化，
# 阶梯价会让「配到 212 而客户只要 210」的冗余损失算成 0（同档内无差价），
# 而这恰恰是本次评估最想量化的东西。
DEFAULT_PRICING: dict = {
    "currency": "元/kg",
    "points": [
        {"bloom": 60.0, "price": 32.0},
        {"bloom": 120.0, "price": 38.0},
        {"bloom": 180.0, "price": 46.0},
        {"bloom": 240.0, "price": 62.0},
        {"bloom": 275.0, "price": 78.0},
        {"bloom": 315.0, "price": 92.0},
    ],
}

# ---------------------------------------------------------------- 目标函数

OBJECTIVES: dict[str, str] = {
    "min_batch": "批次最少",
    "min_cost": "料本最低",
    "min_redundancy": "冗余最小",
    "max_digest": "最大消化难用料",
}

# 对照组：模拟人工试算，不是优化
BASELINE_KEY = "greedy"
BASELINE_LABEL = "贪心配料（对照组）"

# 「难用料」的分位阈值。单独很难达标、必须靠混配才能出货的两端料，
# 消化它们视为高价值。没有库龄数据时用它替代真正的呆滞料口径。
HARD_LOW_Q = 0.15
HARD_HIGH_Q = 0.90
