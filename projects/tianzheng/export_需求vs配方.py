"""
导出「客户需求 vs 实际配方」对照表
Sheet1 需求vs实际配方 : 每行=一条投料明细, 前置列为客户需求/实配结果/达标判定
Sheet2 客户需求标准    : 客户给的验收标准原表(本工作簿所有需求列的取值来源)

客户标准里「透过率 a-b」= T450≥a 且 T620≥b (双波长下限), 已用实配数据交叉验证。
"""
import polars as pl
import xlsxwriter

blend = pl.read_parquet("blend_orders_2025.parquet")
comp = pl.read_parquet("blend_components_2025.parquet")

# ── 客户验收标准 (客户提供) ────────────────────────────────
# 规格: (类型, 年需求吨, 冻力≥, 粘度≥, T450≥, T620≥, 水分< )  None = 该项无要求
STD = {
    120.0: ("食品", 40,  130.0, 3.5, 70.0, 90.0, None),
    140.0: ("食品", 500, 150.0, 3.6, 77.0, 93.0, 12.0),
    160.0: ("食品", 320, 160.0, 3.8, 77.0, 93.0, 12.0),
    200.0: ("胶囊", 410, 220.0, 4.8, 80.0, 90.0, 12.0),
    220.0: ("胶囊", 185, 240.0, 4.8, 85.0, 90.0, 12.0),
    240.0: ("食品", 360, 260.0, 4.8, 85.0, 90.0, 12.0),
}

# ── 1. 未标注规格的单子: 按已标注单据的实配冻力做最近邻推测 ──
known = (blend.filter(pl.col("target_grade").is_not_null())
         .with_columns(pl.col("target_grade").cast(pl.Float64).alias("tg")))
MAIN = [120.0, 140.0, 160.0, 180.0, 200.0, 220.0, 240.0]
cent = {g: known.filter(pl.col("tg") == g)["calc_bloom"].mean() for g in MAIN}


def guess(bloom):
    return None if bloom is None else min(MAIN, key=lambda g: abs(bloom - cent[g]))


blend = blend.with_columns(
    pl.when(pl.col("target_grade").is_not_null())
      .then(pl.col("target_grade").cast(pl.Float64))
      .otherwise(pl.col("calc_bloom").map_elements(guess, return_dtype=pl.Float64))
      .alias("规格"),
    pl.when(pl.col("target_grade").is_not_null())
      .then(pl.lit("表内标注")).otherwise(pl.lit("推测")).alias("规格来源"),
    pl.when(pl.col("blend_month").is_not_null())
      .then(pl.format("2025-{}-{}", pl.col("blend_month").cast(pl.Utf8).str.zfill(2),
                      pl.col("blend_day").cast(pl.Utf8).str.zfill(2)))
      .otherwise(pl.lit("")).alias("配料日期"),
)


# ── 2. 挂上客户标准 + 逐项达标判定 ─────────────────────────
def enrich(r):
    out = {"类型": None, "年需求吨": None, "需_冻力": None, "需_粘度": None,
           "需_T450": None, "需_T620": None, "需_水分": None,
           "冻力差": None, "判定": "无客户标准", "不达标项": None}
    s = STD.get(r["规格"])
    if s is None:
        return out
    ty, qty, bl, vi, t4, t6, mo = s
    out.update({"类型": ty, "年需求吨": qty, "需_冻力": bl, "需_粘度": vi,
                "需_T450": t4, "需_T620": t6, "需_水分": mo})
    fail = []
    if r["calc_bloom"] is not None:
        out["冻力差"] = r["calc_bloom"] - bl
        if r["calc_bloom"] < bl:
            fail.append("冻力")
    if r["calc_viscosity"] is not None and r["calc_viscosity"] < vi:
        fail.append("粘度")
    if r["calc_t450"] is not None and r["calc_t450"] < t4:
        fail.append("T450")
    if r["calc_t620"] is not None and r["calc_t620"] < t6:
        fail.append("T620")
    if mo is not None and r["calc_moisture"] is not None and r["calc_moisture"] >= mo:
        fail.append("水分")
    out["判定"] = "达标" if not fail else "不达标"
    out["不达标项"] = "、".join(fail) if fail else None
    return out


ext = pl.DataFrame([enrich(r) for r in blend.rows(named=True)],
                   schema={"类型": pl.Utf8, "年需求吨": pl.Int64, "需_冻力": pl.Float64,
                           "需_粘度": pl.Float64, "需_T450": pl.Float64,
                           "需_T620": pl.Float64, "需_水分": pl.Float64,
                           "冻力差": pl.Float64, "判定": pl.Utf8, "不达标项": pl.Utf8})
blend = pl.concat([blend, ext], how="horizontal")

# ── 3. 拼单页长表 ──────────────────────────────────────────
keep = ["blend_key", "blend_id", "配料日期", "sheet", "规格", "规格来源", "类型", "年需求吨",
        "需_冻力", "需_粘度", "需_T450", "需_T620", "需_水分",
        "calc_bloom", "calc_viscosity", "calc_t450", "calc_t620", "calc_moisture",
        "calc_so2", "calc_weight", "n_comp", "冻力差", "判定", "不达标项",
        "sheet_ord", "row", "col"]
flat = (comp.join(blend.select(keep), on="blend_key", how="inner")
        .sort(["sheet_ord", "row", "col", "seq"])
        .select(
            pl.col("blend_key").alias("_key"),
            pl.col("blend_id").fill_null("(原表未编号)").alias("配料单号"),
            "配料日期",
            pl.col("sheet").alias("原表页"),
            pl.col("规格").alias("需_规格"),
            pl.col("规格来源").alias("需_规格来源"),
            pl.col("类型").alias("需_类型"),
            pl.col("年需求吨").alias("需_年需求吨"),
            "需_冻力", "需_粘度", "需_T450", "需_T620", "需_水分",
            pl.col("calc_bloom").round(1).alias("配_冻力"),
            pl.col("冻力差").round(1).alias("配_冻力差"),
            pl.col("calc_viscosity").round(2).alias("配_粘度"),
            pl.col("calc_t450").round(1).alias("配_T450"),
            pl.col("calc_t620").round(1).alias("配_T620"),
            pl.col("calc_moisture").round(2).alias("配_水分"),
            pl.col("calc_so2").round(2).alias("配_硫"),
            pl.col("calc_weight").round(1).alias("配_总重kg"),
            pl.col("n_comp").alias("配_批数"),
            pl.col("判定").alias("判_结论"),
            pl.col("不达标项").alias("判_不达标项"),
            pl.col("seq").alias("方_序号"),
            pl.col("batch_id").alias("方_原料批号"),
            pl.col("line").alias("方_来源"),
            pl.col("weight").alias("方_投料kg"),
            pl.col("ratio").alias("方_配比"),
            pl.col("bloom").alias("方_冻力"),
            pl.col("viscosity").alias("方_粘度"),
            pl.col("t450").alias("方_T450"),
            pl.col("t620").alias("方_T620"),
            pl.col("moisture").alias("方_水分"),
            pl.col("so2").alias("方_硫"),
            pl.col("lot_no").alias("方_入库编号"),
        ))

ORDER_FIELDS = [c for c in flat.columns
                if c.startswith(("需_", "配_", "判_"))
                or c in ("配料单号", "配料日期", "原表页")]

# ── 4. 写 Excel ────────────────────────────────────────────
OUT = "客户需求vs实际配方_2025.xlsx"
wb = xlsxwriter.Workbook(OUT, {"nan_inf_to_errors": True})
ws = wb.add_worksheet("需求vs实际配方")

C_REQ, C_ACT, C_JDG, C_REC = "#1F4E79", "#7F3E00", "#7B1E22", "#375623"
base = {"font_name": "微软雅黑", "font_size": 9, "border": 1, "border_color": "#D9D9D9"}


def F(**kw):
    return wb.add_format({**base, **kw})


def pair(**kw):
    return (F(**kw), F(**{**kw, "bg_color": "#F2F7FB"}))


def hdr(c):
    return wb.add_format({"font_name": "微软雅黑", "font_size": 9, "bold": True,
                          "text_wrap": True, "align": "center", "valign": "vcenter",
                          "font_color": "white", "bg_color": c, "border": 1})


def band_fmt(c):
    return wb.add_format({"font_name": "微软雅黑", "font_size": 10, "bold": True,
                          "align": "center", "valign": "vcenter",
                          "font_color": "white", "bg_color": c, "border": 1})


f_txt = pair(align="left")
f_ctr = pair(align="center")
f_int = pair(num_format="0", align="center")
f_i0 = pair(num_format="#,##0", align="right")
f_d1 = pair(num_format="0.0", align="right")
f_d2 = pair(num_format="0.00", align="right")
f_pct = pair(num_format="0.0%", align="right")
f_req = pair(num_format="0.0", align="center", bold=True, font_color=C_REQ)
f_req0 = pair(num_format="0", align="center", bold=True, font_color=C_REQ)
f_act = pair(num_format="0.0", align="right", bold=True)

COLS = [
    ("配料单号",    "需求", f_ctr, 10, "配料单号"),
    ("配料日期",    "需求", f_ctr, 11, "配料日期"),
    ("原表页",      "需求", f_ctr, 7,  "原表页"),
    ("需_规格",     "需求", f_req0, 7, "规格"),
    ("需_规格来源", "需求", f_ctr, 9,  "规格来源"),
    ("需_类型",     "需求", f_ctr, 7,  "类型"),
    ("需_年需求吨", "需求", f_i0, 9,   "年需求量(吨)"),
    ("需_冻力",     "需求", f_req0, 8, "冻力 ≥"),
    ("需_粘度",     "需求", f_req, 8,  "粘度 ≥"),
    ("需_T450",     "需求", f_req0, 8, "T450 ≥"),
    ("需_T620",     "需求", f_req0, 8, "T620 ≥"),
    ("需_水分",     "需求", f_req, 8,  "水分 <"),
    ("配_冻力",     "实配", f_act, 8,  "冻力"),
    ("配_冻力差",   "实配", f_d1, 8,   "冻力差"),
    ("配_粘度",     "实配", f_d2, 8,   "粘度"),
    ("配_T450",     "实配", f_d1, 8,   "T450"),
    ("配_T620",     "实配", f_d1, 8,   "T620"),
    ("配_水分",     "实配", f_d2, 8,   "水分"),
    ("配_硫",       "实配", f_d2, 7,   "硫"),
    ("配_总重kg",   "实配", f_i0, 10,  "总重kg"),
    ("配_批数",     "实配", f_int, 6,  "批数"),
    ("判_结论",     "判定", f_ctr, 10, "结论"),
    ("判_不达标项", "判定", f_ctr, 14, "不达标项"),
    ("方_序号",     "配方", f_int, 5,  "序号"),
    ("方_原料批号", "配方", f_txt, 15, "原料批号"),
    ("方_来源",     "配方", f_ctr, 8,  "来源"),
    ("方_投料kg",   "配方", f_d1, 9,   "投料kg"),
    ("方_配比",     "配方", f_pct, 8,  "配比"),
    ("方_冻力",     "配方", f_d1, 8,   "冻力"),
    ("方_粘度",     "配方", f_d2, 8,   "粘度"),
    ("方_T450",     "配方", f_d1, 8,   "T450"),
    ("方_T620",     "配方", f_d1, 8,   "T620"),
    ("方_水分",     "配方", f_d2, 8,   "水分"),
    ("方_硫",       "配方", f_d2, 7,   "硫"),
    ("方_入库编号", "配方", f_int, 11, "入库编号"),
]
GC = {"需求": C_REQ, "实配": C_ACT, "判定": C_JDG, "配方": C_REC}
TITLE = {"需求": "客 户 需 求 (按客户验收标准)", "实配": "实 际 配 出 来 的 成 品",
         "判定": "达 标 判 定", "配方": "实 际 配 方 (逐批投料)"}

spans, i = [], 0
while i < len(COLS):
    g, j = COLS[i][1], i
    while j + 1 < len(COLS) and COLS[j + 1][1] == g:
        j += 1
    spans.append((g, i, j))
    i = j + 1
for g, a, z in spans:
    ws.merge_range(0, a, 0, z, TITLE[g], band_fmt(GC[g]))
for j, (_, g, _, w, disp) in enumerate(COLS):
    ws.write(1, j, disp, hdr(GC[g]))
    ws.set_column(j, j, w)
ws.freeze_panes(2, 4)
ws.set_row(0, 22)
ws.set_row(1, 30)

VERDICT = {"达标": pair(align="center", bold=True, font_color="#1E7B34"),
           "不达标": pair(align="center", bold=True, font_color="#C00000"),
           "无客户标准": pair(align="center", font_color="#808080")}

r_i, prev_key, shade = 2, None, 0
for r in flat.rows(named=True):
    first = r["_key"] != prev_key
    if first and prev_key is not None:
        shade ^= 1
    prev_key = r["_key"]
    for j, (name, g, fmts, _w, _d) in enumerate(COLS):
        v = r[name]
        if name in ORDER_FIELDS and not first:
            v = None
        fmt = VERDICT[v][shade] if (name == "判_结论" and v in VERDICT) else fmts[shade]
        if v is None:
            ws.write_blank(r_i, j, None, fmt)
        else:
            ws.write(r_i, j, v, fmt)
    r_i += 1

ws.autofilter(1, 0, r_i - 1, len(COLS) - 1)
names = [c[0] for c in COLS]
c_diff = names.index("配_冻力差")
ws.conditional_format(2, c_diff, r_i - 1, c_diff, {
    "type": "3_color_scale", "min_color": "#F8696B", "mid_color": "#FFEB84",
    "max_color": "#63BE7B", "min_type": "num", "min_value": -30,
    "mid_type": "num", "mid_value": 0, "max_type": "num", "max_value": 30})
c_ratio = names.index("方_配比")
ws.conditional_format(2, c_ratio, r_i - 1, c_ratio,
                      {"type": "data_bar", "bar_color": "#8EA9DB", "bar_solid": True})

# ── Sheet2: 客户需求标准 ───────────────────────────────────
w2 = wb.add_worksheet("客户需求标准")
head = ["规格", "类型", "年需求量(吨)", "冻力 ≥", "粘度 ≥", "透过率T450 ≥",
        "透过率T620 ≥", "水分 <", "2025实际配出(吨)", "达标单数", "总单数", "达标率"]
w2.merge_range(0, 0, 0, len(head) - 1,
               "客 户 验 收 标 准 (客户提供) 与 2025 年执行情况", band_fmt(C_REQ))
for j, h in enumerate(head):
    w2.write(1, j, h, hdr(C_REQ))
    w2.set_column(j, j, 14 if j in (2, 5, 6, 8, 11) else 11)
w2.set_row(0, 22)
w2.set_row(1, 30)

cell = F(align="center")
cellf = F(align="center", num_format="0.0")
celli = F(align="center", num_format="#,##0")
cellp = F(align="center", num_format="0.0%")
cellg = F(align="center", bold=True, font_color=C_REQ)
for i2, (g, (ty, qty, bl, vi, t4, t6, mo)) in enumerate(sorted(STD.items()), start=2):
    d = blend.filter(pl.col("规格") == g)
    n, ok = d.height, int((d["判定"] == "达标").sum())
    row = [(g, cellg), (ty, cell), (qty, celli), (bl, cell), (vi, cellf),
           (t4, cell), (t6, cell),
           (mo if mo is not None else "没要求", cellf if mo is not None else cell),
           (round(d["calc_weight"].sum() / 1000, 1), cellf), (ok, cell), (n, cell),
           (ok / n if n else None, cellp)]
    for j, (v, f) in enumerate(row):
        if v is None:
            w2.write_blank(i2, j, None, f)
        else:
            w2.write(i2, j, v, f)

note = wb.add_format({"font_name": "微软雅黑", "font_size": 9, "font_color": "#595959",
                      "text_wrap": True, "valign": "top"})
w2.merge_range(len(STD) + 3, 0, len(STD) + 8, len(head) - 1,
               "说明:\n"
               "1) 客户标准中的「透过率 a-b」经实配数据交叉验证, 含义为 T450≥a 且 T620≥b "
               "(双波长下限), 而非单一数值的区间。\n"
               "2) 「规格」在原始台账中记录于每个配料区块标签行的首格(如 A2 / J2 / A28); "
               "252 张配料单中 191 张有标注, 61 张未标注, 由实配冻力最近邻推测, 见「规格来源」列。\n"
               "3) 规格 80/100/150/180/250 客户未提供标准, 判定列标为「无客户标准」。\n"
               "4) 达标判定按配料单的加权平均值计算, 含推测规格的单据。\n"
               "5) 水分仅 82 张配料单有记录, 无记录的单据不参与水分判定。", note)

wb.close()

print(f"已导出 {OUT}")
print(f"  Sheet1 需求vs实际配方: {r_i-2} 行 x {len(COLS)} 列 / {blend.height} 张配料单")
print(f"  Sheet2 客户需求标准: {len(STD)} 个规格")
print(blend.group_by("判定").agg(pl.len().alias("配料单数"),
                                 (pl.col("calc_weight").sum() / 1000).round(1).alias("吨"))
      .sort("配料单数", descending=True))
