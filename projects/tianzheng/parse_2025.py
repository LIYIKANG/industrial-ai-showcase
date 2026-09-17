"""
解析 2025(1).xlsx —— 明胶配料(掺混)台账
--------------------------------------------------
每个 sheet = 一天(或几天)的配料记录，内含 1~6 个"配料单"区块。
区块结构:
    r0 : [单号, 总重量(=SUM), 加权冻力, 加权粘度, 加权T450, 加权T620, 加权水分/硫]
    r1 : [目标型号?, '重量','冻力','粘度','T450','T620','水分'|'H2O'|'硫'|'SO2']
    r2+: [原料批号, 重量, 冻力, 粘度, T450, T620, 水分/硫, 入库编号]
区块在网格上平铺: 列偏移 0/9/18..., 行偏移 0/26/50...
"""
import fastexcel
import polars as pl

SRC = "2025(1).xlsx"

PROP_MAP = {
    "重量": "weight", "冻力": "bloom", "粘度": "viscosity",
    "T450": "t450", "T620": "t620",
    "水分": "moisture", "H2O": "moisture",
    "硫": "so2", "SO2": "so2", "二氧化硫": "so2", "S2O": "so2",
}
NUM_PROPS = ["weight", "bloom", "viscosity", "t450", "t620", "moisture", "so2"]


def cell(g, r, c):
    if 0 <= r < len(g) and 0 <= c < len(g[r]):
        v = g[r][c]
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v
    return None


def fmt_id(v):
    """单号/型号可能是数字或文本, 统一成字符串 (220.0 -> '220')"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def as_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse():
    rd = fastexcel.read_excel(SRC)
    comps, blends, leftovers = [], [], []

    for sheet in rd.sheet_names:
        g = rd.load_sheet_by_name(sheet, header_row=None).to_polars().rows()
        if not g:
            continue
        used = set()  # (r,c) consumed cells

        # ---- 1. 定位所有区块锚点: 值为 '重量' 的单元格 ----
        anchors = sorted(
            (r, c)
            for r in range(len(g))
            for c in range(len(g[r]))
            if cell(g, r, c) == "重量"
        )
        anchor_rows = sorted({r for r, _ in anchors})

        for lab_r, w_c in anchors:
            id_c = w_c - 1
            hdr_r = lab_r - 1

            # ---- 2. 读属性标签 ----
            cols = {}  # prop -> column index
            c = w_c
            while True:
                lab = cell(g, lab_r, c)
                if lab is None or lab not in PROP_MAP:
                    break
                cols[PROP_MAP[lab]] = c
                used.add((lab_r, c))
                c += 1
            last_prop_c = c - 1

            # ---- 3. 表头行: 单号 + 汇总值 ----
            block_id = cell(g, hdr_r, id_c)
            block_id = None if block_id is None else fmt_id(block_id)
            used.add((hdr_r, id_c))
            stated = {}
            for p, pc in cols.items():
                stated[p] = as_num(cell(g, hdr_r, pc))
                used.add((hdr_r, pc))
            # 标签行首列偶尔写目标型号 (220/240/200/160)
            target = cell(g, lab_r, id_c)
            target = None if target is None else fmt_id(target)
            used.add((lab_r, id_c))

            # ---- 4. 明细行 ----
            # 下一个区块的"表头行"就是边界(不能把它当明细吃进来)
            stop = min([r - 1 for r in anchor_rows if r > lab_r] or [len(g)])
            rows = []
            gap = 0
            for r in range(lab_r + 1, stop):
                bid = cell(g, r, id_c)
                wt = as_num(cell(g, r, cols["weight"]))
                if bid is None and wt is None:
                    gap += 1
                    # 连续空行 >=3 视为区块结束(后面多半是另贴的备料清单)
                    if gap >= 3 and rows:
                        break
                    continue
                gap = 0
                # 备料清单行: [批号, 重量, 入库号] —— 第二个属性列落的是入库号而非冻力
                probe = as_num(cell(g, r, cols.get("bloom", last_prop_c)))
                if probe is not None and probe > 1e6:
                    break
                rec = {"batch_id": fmt_id(bid) if bid is not None else None}
                used.add((r, id_c))
                for p, pc in cols.items():
                    rec[p] = as_num(cell(g, r, pc))
                    used.add((r, pc))
                # 属性列之后剩下的数字 = 入库编号
                lot = None
                for cc in range(last_prop_c + 1, len(g[r])):
                    v = cell(g, r, cc)
                    if v is None:
                        continue
                    n = as_num(v)
                    if n is not None and n > 1e6:
                        lot = int(round(n))
                        used.add((r, cc))
                        break
                rec["lot_no"] = lot
                rows.append(rec)

            if not rows:
                continue

            key = f"{sheet}#{block_id}@r{hdr_r}c{id_c}"
            tw = sum(r["weight"] for r in rows if r["weight"]) or 0.0
            for i, rec in enumerate(rows):
                comps.append({
                    "sheet": sheet, "blend_key": key, "blend_id": block_id,
                    "target_grade": target, "seq": i + 1,
                    "ratio": (rec["weight"] / tw) if (tw and rec["weight"]) else None,
                    **{k: rec.get(k) for k in ["batch_id"] + NUM_PROPS + ["lot_no"]},
                })

            b = {"sheet": sheet, "blend_key": key, "blend_id": block_id,
                 "target_grade": target, "n_comp": len(rows),
                 "row": hdr_r, "col": id_c,
                 "spec_axis": "so2" if "so2" in cols else ("moisture" if "moisture" in cols else None)}
            for p in NUM_PROPS:
                b[f"stated_{p}"] = stated.get(p)
                if p == "weight":
                    b["calc_weight"] = tw
                else:
                    num = sum(r["weight"] * r[p] for r in rows
                              if r.get(p) is not None and r["weight"])
                    b[f"calc_{p}"] = (num / tw) if (tw and p in cols) else None
            blends.append(b)

        # ---- 5. 未被消费的单元格 (辅助清单/备注) ----
        for r in range(len(g)):
            for c in range(len(g[r])):
                if (r, c) in used:
                    continue
                v = cell(g, r, c)
                if v is None:
                    continue
                leftovers.append({"sheet": sheet, "row": r, "col": c, "value": str(v)})

    comp_schema = {"sheet": pl.Utf8, "blend_key": pl.Utf8, "blend_id": pl.Utf8,
                   "target_grade": pl.Utf8, "seq": pl.Int64, "ratio": pl.Float64,
                   "batch_id": pl.Utf8,
                   **{k: pl.Float64 for k in NUM_PROPS}, "lot_no": pl.Int64}
    blend_schema = {"sheet": pl.Utf8, "blend_key": pl.Utf8, "blend_id": pl.Utf8,
                    "target_grade": pl.Utf8, "n_comp": pl.Int64,
                    "row": pl.Int64, "col": pl.Int64, "spec_axis": pl.Utf8,
                    **{f"{pre}_{p}": pl.Float64 for p in NUM_PROPS for pre in ("stated", "calc")}}
    comps2 = [{k: r.get(k) for k in comp_schema} for r in comps]
    blends2 = [{k: r.get(k) for k in blend_schema} for r in blends]
    return (pl.DataFrame(comps2, schema=comp_schema),
            pl.DataFrame(blends2, schema=blend_schema),
            pl.DataFrame(leftovers, schema={"sheet": pl.Utf8, "row": pl.Int64,
                                            "col": pl.Int64, "value": pl.Utf8}))


if __name__ == "__main__":
    comp, blend, left = parse()
    comp.write_parquet("blend_components_2025.parquet")
    blend.write_parquet("blend_orders_2025.parquet")
    print("components:", comp.shape, "| blends:", blend.shape, "| leftover cells:", left.shape)
    left.write_parquet("_leftover_cells.parquet")
