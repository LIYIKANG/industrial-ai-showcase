"""
在解析结果上补充派生字段 + 数据体检
- sheet 名 -> 配料日期 (利用 sheet 在工作簿中的先后顺序消歧, 如 '101' 在 '918' 之后 => 10/01)
- 批号 -> 生产线 / 批次年份
- 入库编号 -> 入库年月
"""
import re
import polars as pl
import fastexcel

comp = pl.read_parquet("blend_components_2025.parquet")
blend = pl.read_parquet("blend_orders_2025.parquet")
order = {n: i for i, n in enumerate(fastexcel.read_excel("2025(1).xlsx").sheet_names)}


def sheet_date(name, prev):
    """从 sheet 名推日期; prev=(month,day) 用于消歧"""
    m = re.match(r"^(?:JK)?0*(\d{1,4})", name)
    if not m:
        return None
    d = m.group(1)
    cands = []
    if len(d) >= 5:            # JK 订单号(2510001) 不是日期
        return None
    if len(d) == 4:
        cands = [(int(d[:2]), int(d[2:]))]
    elif len(d) == 3:
        cands = [(int(d[0]), int(d[1:])), (int(d[:2]), int(d[2:]))]
    elif len(d) <= 2:
        cands = [(int(d), 1)]
    ok = [(mo, dy) for mo, dy in cands if 1 <= mo <= 12 and 1 <= dy <= 31]
    if not ok:
        return None
    if prev:                    # 选不早于上一张表、且最接近的解释
        fwd = [c for c in ok if c >= prev]
        return min(fwd or ok, key=lambda c: (c < prev, abs(c[0] - prev[0])))
    return ok[0]


dates, prev = {}, None
for n in sorted(order, key=order.get):
    d = sheet_date(n, prev)
    if d:
        prev = d
    dates[n] = d

date_df = pl.DataFrame(
    [{"sheet": n, "sheet_ord": order[n],
      "blend_month": dates[n][0] if dates[n] else None,
      "blend_day": dates[n][1] if dates[n] else None} for n in order],
    schema={"sheet": pl.Utf8, "sheet_ord": pl.Int64,
            "blend_month": pl.Int64, "blend_day": pl.Int64},
)

LINE = (pl.when(pl.col("batch_id").str.contains(r"^C0\dA")).then(pl.lit("A线"))
          .when(pl.col("batch_id").str.contains(r"^C0\dB")).then(pl.lit("B线"))
          .when(pl.col("batch_id").str.contains("LMB")).then(pl.lit("LMB(外购/旧料)"))
          .when(pl.col("batch_id").is_null()).then(pl.lit("无批号(计划/虚拟行)"))
          .otherwise(pl.lit("其他")))

comp = (comp.join(date_df, on="sheet", how="left")
        .with_columns(
            LINE.alias("line"),
            (pl.col("lot_no") // 10000).alias("lot_ym"),          # 2501 = 2025年1月
            pl.col("batch_id").str.extract(r"^C0\d[AB](\d{2})").cast(pl.Int64).alias("batch_yy"),
        )
        .with_columns(
            (2000 + pl.col("lot_ym") // 100).alias("lot_year"),
            (pl.col("lot_ym") % 100).alias("lot_month"),
        ))

blend = blend.join(date_df, on="sheet", how="left")
comp.write_parquet("blend_components_2025.parquet")
blend.write_parquet("blend_orders_2025.parquet")

pl.Config.set_tbl_rows(40)
pl.Config.set_tbl_cols(20)
pl.Config.set_fmt_str_lengths(30)

print("═" * 78)
print(f"配料单 blends = {blend.height}    投料明细 components = {comp.height}")
print("═" * 78)

print("\n【1】配料单规模")
print(blend.select(
    pl.col("calc_weight").min().alias("最小批量kg"),
    pl.col("calc_weight").median().alias("中位批量kg"),
    pl.col("calc_weight").max().alias("最大批量kg"),
    pl.col("calc_weight").sum().alias("全年总产出kg"),
    pl.col("n_comp").min().alias("最少投料批数"),
    pl.col("n_comp").median().alias("中位投料批数"),
    pl.col("n_comp").max().alias("最多投料批数"),
))

print("\n【2】成品指标分布 (加权平均值)")
print(blend.select("calc_bloom", "calc_viscosity", "calc_t450", "calc_t620")
      .describe().filter(pl.col("statistic").is_in(["min", "25%", "50%", "75%", "max"])))

print("\n【3】原料批次指标分布")
print(comp.filter(pl.col("weight") > 0)
      .select("weight", "bloom", "viscosity", "t450", "t620", "moisture", "so2")
      .describe().filter(pl.col("statistic").is_in(["count", "min", "50%", "max"])))

print("\n【4】质控口径: 第6列是水分还是硫")
print(blend.group_by("spec_axis").agg(pl.len().alias("配料单数"),
                                      pl.col("calc_bloom").mean().round(1).alias("平均冻力")))

print("\n【5】明示目标型号 target_grade 的配料单")
print(blend.filter(pl.col("target_grade").is_not_null())
      .group_by("target_grade")
      .agg(pl.len().alias("单数"),
           pl.col("calc_bloom").mean().round(1).alias("实配冻力均值"),
           pl.col("calc_bloom").min().round(1).alias("最低"),
           pl.col("calc_bloom").max().round(1).alias("最高"))
      .sort("target_grade"))

print("\n【6】按月产出")
print(blend.group_by("blend_month").agg(
    pl.len().alias("配料单数"),
    (pl.col("calc_weight").sum() / 1000).round(1).alias("产出吨"),
    pl.col("calc_bloom").mean().round(1).alias("平均冻力"),
).sort("blend_month"))

print("\n【7】原料来源")
print(comp.group_by("line").agg(pl.len().alias("投料次数"),
                                (pl.col("weight").sum() / 1000).round(1).alias("吨"))
      .sort("投料次数", descending=True))

print("\n【8】数据质量")
n_nobatch = comp.filter(pl.col("batch_id").is_null()).height
n_zero = comp.filter(pl.col("weight") == 0).height
dup = (comp.filter(pl.col("batch_id").is_not_null())
       .group_by("batch_id").agg(pl.len().alias("n"), pl.col("blend_key").unique().alias("用在"))
       .filter(pl.col("n") > 1).sort("n", descending=True))
print(f"  无批号的投料行(纯目标/计划行): {n_nobatch}")
print(f"  重量=0 的投料行:               {n_zero}")
print(f"  同一批号出现在多张配料单:       {dup.height} 个批号")
print(f"  缺水分且缺硫的投料行:           "
      f"{comp.filter(pl.col('moisture').is_null() & pl.col('so2').is_null()).height}")
print(f"  未能推出日期的 sheet:           {date_df.filter(pl.col('blend_month').is_null())['sheet'].to_list()}")

print("\n  同一批号被重复使用最多的:")
print(dup.select("batch_id", "n").head(8))

print("\n【9】时序自洽性校验: 配料日期 vs 原料入库年月")
chk = (comp.filter(pl.col("lot_ym").is_not_null() & pl.col("blend_month").is_not_null())
       .with_columns(
           (pl.when(pl.col("lot_year") == 2025).then(pl.col("lot_month")).otherwise(pl.col("lot_month") - 12))
           .alias("lot_m_rel"))
       .with_columns((pl.col("blend_month") - pl.col("lot_m_rel")).alias("料龄_月")))
print(f"  可校验行数: {chk.height}")
print(f"  入库晚于配料(不合逻辑)的行数: {chk.filter(pl.col('料龄_月') < 0).height}")
print(chk.select(pl.col("料龄_月").min().alias("最小"),
                 pl.col("料龄_月").median().alias("中位"),
                 pl.col("料龄_月").max().alias("最大")))

print("\n【10】样例: 一张完整配料单")
k = blend.filter(pl.col("sheet") == "0101")["blend_key"][0]
print(blend.filter(pl.col("blend_key") == k)
      .select("sheet", "blend_id", "n_comp", "calc_weight", "calc_bloom",
              "calc_viscosity", "calc_t450", "calc_t620", "calc_moisture"))
print(comp.filter(pl.col("blend_key") == k)
      .select("seq", "batch_id", "weight", "ratio", "bloom", "viscosity",
              "t450", "t620", "moisture", "lot_no", "line"))
